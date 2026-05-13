"""KiCad Action Plugin bridge for launching KiCad2Fritzing from PCB Editor."""

from __future__ import annotations

import copy
import json
import os
import re
from xml.etree import ElementTree as ET
from pathlib import Path

from kicad2fritzing.core.extractor import export_board_to_fritzing_stub

try:
    import pcbnew  # type: ignore
    import wx  # type: ignore
except ImportError:  # pragma: no cover
    pcbnew = None
    wx = None


SVG_NS = "http://www.w3.org/2000/svg"
NUMERIC_PREFIX_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _parse_svg_number(value: str | None) -> float | None:
    if not value:
        return None
    match = NUMERIC_PREFIX_RE.search(value)
    if not match:
        return None
    return float(match.group(0))


def _svg_canvas_bounds(svg_root: ET.Element) -> tuple[float, float, float, float] | None:
    view_box = svg_root.attrib.get("viewBox")
    if view_box:
        parts = [p for p in re.split(r"[\s,]+", view_box.strip()) if p]
        if len(parts) == 4:
            min_x, min_y, width, height = [float(p) for p in parts]
            if width > 0 and height > 0:
                return (min_x, min_y, width, height)

    width = _parse_svg_number(svg_root.attrib.get("width"))
    height = _parse_svg_number(svg_root.attrib.get("height"))
    if width and height and width > 0 and height > 0:
        return (0.0, 0.0, width, height)

    return None


def _board_outline_bounds(svg_root: ET.Element) -> tuple[float, float, float, float] | None:
    board_outline = None
    for elem in svg_root.iter():
        if elem.attrib.get("id") == "boardOutline":
            board_outline = elem
            break

    if board_outline is None:
        return None

    points_attr = board_outline.attrib.get("points", "")
    points: list[tuple[float, float]] = []
    for pair in points_attr.strip().split():
        if "," not in pair:
            continue
        x_str, y_str = pair.split(",", 1)
        points.append((float(x_str), float(y_str)))

    if not points:
        return None

    min_x = min(x for x, _ in points)
    max_x = max(x for x, _ in points)
    min_y = min(y for _, y in points)
    max_y = max(y for _, y in points)
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def _remove_custom_silkscreen_elements(svg_root: ET.Element) -> None:
    """Remove custom generated silkscreen primitives from breadboard SVG.

    Current custom silkscreen uses a light foreground color. When KiCad-native
    silkscreen is enabled we remove these primitives to avoid duplicate text/
    line overlays.
    """

    def should_remove(elem: ET.Element) -> bool:
        stroke = elem.attrib.get("stroke", "").lower()
        fill = elem.attrib.get("fill", "").lower()
        return stroke == "#f5f5f5" or fill == "#f5f5f5"

    def recurse(parent: ET.Element) -> None:
        for child in list(parent):
            recurse(child)
            if should_remove(child):
                parent.remove(child)

    recurse(svg_root)


def overlay_kicad_plots_on_breadboard(
    out_dir: Path,
    plotted: dict[str, Path],
    replace_custom_silkscreen: bool = True,
) -> Path | None:
    """Overlay KiCad-plotted SVG content on generated breadboard SVG.

    This supports KiCad-native silkscreen as the default path while retaining
    fallback behavior to custom rendering when disabled.
    """
    breadboard_svg_path = out_dir / "breadboard.svg"
    if not breadboard_svg_path.exists():
        return None

    overlay_sources = [
        plotted.get("edge_cuts"),
        plotted.get("f_silks"),
    ]
    overlay_sources = [p for p in overlay_sources if p and p.exists()]
    if not overlay_sources:
        return None

    ET.register_namespace("", SVG_NS)
    target_tree = ET.parse(breadboard_svg_path)
    target_root = target_tree.getroot()

    board_bounds = _board_outline_bounds(target_root)
    if board_bounds is None:
        return None

    anchor_tree = ET.parse(overlay_sources[0])
    anchor_bounds = _svg_canvas_bounds(anchor_tree.getroot())
    if anchor_bounds is None:
        return None

    target_x, target_y, target_w, target_h = board_bounds
    source_x, source_y, source_w, source_h = anchor_bounds
    if source_w <= 0 or source_h <= 0:
        return None

    scale = min(target_w / source_w, target_h / source_h)
    tx = target_x - (source_x * scale) + ((target_w - (source_w * scale)) / 2.0)
    ty = target_y - (source_y * scale) + ((target_h - (source_h * scale)) / 2.0)

    for elem in list(target_root):
        if elem.attrib.get("id") == "kicadNativeOverlay":
            target_root.remove(elem)

    if replace_custom_silkscreen:
        _remove_custom_silkscreen_elements(target_root)

    overlay_group = ET.Element(
        f"{{{SVG_NS}}}g",
        {
            "id": "kicadNativeOverlay",
            "transform": f"translate({tx:.3f},{ty:.3f}) scale({scale:.6f})",
            "opacity": "0.95",
        },
    )

    for source_path in overlay_sources:
        source_tree = ET.parse(source_path)
        source_root = source_tree.getroot()
        subgroup = ET.SubElement(
            overlay_group,
            f"{{{SVG_NS}}}g",
            {"id": f"kicad_{source_path.stem}"},
        )
        for child in list(source_root):
            if child.tag.endswith("metadata") or child.tag.endswith("title"):
                continue
            subgroup.append(copy.deepcopy(child))

    target_root.append(overlay_group)
    target_tree.write(breadboard_svg_path, encoding="utf-8", xml_declaration=False)
    return breadboard_svg_path


def plot_kicad_svg_layers(board, out_dir: Path) -> dict[str, Path]:
    """Plot KiCad-native SVG layers for comparison and future integration.

    This spike helper exports KiCad's own SVG for selected layers so we can
    evaluate fidelity against the custom renderer.
    """
    if pcbnew is None:
        return {}

    out_dir.mkdir(parents=True, exist_ok=True)
    plot_ctrl = pcbnew.PLOT_CONTROLLER(board)
    plot_opts = plot_ctrl.GetPlotOptions()

    plot_opts.SetOutputDirectory(str(out_dir))
    plot_opts.SetPlotFrameRef(False)
    plot_opts.SetMirror(False)
    plot_opts.SetNegative(False)
    plot_opts.SetUseAuxOrigin(False)
    plot_opts.SetFormat(pcbnew.PLOT_FORMAT_SVG)

    if hasattr(plot_opts, "SetSvgPrecision"):
        # KiCad Python API signature varies by version.
        # Some builds accept (precision, useInch), others only (precision).
        try:
            plot_opts.SetSvgPrecision(4, False)
        except TypeError:
            plot_opts.SetSvgPrecision(4)
    if hasattr(plot_opts, "SetTextMode") and hasattr(pcbnew, "PLOT_TEXT_MODE_DEFAULT"):
        plot_opts.SetTextMode(pcbnew.PLOT_TEXT_MODE_DEFAULT)

    plotted: dict[str, Path] = {}
    target_layers = (
        ("f_silks", pcbnew.F_SilkS, "Front Silkscreen"),
        ("edge_cuts", pcbnew.Edge_Cuts, "Board Outline"),
    )

    for key, layer_id, description in target_layers:
        plot_ctrl.SetLayer(layer_id)
        if not plot_ctrl.OpenPlotfile(key, pcbnew.PLOT_FORMAT_SVG, description):
            continue
        if plot_ctrl.PlotLayer():
            plotted[key] = Path(str(plot_ctrl.GetPlotFileName()))

    plot_ctrl.ClosePlot()
    return plotted


def write_overlay_mode_marker(
    out_dir: Path,
    requested_native_overlay: bool,
    applied_native_overlay: bool,
) -> Path:
    """Write a small marker file describing how silkscreen overlay was produced."""
    marker_path = out_dir / "k2f_overlay_mode.json"
    marker_payload = {
        "requested_native_overlay": requested_native_overlay,
        "applied_native_overlay": applied_native_overlay,
        "effective_mode": "kicad_native" if applied_native_overlay else "custom_fallback",
    }
    marker_path.write_text(json.dumps(marker_payload, indent=2), encoding="utf-8")
    return marker_path


class KiCad2FritzingDialog(wx.Dialog if wx else object):  # type: ignore
    """Dialog for KiCad2Fritzing part generation settings."""

    def __init__(self, parent, board_path: Path) -> None:
        """Initialize dialog with default values from board path."""
        if wx is None:
            raise RuntimeError("wxPython not available")
        
        wx.Dialog.__init__(self, parent, title="KiCad2Fritzing Part Generation", size=(620, 360))
        self.board_path = board_path
        
        # Panel setup
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Part Name
        part_label = wx.StaticText(panel, label="Part Name:")
        self.part_name_input = wx.TextCtrl(
            panel,
            value=board_path.stem,
            size=(400, -1)
        )
        part_sizer = wx.BoxSizer(wx.HORIZONTAL)
        part_sizer.Add(part_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        part_sizer.Add(self.part_name_input, 1, wx.EXPAND)
        sizer.Add(part_sizer, 0, wx.ALL | wx.EXPAND, 10)
        
        # Directory
        dir_label = wx.StaticText(panel, label="Output Directory:")
        self.dir_input = wx.TextCtrl(
            panel,
            value=str(board_path.parent / "fritzing-part"),
            size=(350, -1)
        )
        self.browse_btn = wx.Button(panel, label="Browse", size=(80, -1))
        self.browse_btn.Bind(wx.EVT_BUTTON, self._on_browse)
        dir_sizer = wx.BoxSizer(wx.HORIZONTAL)
        dir_sizer.Add(dir_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        dir_sizer.Add(self.dir_input, 1, wx.EXPAND | wx.RIGHT, 5)
        dir_sizer.Add(self.browse_btn, 0)
        sizer.Add(dir_sizer, 0, wx.ALL | wx.EXPAND, 10)
        
        # Text Scaling
        scale_label = wx.StaticText(panel, label="Text Scaling:")
        self.scale_input = wx.TextCtrl(panel, value="1.15", size=(400, -1))
        scale_sizer = wx.BoxSizer(wx.HORIZONTAL)
        scale_sizer.Add(scale_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        scale_sizer.Add(self.scale_input, 1, wx.EXPAND)
        sizer.Add(scale_sizer, 0, wx.ALL | wx.EXPAND, 10)

        # KiCad-native silkscreen toggle (default on for alpha/beta diagnostics)
        self.use_kicad_native_overlay = wx.CheckBox(
            panel,
            label="Use KiCad-native silkscreen overlay (recommended)",
        )
        self.use_kicad_native_overlay.SetValue(True)
        sizer.Add(self.use_kicad_native_overlay, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        
        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        generate_btn = wx.Button(panel, wx.ID_OK, "Generate")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        btn_sizer.Add(generate_btn, 0, wx.RIGHT, 10)
        btn_sizer.Add(cancel_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        
        panel.SetSizer(sizer)
    
    def _on_browse(self, event) -> None:
        """Handle browse button click."""
        dlg = wx.DirDialog(
            self,
            "Choose output directory",
            defaultPath=self.dir_input.GetValue(),
            style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.dir_input.SetValue(dlg.GetPath())
        dlg.Destroy()
    
    def get_values(self) -> tuple[str, Path, float, bool]:
        """Return (part_name, out_dir, text_scale, use_kicad_native_overlay)."""
        part_name = self.part_name_input.GetValue()
        out_dir = Path(self.dir_input.GetValue())
        try:
            text_scale = float(self.scale_input.GetValue())
        except ValueError:
            text_scale = 1.15
        use_kicad_native_overlay = bool(self.use_kicad_native_overlay.GetValue())
        return part_name, out_dir, text_scale, use_kicad_native_overlay


class KiCad2FritzingActionPlugin(pcbnew.ActionPlugin if pcbnew else object):
    """Action plugin wrapper for KiCad's PCB Editor."""

    def defaults(self) -> None:
        self.name = "KiCad2Fritzing"
        self.category = "Export"
        self.description = "Export current KiCad board into Fritzing starter assets"
        self.show_toolbar_button = True

    def Run(self) -> None:  # noqa: N802 - KiCad API uses Run
        board = pcbnew.GetBoard()
        board_path = Path(board.GetFileName())
        
        # Show dialog to collect user settings
        if wx is None:
            # Fallback if wxPython unavailable
            out_dir = board_path.parent / "fritzing-part"
            export_board_to_fritzing_stub(board_path, out_dir)
            write_overlay_mode_marker(
                out_dir,
                requested_native_overlay=False,
                applied_native_overlay=False,
            )
            return
        
        dlg = KiCad2FritzingDialog(None, board_path)
        if dlg.ShowModal() == wx.ID_OK:
            part_name, out_dir, text_scale, use_kicad_native_overlay = dlg.get_values()
            out_dir.mkdir(parents=True, exist_ok=True)
            
            # Set text scaling as environment variable
            previous_text_scale = os.environ.get("K2F_SILK_TEXT_SCALE")
            os.environ["K2F_SILK_TEXT_SCALE"] = str(text_scale)
            
            try:
                export_board_to_fritzing_stub(board_path, out_dir, part_name=part_name)

                native_overlay_applied = False

                if use_kicad_native_overlay:
                    plotted = plot_kicad_svg_layers(board, out_dir / "kicad_svg_plots")
                    overlaid_path = overlay_kicad_plots_on_breadboard(
                        out_dir,
                        plotted,
                        replace_custom_silkscreen=True,
                    )
                    native_overlay_applied = overlaid_path is not None

                write_overlay_mode_marker(
                    out_dir,
                    requested_native_overlay=use_kicad_native_overlay,
                    applied_native_overlay=native_overlay_applied,
                )
            finally:
                # Restore previous text scaling environment state.
                if previous_text_scale is None:
                    os.environ.pop("K2F_SILK_TEXT_SCALE", None)
                else:
                    os.environ["K2F_SILK_TEXT_SCALE"] = previous_text_scale
        
        dlg.Destroy()


def register_plugin() -> bool:
    """Register with KiCad if pcbnew runtime is available."""
    if pcbnew is None:
        return False

    KiCad2FritzingActionPlugin().register()
    return True
