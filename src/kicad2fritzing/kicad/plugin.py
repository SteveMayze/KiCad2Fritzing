"""KiCad Action Plugin bridge for launching KiCad to Fritzing from PCB Editor."""

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
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


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


def _normalize_hex_color(value: str, fallback: str) -> str:
    text = value.strip()
    if HEX_COLOR_RE.fullmatch(text):
        return text.lower()
    return fallback


class KiCad2FritzingDialog(wx.Dialog if wx else object):  # type: ignore
    """Dialog for KiCad to Fritzing part generation settings."""

    def __init__(self, parent, board_path: Path) -> None:
        """Initialize dialog with default values from board path."""
        if wx is None:
            raise RuntimeError("wxPython not available")
        
        wx.Dialog.__init__(self, parent, title="KiCad to Fritzing Part Generation", size=(720, 430))
        self.board_path = board_path
        self.project_dir = board_path.parent.resolve()
        
        # Panel setup
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Directory
        dir_label = wx.StaticText(panel, label="Output Directory:")
        self.dir_input = wx.TextCtrl(
            panel,
            value=str(board_path.parent / "fritzing-part"),
            size=(350, -1)
        )
        self.browse_btn = wx.Button(panel, label="Browse", size=(80, -1))
        self.open_dir_btn = wx.Button(panel, label="Open", size=(80, -1))
        self.browse_btn.Bind(wx.EVT_BUTTON, self._on_browse)
        self.open_dir_btn.Bind(wx.EVT_BUTTON, self._on_open_output_dir)
        dir_sizer = wx.BoxSizer(wx.HORIZONTAL)
        dir_sizer.Add(dir_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        dir_sizer.Add(self.dir_input, 1, wx.EXPAND | wx.RIGHT, 5)
        dir_sizer.Add(self.browse_btn, 0, wx.RIGHT, 5)
        dir_sizer.Add(self.open_dir_btn, 0)
        sizer.Add(dir_sizer, 0, wx.ALL | wx.EXPAND, 10)

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

        # Render colors
        color_sizer = wx.BoxSizer(wx.HORIZONTAL)
        soldermask_label = wx.StaticText(panel, label="Soldermask color:")
        self.soldermask_color_input = wx.ColourPickerCtrl(panel, colour=wx.Colour("#2b5f82"))
        self.silkscreen_color_label = wx.StaticText(panel, label="Silkscreen color:")
        self.silkscreen_color_input = wx.ColourPickerCtrl(panel, colour=wx.Colour("#f5f5f5"))
        color_sizer.Add(soldermask_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        color_sizer.Add(self.soldermask_color_input, 0, wx.RIGHT, 22)
        color_sizer.Add(self.silkscreen_color_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        color_sizer.Add(self.silkscreen_color_input, 0)
        sizer.Add(color_sizer, 0, wx.ALL, 10)

        # Pad/Pin scaling
        pad_scale_label = wx.StaticText(panel, label="Pad/Pin Scaling:")
        self.pad_scale_input = wx.TextCtrl(panel, value="1.0", size=(120, -1))
        pad_scale_sizer = wx.BoxSizer(wx.HORIZONTAL)
        pad_scale_sizer.Add(pad_scale_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        pad_scale_sizer.Add(self.pad_scale_input, 0)
        sizer.Add(pad_scale_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        
        # KiCad-native silkscreen toggle (default on for alpha/beta diagnostics)
        self.use_kicad_native_overlay = wx.CheckBox(
            panel,
            label="Use KiCad-native silkscreen overlay (recommended)",
        )
        self.use_kicad_native_overlay.SetValue(True)
        self.use_kicad_native_overlay.Bind(wx.EVT_CHECKBOX, self._on_native_overlay_toggle)
        sizer.Add(self.use_kicad_native_overlay, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Text Scaling
        scale_label = wx.StaticText(panel, label="Text scaling (custom renderer only):")
        self.scale_label = scale_label
        self.scale_input = wx.TextCtrl(panel, value="1.15", size=(400, -1))
        scale_sizer = wx.BoxSizer(wx.HORIZONTAL)
        scale_sizer.Add(scale_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        scale_sizer.Add(self.scale_input, 1, wx.EXPAND)
        sizer.Add(scale_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.custom_options_hint = wx.StaticText(
            panel,
            label="Silkscreen color and text scaling apply only to custom silkscreen rendering.",
        )
        sizer.Add(self.custom_options_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Native overlay bypasses custom text scaling; disable to avoid confusion.
        self._sync_text_scaling_controls()
        
        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        close_btn = wx.Button(panel, wx.ID_CANCEL, "Close")
        generate_btn = wx.Button(panel, wx.ID_OK, "Generate")
        btn_sizer.Add(close_btn, 0, wx.RIGHT, 10)
        btn_sizer.Add(generate_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        
        panel.SetSizer(sizer)
    
    def _on_browse(self, event) -> None:
        """Handle browse button click."""
        current_path = self._resolve_output_dir(self.dir_input.GetValue())
        default_path = str(current_path if current_path.exists() else self.project_dir)
        dlg = wx.DirDialog(
            self,
            "Choose output directory",
            defaultPath=default_path,
            style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST
        )
        if dlg.ShowModal() == wx.ID_OK:
            chosen = Path(dlg.GetPath()).resolve()
            if self._is_within_project_dir(chosen):
                relative_path = os.path.relpath(chosen, self.project_dir)
                prompt = wx.MessageDialog(
                    self,
                    "Selected directory is inside this KiCad project. Store it as a relative path?",
                    "Store Relative Path",
                    wx.YES_NO | wx.ICON_QUESTION,
                )
                if prompt.ShowModal() == wx.ID_YES:
                    self.dir_input.SetValue(relative_path)
                else:
                    self.dir_input.SetValue(str(chosen))
                prompt.Destroy()
            else:
                self.dir_input.SetValue(str(chosen))
        dlg.Destroy()

    def _is_within_project_dir(self, path: Path) -> bool:
        """Return True when path is inside the current project directory."""
        try:
            path.relative_to(self.project_dir)
            return True
        except ValueError:
            return False

    def _resolve_output_dir(self, raw_path: str) -> Path:
        """Resolve user-entered output path, supporting project-relative notation."""
        path = Path(raw_path).expanduser()
        if path.is_absolute():
            return path
        return (self.project_dir / path).resolve()

    def _on_open_output_dir(self, event) -> None:
        """Open current output directory in the system file manager."""
        out_dir = self._resolve_output_dir(self.dir_input.GetValue())
        if not out_dir.exists():
            prompt = wx.MessageDialog(
                self,
                f"Create and open this directory?\n{out_dir}",
                "Create Output Directory",
                wx.YES_NO | wx.ICON_QUESTION,
            )
            should_create = prompt.ShowModal() == wx.ID_YES
            prompt.Destroy()
            if not should_create:
                return
            out_dir.mkdir(parents=True, exist_ok=True)

        if not wx.LaunchDefaultApplication(str(out_dir)):
            wx.MessageBox(
                f"Unable to open directory:\n{out_dir}",
                "Open Directory Failed",
                wx.OK | wx.ICON_ERROR,
            )

    def _sync_text_scaling_controls(self) -> None:
        """Enable custom silkscreen controls only when native overlay is disabled."""
        enable_custom_controls = not bool(self.use_kicad_native_overlay.GetValue())
        self.scale_input.Enable(enable_custom_controls)
        self.scale_label.Enable(enable_custom_controls)
        self.silkscreen_color_input.Enable(enable_custom_controls)
        self.silkscreen_color_label.Enable(enable_custom_controls)

    def _on_native_overlay_toggle(self, event) -> None:
        """Update control state when native-overlay checkbox changes."""
        self._sync_text_scaling_controls()
        event.Skip()
    
    def get_values(self) -> tuple[str, Path, float, float, str, str, bool]:
        """Return dialog values including rendering options."""
        part_name = self.part_name_input.GetValue()
        out_dir = self._resolve_output_dir(self.dir_input.GetValue())

        try:
            text_scale = float(self.scale_input.GetValue())
        except ValueError:
            text_scale = 1.15

        try:
            pad_scale = float(self.pad_scale_input.GetValue())
        except ValueError:
            pad_scale = 1.0

        pad_scale = max(0.2, min(3.0, pad_scale))

        soldermask_color = _normalize_hex_color(self.soldermask_color_input.GetColour().GetAsString(wx.C2S_HTML_SYNTAX), "#2b5f82")
        silkscreen_color = _normalize_hex_color(self.silkscreen_color_input.GetColour().GetAsString(wx.C2S_HTML_SYNTAX), "#f5f5f5")

        use_kicad_native_overlay = bool(self.use_kicad_native_overlay.GetValue())
        return (
            part_name,
            out_dir,
            text_scale,
            pad_scale,
            soldermask_color,
            silkscreen_color,
            use_kicad_native_overlay,
        )


class KiCad2FritzingActionPlugin(pcbnew.ActionPlugin if pcbnew else object):
    """Action plugin wrapper for KiCad's PCB Editor."""

    def defaults(self) -> None:
        self.name = "KiCad to Fritzing"
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
            (
                part_name,
                out_dir,
                text_scale,
                pad_scale,
                soldermask_color,
                silkscreen_color,
                use_kicad_native_overlay,
            ) = dlg.get_values()
            out_dir.mkdir(parents=True, exist_ok=True)

            render_options = {
                "soldermask_color": soldermask_color,
                "silkscreen_color": silkscreen_color,
                "pad_scale": pad_scale,
                "silk_text_scale": text_scale,
            }
            export_board_to_fritzing_stub(
                board_path,
                out_dir,
                part_name=part_name,
                render_options=render_options,
            )

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
        
        dlg.Destroy()


def register_plugin() -> bool:
    """Register with KiCad if pcbnew runtime is available."""
    if pcbnew is None:
        return False

    KiCad2FritzingActionPlugin().register()
    return True
