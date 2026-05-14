"""KiCad Action Plugin bridge for launching KiCad to Fritzing from PCB Editor."""

from __future__ import annotations

import base64
import math
import struct
import zlib
from io import BytesIO
try:
    from PIL import Image, ImageEnhance
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
import copy
import json
import os
import re
import shutil
import subprocess
from xml.etree import ElementTree as ET
from pathlib import Path

from pcb2fritzing.core.extractor import build_fritzing_package_zip, export_board_to_fritzing_stub

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


def _png_image_metrics(
    png_bytes: bytes,
    alpha_threshold: int = 96,
) -> tuple[int, int, tuple[int, int, int, int] | None] | None:
    """Return (width, height, alpha_bounds) for an RGBA/GA PNG.

    alpha_bounds is (min_x, min_y, max_x_exclusive, max_y_exclusive) for
    pixels with alpha >= alpha_threshold. Returns None when PNG parsing fails.
    """
    signature = b"\x89PNG\r\n\x1a\n"
    if len(png_bytes) < 8 or png_bytes[:8] != signature:
        return None

    width: int | None = None
    height: int | None = None
    bit_depth: int | None = None
    color_type: int | None = None
    interlace: int | None = None
    idat_parts: list[bytes] = []

    offset = 8
    while offset + 8 <= len(png_bytes):
        length = struct.unpack(">I", png_bytes[offset:offset + 4])[0]
        chunk_type = png_bytes[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(png_bytes):
            return None
        chunk_data = png_bytes[data_start:data_end]

        if chunk_type == b"IHDR":
            if length < 13:
                return None
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB",
                chunk_data[:13],
            )
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            break

        offset = crc_end

    if (
        width is None
        or height is None
        or bit_depth is None
        or color_type is None
        or interlace is None
    ):
        return None

    if width <= 0 or height <= 0:
        return None

    # Return dimensions even for unsupported formats.
    if interlace != 0 or color_type not in (4, 6) or bit_depth not in (8, 16):
        return (width, height, None)

    channels = 2 if color_type == 4 else 4
    bytes_per_sample = 2 if bit_depth == 16 else 1
    bytes_per_pixel = channels * bytes_per_sample
    scanline_len = math.ceil(width * channels * bit_depth / 8)

    try:
        raw = zlib.decompress(b"".join(idat_parts))
    except zlib.error:
        return (width, height, None)

    expected_len = (scanline_len + 1) * height
    if len(raw) < expected_len:
        return (width, height, None)

    min_x = width
    min_y = height
    max_x = -1
    max_y = -1

    prev = bytearray(scanline_len)
    for y in range(height):
        base = y * (scanline_len + 1)
        filter_type = raw[base]
        row = bytearray(raw[base + 1: base + 1 + scanline_len])

        if filter_type == 1:  # Sub
            for i in range(scanline_len):
                left = row[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                row[i] = (row[i] + left) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(scanline_len):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(scanline_len):
                left = row[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                up = prev[i]
                row[i] = (row[i] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(scanline_len):
                left = row[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                up = prev[i]
                up_left = prev[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                p = left + up - up_left
                pa = abs(p - left)
                pb = abs(p - up)
                pc = abs(p - up_left)
                predictor = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                row[i] = (row[i] + predictor) & 0xFF
        elif filter_type != 0:  # None
            return (width, height, None)

        alpha_offset = bytes_per_sample if color_type == 4 else (3 * bytes_per_sample)
        stride = bytes_per_pixel
        for x in range(width):
            pixel_idx = x * stride + alpha_offset
            if bit_depth == 16:
                alpha = (row[pixel_idx] << 8) | row[pixel_idx + 1]
                alpha = alpha >> 8
            else:
                alpha = row[pixel_idx]

            if alpha >= alpha_threshold:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

        prev = row

    if max_x < min_x or max_y < min_y:
        return (width, height, None)

    return (width, height, (min_x, min_y, max_x + 1, max_y + 1))


def _remove_custom_silkscreen_elements(svg_root: ET.Element, silkscreen_color: str = "#f5f5f5") -> None:
    """Remove custom generated silkscreen primitives from breadboard SVG.

    Current custom silkscreen uses a light foreground color. When KiCad-native
    silkscreen is enabled we remove these primitives to avoid duplicate text/
    line overlays.
    """
    color_lower = silkscreen_color.lower()

    def should_remove(elem: ET.Element) -> bool:
        stroke = elem.attrib.get("stroke", "").lower()
        fill = elem.attrib.get("fill", "").lower()
        return stroke == color_lower or fill == color_lower

    def recurse(parent: ET.Element) -> None:
        for child in list(parent):
            recurse(child)
            if should_remove(child):
                parent.remove(child)

    recurse(svg_root)


def strip_silkscreen_overlays_for_3d(
    out_dir: Path,
    silkscreen_color: str = "#f5f5f5",
) -> bool:
    """Remove 2D silkscreen overlays from breadboard SVG before 3D embedding.

    In photorealistic mode, silkscreen is already present in the 3D render.
    Keeping 2D overlays causes ghosting/double text.
    """
    breadboard_svg_path = out_dir / "breadboard.svg"
    if not breadboard_svg_path.exists():
        return False

    ET.register_namespace("", SVG_NS)
    tree = ET.parse(breadboard_svg_path)
    root = tree.getroot()

    # Remove any prior KiCad-native overlay group if present.
    for elem in list(root):
        if elem.attrib.get("id") == "kicadNativeOverlay":
            root.remove(elem)

    _remove_custom_silkscreen_elements(root, silkscreen_color)
    tree.write(breadboard_svg_path, encoding="utf-8", xml_declaration=False)
    return True


def overlay_kicad_plots_on_breadboard(
    out_dir: Path,
    plotted: dict[str, Path],
    replace_custom_silkscreen: bool = True,
    silkscreen_color: str = "#f5f5f5",
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
        plotted.get("f_fab"),
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
        _remove_custom_silkscreen_elements(target_root, silkscreen_color)

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


def plot_kicad_svg_layers(
    board, out_dir: Path, include_fab_layer: bool = False
) -> dict[str, Path]:
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
    target_layers = [
        ("f_silks", pcbnew.F_SilkS, "Front Silkscreen"),
        ("edge_cuts", pcbnew.Edge_Cuts, "Board Outline"),
    ]
    if include_fab_layer:
        target_layers.append(("f_fab", pcbnew.F_Fab, "Front Fabrication"))

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


def _find_sexp_block(content: str, marker: str, search_start: int = 0) -> "tuple[int, int] | None":
    """Return (start, end) character positions of an S-expr block beginning with *marker*.

    *end* is exclusive (points one past the closing parenthesis).
    Returns None if the marker is not found or the block is unclosed.
    """
    pos = content.find(marker, search_start)
    if pos == -1:
        return None
    depth = 0
    i = pos
    while i < len(content):
        ch = content[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return (pos, i + 1)
        i += 1
    return None


def _find_sexp_block_at(content: str, start_pos: int) -> "tuple[int, int] | None":
    """Return (start, end) of an S-expr whose opening '(' is at *start_pos*."""
    if start_pos < 0 or start_pos >= len(content) or content[start_pos] != "(":
        return None
    depth = 0
    i = start_pos
    while i < len(content):
        ch = content[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return (start_pos, i + 1)
        i += 1
    return None


def _patch_stackup_layer_color(content: str, layer_name: str, color: str) -> str:
    """Within the (stackup ...) block, set the (color ...) entry of a named layer."""
    stackup_range = _find_sexp_block(content, "(stackup")
    if stackup_range is None:
        return content
    stackup_start, stackup_end = stackup_range
    stackup = content[stackup_start:stackup_end]

    # Support both `(layer "F.Mask" ...)` and `(layer F.Mask ...)` styles.
    layer_re = re.compile(rf'\(layer\s+"?{re.escape(layer_name)}"?(?=[\s\)])')
    layer_match = layer_re.search(stackup)
    if layer_match is None:
        return content
    layer_range = _find_sexp_block_at(stackup, layer_match.start())
    if layer_range is None:
        return content
    layer_start, layer_end = layer_range
    block = stackup[layer_start:layer_end]

    color_re = re.compile(r'\(color\s+(?:"[^"]*"|#[0-9A-Fa-f]{6}|[^\s\)]+)\)')
    if color_re.search(block):
        new_block = color_re.sub(f'(color "{color}")', block)
    else:
        # Insert (color "...") before the layer block closing parenthesis.
        insert_pos = len(block) - 1
        while insert_pos > 0 and block[insert_pos - 1] in " \t\n\r":
            insert_pos -= 1
        new_block = block[:insert_pos] + f'\n\t\t\t\t(color "{color}")' + block[insert_pos:]

    new_stackup = stackup[:layer_start] + new_block + stackup[layer_end:]
    return content[:stackup_start] + new_stackup + content[stackup_end:]


def _create_temp_board_for_render(
    board_file: Path,
    soldermask_color: str,
    silkscreen_color: str,
) -> "Path | None":
    """Return a temporary .kicad_pcb with custom stackup colours, or None on failure.

    The caller is responsible for deleting the returned file after use.
    """
    try:
        content = board_file.read_text(encoding="utf-8")
    except OSError:
        return None

    if "(stackup" not in content:
        # Inject a minimal stackup block inside (setup ...) so KiCad renders
        # with the requested board colours.
        stackup_block = (
            "\t\t(stackup\n"
            f'\t\t\t(layer "F.SilkS"\n'
            f'\t\t\t\t(type "Top Silk Screen")\n'
            f'\t\t\t\t(color "{silkscreen_color}"))\n'
            f'\t\t\t(layer "B.SilkS"\n'
            f'\t\t\t\t(type "Bottom Silk Screen")\n'
            f'\t\t\t\t(color "{silkscreen_color}"))\n'
            f'\t\t\t(layer "F.Paste"\n'
            f'\t\t\t\t(type "Top Solder Paste"))\n'
            f'\t\t\t(layer "B.Paste"\n'
            f'\t\t\t\t(type "Bottom Solder Paste"))\n'
            f'\t\t\t(layer "F.Mask"\n'
            f'\t\t\t\t(type "Top Solder Mask")\n'
            f'\t\t\t\t(color "{soldermask_color}"))\n'
            f'\t\t\t(layer "B.Mask"\n'
            f'\t\t\t\t(type "Bottom Solder Mask")\n'
            f'\t\t\t\t(color "{soldermask_color}"))\n'
            "\t\t)\n"
        )
        setup_range = _find_sexp_block(content, "(setup")
        if setup_range is not None:
            s_start, s_end = setup_range
            setup_block = content[s_start:s_end]
            nl_pos = setup_block.find("\n")
            if nl_pos != -1:
                new_setup = setup_block[: nl_pos + 1] + stackup_block + setup_block[nl_pos + 1:]
            else:
                new_setup = setup_block[:-1] + "\n" + stackup_block + ")"
            content = content[:s_start] + new_setup + content[s_end:]
    else:
        content = _patch_stackup_layer_color(content, "F.Mask", soldermask_color)
        content = _patch_stackup_layer_color(content, "B.Mask", soldermask_color)
        content = _patch_stackup_layer_color(content, "F.SilkS", silkscreen_color)
        content = _patch_stackup_layer_color(content, "B.SilkS", silkscreen_color)

    tmp_path = board_file.parent / f"_k2f_tmp_{board_file.stem}.kicad_pcb"
    try:
        tmp_path.write_text(content, encoding="utf-8")
    except OSError:
        return None
    return tmp_path


def _find_kicad_cli(override: str | None) -> str | None:
    """Locate the kicad-cli executable, returning its path or None."""
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        return None
    candidates = [
        "/Applications/KiCad-10.01/KiCad.app/Contents/MacOS/kicad-cli",
        "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    return shutil.which("kicad-cli")


def render_board_3d(
    board_file: Path,
    out_dir: Path,
    board_bounds_mm: dict | None = None,
    kicad_cli_path: str | None = None,
    soldermask_color: str | None = None,
    silkscreen_color: str | None = None,
    diagnostics: list[str] | None = None,
) -> "Path | None":
    """Render the board top-down using kicad-cli and return the PNG path.

    When *soldermask_color* or *silkscreen_color* are provided the board's
    stackup colours are patched in a temporary copy of the board file before
    rendering so that the 3D image matches the colours chosen in the dialog.
    The temporary file is deleted after the render completes regardless of
    whether the render succeeds.

    Uses orthogonal (non-perspective) projection so component positions map
    exactly onto their XY footprint coordinates regardless of part height.
    Returns None when kicad-cli is unavailable or the render fails.
    """
    cli = _find_kicad_cli(kicad_cli_path)
    if cli is None:
        return None

    board_file = board_file.resolve()
    render_source = board_file
    tmp_board: "Path | None" = None

    if soldermask_color or silkscreen_color:
        sm = soldermask_color or "#2b5f82"
        sk = silkscreen_color or "#f5f5f5"
        tmp_board = _create_temp_board_for_render(board_file, sm, sk)
        if tmp_board is not None:
            render_source = tmp_board

    out_dir.mkdir(parents=True, exist_ok=True)
    render_path = (out_dir / "board_render.png").resolve()

    # Compute pixel dimensions matching the board aspect ratio so we can
    # later use preserveAspectRatio="none" for a pixel-perfect fit.
    width_px, height_px = 2000, 2000
    if board_bounds_mm:
        bw = board_bounds_mm.get("max_x", 1.0) - board_bounds_mm.get("min_x", 0.0)
        bh = board_bounds_mm.get("max_y", 1.0) - board_bounds_mm.get("min_y", 0.0)
        if bw > 0 and bh > 0:
            if bw >= bh:
                height_px = max(100, round(2000 * bh / bw))
            else:
                width_px = max(100, round(2000 * bw / bh))

    def _diag(msg: str) -> None:
        if diagnostics is not None:
            diagnostics.append(msg)

    def _summarize_output(text: bytes | str | None) -> str:
        if text is None:
            return ""
        if isinstance(text, bytes):
            try:
                value = text.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                value = str(text)
        else:
            value = text
        value = value.strip()
        if not value:
            return ""
        lines = value.splitlines()
        return " | ".join(lines[-3:])

    def _run_render(source_path: Path, label: str) -> bool:
        cmd = [
            cli, "pcb", "render",
            "--side", "top",
            "--background", "transparent",
            "--quality", "high",
            "--width", str(width_px),
            "--height", str(height_px),
            "--output", str(render_path),
            str(source_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=120,
                cwd=str(board_file.parent),
            )
        except subprocess.TimeoutExpired:
            _diag(f"  kicad-cli render ({label}) timed out after 120s.")
            return False
        except Exception as exc:  # noqa: BLE001
            _diag(f"  kicad-cli render ({label}) failed to execute: {exc}")
            return False

        if result.returncode == 0 and render_path.exists():
            return True

        stderr_summary = _summarize_output(result.stderr)
        stdout_summary = _summarize_output(result.stdout)
        _diag(f"  kicad-cli render ({label}) failed (exit {result.returncode}).")
        if stderr_summary:
            _diag(f"    stderr: {stderr_summary}")
        elif stdout_summary:
            _diag(f"    stdout: {stdout_summary}")
        return False

    try:
        # First try the selected source (patched temp board when available), then
        # fall back to the original board if the patched render fails.
        if _run_render(render_source, "patched board" if render_source != board_file else "board"):
            return render_path

        if render_source != board_file:
            _diag("  Retrying 3D render with original board file (no temporary color patch)...")
            if _run_render(board_file, "original board"):
                _diag("  Fallback render succeeded using original board file.")
                return render_path

        return None
    finally:
        if tmp_board is not None:
            try:
                tmp_board.unlink(missing_ok=True)
            except OSError:
                pass


def embed_3d_render_in_breadboard_svg(out_dir: Path, render_path: Path) -> bool:
    """Embed a board render PNG into the breadboard SVG as a base64 data URI.

    Sets the boardOutline fill to none so the render provides the board colour,
    clips the image to the exact board shape, and inserts it behind all overlays
    and connector pins.
    """
    breadboard_svg_path = out_dir / "breadboard.svg"
    if not breadboard_svg_path.exists() or not render_path.exists():
        return False

    ET.register_namespace("", SVG_NS)
    tree = ET.parse(breadboard_svg_path)
    root = tree.getroot()

    board_bounds = _board_outline_bounds(root)
    if board_bounds is None:
        return False
    bx, by, bw, bh = board_bounds

    # Locate boardOutline, strip its fill, capture its polygon points.
    board_outline_points = ""
    board_outline_idx: int | None = None
    for i, elem in enumerate(root):
        if elem.attrib.get("id") == "boardOutline":
            elem.attrib["fill"] = "none"
            board_outline_points = elem.attrib.get("points", "")
            board_outline_idx = i
            break

    # Remove any previously embedded render group.
    for elem in list(root):
        if elem.attrib.get("id") == "render3d":
            root.remove(elem)

    # Ensure <defs> exists at position 0.
    defs = root.find(f"{{{SVG_NS}}}defs")
    if defs is None:
        defs = ET.Element(f"{{{SVG_NS}}}defs")
        root.insert(0, defs)
        if board_outline_idx is not None:
            board_outline_idx += 1

    # (Re-)create the boardClip clipPath.
    for elem in list(defs):
        if elem.attrib.get("id") == "boardClip":
            defs.remove(elem)
    if board_outline_points:
        clip_path = ET.SubElement(defs, f"{{{SVG_NS}}}clipPath", {"id": "boardClip"})
        ET.SubElement(clip_path, f"{{{SVG_NS}}}polygon", {"points": board_outline_points})

    # Build the render group with an embedded PNG image.
    group_attrs: dict[str, str] = {"id": "render3d"}
    if board_outline_points:
        group_attrs["clip-path"] = "url(#boardClip)"
    render_group = ET.Element(f"{{{SVG_NS}}}g", group_attrs)
    # --- Soft shadow suppression: post-process PNG to reduce shadow opacity/contrast ---
    if _PIL_AVAILABLE:
        try:
            with Image.open(render_path) as im:
                im = im.convert("RGBA")
                r, g, b, a = im.split()
                shadow_mask = Image.eval(r, lambda px: 255 if px < 64 else 0)
                light = ImageEnhance.Brightness(im).enhance(1.5)
                im = Image.composite(light, im, shadow_mask)
                im = ImageEnhance.Contrast(im).enhance(0.92)
                buf = BytesIO()
                im.save(buf, format="PNG")
                png_bytes = buf.getvalue()
        except Exception:
            png_bytes = render_path.read_bytes()
    else:
        png_bytes = render_path.read_bytes()
    image_x = bx
    image_y = by
    image_w = bw
    image_h = bh

    # kicad-cli high-quality renders can include low-alpha haze/shadow across
    # the full canvas. Detect the meaningful alpha content bounds and remap the
    # image so the board area aligns with boardOutline dimensions.
    # Prefer aggressive thresholds first so soft shadows/haze do not expand the
    # fitted bounds. Fall back progressively for edge cases.
    selected_metrics: tuple[int, int, tuple[int, int, int, int] | None] | None = None
    for threshold in (224, 192, 160, 128, 96):
        metrics = _png_image_metrics(png_bytes, alpha_threshold=threshold)
        if metrics is None:
            continue
        img_w_px, img_h_px, alpha_bounds = metrics
        if alpha_bounds is None:
            continue
        min_x_px, min_y_px, max_x_px, max_y_px = alpha_bounds
        content_w_px = max(1, max_x_px - min_x_px)
        content_h_px = max(1, max_y_px - min_y_px)
        area_ratio = (content_w_px * content_h_px) / max(1, img_w_px * img_h_px)

        selected_metrics = metrics
        # Accept the first threshold that trims at least a little border.
        if area_ratio < 0.995:
            break

    if selected_metrics is not None:
        img_w_px, img_h_px, alpha_bounds = selected_metrics
        if alpha_bounds is not None:
            min_x_px, min_y_px, max_x_px, max_y_px = alpha_bounds
            content_w_px = max(1, max_x_px - min_x_px)
            content_h_px = max(1, max_y_px - min_y_px)
            scale_x = bw / content_w_px
            scale_y = bh / content_h_px
            image_x = bx - (min_x_px * scale_x)
            image_y = by - (min_y_px * scale_y)
            image_w = img_w_px * scale_x
            image_h = img_h_px * scale_y

    png_data = base64.b64encode(png_bytes).decode("ascii")
    ET.SubElement(
        render_group,
        f"{{{SVG_NS}}}image",
        {
            "x": str(round(image_x, 3)),
            "y": str(round(image_y, 3)),
            "width": str(round(image_w, 3)),
            "height": str(round(image_h, 3)),
            "href": f"data:image/png;base64,{png_data}",
            # preserveAspectRatio="none" works correctly here because we
            # computed the PNG dimensions to match the board aspect ratio.
            "preserveAspectRatio": "none",
        },
    )

    # Insert the render group immediately after boardOutline so the
    # outline stroke, silkscreen and connector pins all render on top.
    insert_at = (board_outline_idx + 1) if board_outline_idx is not None else 1
    root.insert(insert_at, render_group)

    tree.write(breadboard_svg_path, encoding="utf-8", xml_declaration=False)
    return True


class PCBtoFritzingPartDialog(wx.Dialog if wx else object):  # type: ignore
    """Dialog for PCB to Fritzing Part generation settings."""

    def __init__(self, parent, board_path: Path, board=None) -> None:
        """Initialize dialog with default values from board path."""
        if wx is None:
            raise RuntimeError("wxPython not available")
        
        wx.Dialog.__init__(self, parent, title="PCB to Fritzing Part", size=(720, 750))
        self.board_path = board_path
        self.board = board
        self.project_dir = board_path.parent.resolve()
        
        # Panel setup
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Compute default output dir value: blank if non-existent, relative+sep if inside project
        _default_out = board_path.parent / "fritzing-part"
        if _default_out.exists():
            try:
                _rel = os.path.relpath(_default_out, board_path.parent)
                _dir_default = _rel + os.sep
            except ValueError:
                _dir_default = str(_default_out) + os.sep
        else:
            _dir_default = ""

        # Directory
        dir_label = wx.StaticText(panel, label="Output Directory:")
        self.dir_input = wx.TextCtrl(
            panel,
            value=_dir_default,
            size=(350, -1)
        )
        browse_icon = wx.ArtProvider.GetBitmap(wx.ART_FOLDER_OPEN, wx.ART_BUTTON, (16, 16))
        open_icon = wx.ArtProvider.GetBitmap(wx.ART_FILE_OPEN, wx.ART_BUTTON, (16, 16))

        if browse_icon.IsOk():
            self.browse_btn = wx.BitmapButton(panel, bitmap=browse_icon, size=(34, 30))
        else:
            self.browse_btn = wx.Button(panel, label="Browse", size=(80, -1))

        if open_icon.IsOk():
            self.open_dir_btn = wx.BitmapButton(panel, bitmap=open_icon, size=(34, 30))
        else:
            self.open_dir_btn = wx.Button(panel, label="Open", size=(80, -1))

        self.browse_btn.SetToolTip("Choose output directory")
        self.open_dir_btn.SetToolTip("Open output directory in file manager")
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

        # Fritzing metadata
        metadata_sizer = wx.BoxSizer(wx.HORIZONTAL)
        family_label = wx.StaticText(panel, label="Part Family:")
        self.part_family_input = wx.TextCtrl(
            panel,
            value="KiCad2Fritzing Generated",
            size=(230, -1),
        )
        type_label = wx.StaticText(panel, label="Part Type:")
        self.part_type_input = wx.TextCtrl(
            panel,
            value="Custom PCB",
            size=(190, -1),
        )
        metadata_sizer.Add(family_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        metadata_sizer.Add(self.part_family_input, 1, wx.RIGHT, 16)
        metadata_sizer.Add(type_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        metadata_sizer.Add(self.part_type_input, 1)
        sizer.Add(metadata_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

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

        # Pad/Pin scaling (moved up to after color pickers)
        pad_scale_label = wx.StaticText(panel, label="Pad/Pin Scaling:")
        self.pad_scale_input = wx.TextCtrl(panel, value="0.75", size=(120, -1))
        pad_scale_sizer = wx.BoxSizer(wx.HORIZONTAL)
        pad_scale_sizer.Add(pad_scale_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        pad_scale_sizer.Add(self.pad_scale_input, 0)
        sizer.Add(pad_scale_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Photorealistic 3D render
        self.use_3d_render = wx.CheckBox(
            panel,
            label="Photorealistic 3D render (requires kicad-cli)",
        )
        self.use_3d_render.SetValue(True)
        self.use_3d_render.Bind(wx.EVT_CHECKBOX, self._on_3d_render_toggle)
        sizer.Add(self.use_3d_render, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        kicad_cli_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.kicad_cli_label = wx.StaticText(panel, label="kicad-cli path:")
        self.kicad_cli_path_input = wx.TextCtrl(
            panel, value=_find_kicad_cli(None) or "", size=(320, -1)
        )
        self.kicad_cli_detect_btn = wx.Button(panel, label="Detect", size=(70, -1))
        self.kicad_cli_detect_btn.Bind(wx.EVT_BUTTON, self._on_detect_kicad_cli)
        kicad_cli_sizer.Add(self.kicad_cli_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        kicad_cli_sizer.Add(self.kicad_cli_path_input, 1, wx.RIGHT, 5)
        kicad_cli_sizer.Add(self.kicad_cli_detect_btn, 0)
        sizer.Add(kicad_cli_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        self._sync_3d_render_controls()

        # Advanced 2D silkscreen/body options (moved down to after 3D render)
        self.silkscreen_options_label = wx.StaticText(
            panel,
            label="Advanced 2D overlays (optional):",
        )
        sizer.Add(self.silkscreen_options_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 2)

        # Silkscreen options
        silkscreen_sizer = wx.BoxSizer(wx.VERTICAL)
        self.include_component_silkscreen = wx.CheckBox(
            panel,
            label="Include component footprint outlines and labels (F.SilkS)",
        )
        self.include_component_silkscreen.SetValue(False)
        silkscreen_sizer.Add(self.include_component_silkscreen, 0, wx.BOTTOM, 4)

        self.include_fab_layer = wx.CheckBox(
            panel,
            label="Include component body layer (F.Fab)",
        )
        self.include_fab_layer.SetValue(False)
        silkscreen_sizer.Add(self.include_fab_layer, 0, wx.BOTTOM, 8)

        sizer.Add(silkscreen_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        
        # KiCad-native silkscreen toggle (default on for alpha/beta diagnostics)
        self.use_kicad_native_overlay = wx.CheckBox(
            panel,
            label="Use KiCad-native silkscreen overlay (recommended)",
        )
        self.use_kicad_native_overlay.SetValue(True)
        self.use_kicad_native_overlay.Bind(wx.EVT_CHECKBOX, self._on_native_overlay_toggle)
        sizer.Add(self.use_kicad_native_overlay, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        
        self._sync_advanced_overlay_controls()

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
            label="Text scaling applies only to custom silkscreen rendering.",
        )
        sizer.Add(self.custom_options_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Native overlay bypasses custom text scaling; disable to avoid confusion.
        self._sync_text_scaling_controls()

        # Output messages panel
        output_label = wx.StaticText(panel, label="Output messages:")
        sizer.Add(output_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        
        self.output_messages = wx.TextCtrl(
            panel,
            value="",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
            size=(-1, 120)
        )
        sizer.Add(self.output_messages, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        
        # Buttons with Save... button
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        save_btn = wx.Button(panel, label="Save...")
        save_btn.Bind(wx.EVT_BUTTON, self._on_save_messages)
        close_btn = wx.Button(panel, wx.ID_CANCEL, "Close")
        self.generate_btn = wx.Button(panel, label="Generate")
        self.generate_btn.Bind(wx.EVT_BUTTON, self._on_generate)
        btn_sizer.Add(save_btn, 0, wx.RIGHT, 10)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(close_btn, 0, wx.RIGHT, 10)
        btn_sizer.Add(self.generate_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)
        
        panel.SetSizer(sizer)
    
    def _on_browse(self, event) -> None:
        """Handle browse button click."""
        current_path = self._resolve_output_dir(self.dir_input.GetValue())
        default_path = str(current_path if current_path.exists() else self.project_dir)
        dlg = wx.DirDialog(
            self,
            "Choose output directory",
            defaultPath=default_path,
            style=wx.DD_DEFAULT_STYLE | wx.DD_NEW_DIR_BUTTON,
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
                    self.dir_input.SetValue(relative_path + os.sep)
                else:
                    self.dir_input.SetValue(str(chosen) + os.sep)
                prompt.Destroy()
            else:
                self.dir_input.SetValue(str(chosen) + os.sep)
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
        stripped = raw_path.strip().rstrip("/\\")
        if not stripped:
            return self.project_dir
        path = Path(stripped).expanduser()
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
        """Enable custom text-scaling controls only when native overlay is disabled."""
        enable_custom_controls = not bool(self.use_kicad_native_overlay.GetValue())
        self.scale_input.Enable(enable_custom_controls)
        self.scale_label.Enable(enable_custom_controls)

    def _sync_3d_render_controls(self) -> None:
        """Enable kicad-cli path controls only when 3D render is enabled."""
        enabled = bool(self.use_3d_render.GetValue())
        self.kicad_cli_label.Enable(enabled)
        self.kicad_cli_path_input.Enable(enabled)
        self.kicad_cli_detect_btn.Enable(enabled)

    def _sync_advanced_overlay_controls(self) -> None:
        """Disable 2D overlay options while 3D render mode is enabled."""
        using_3d = bool(self.use_3d_render.GetValue())
        enable_2d_options = not using_3d
        self.silkscreen_options_label.Enable(enable_2d_options)
        self.include_component_silkscreen.Enable(enable_2d_options)
        self.include_fab_layer.Enable(enable_2d_options)
        self.use_kicad_native_overlay.Enable(enable_2d_options)
        if using_3d:
            # Keep 3D mode visually clean and avoid accidental mixed-mode exports.
            self.include_component_silkscreen.SetValue(False)
            self.include_fab_layer.SetValue(False)

    def _on_native_overlay_toggle(self, event) -> None:
        """Update control state when native-overlay checkbox changes."""
        self._sync_text_scaling_controls()
        event.Skip()

    def _on_3d_render_toggle(self, event) -> None:
        """Update control state when 3D render checkbox changes."""
        self._sync_3d_render_controls()
        self._sync_advanced_overlay_controls()
        event.Skip()

    def _on_detect_kicad_cli(self, event) -> None:
        """Auto-detect kicad-cli and populate the path field."""
        found = _find_kicad_cli(None)
        if found:
            self.kicad_cli_path_input.SetValue(found)
        else:
            wx.MessageBox(
                "kicad-cli not found. Please enter the path manually.",
                "kicad-cli Not Found",
                wx.OK | wx.ICON_INFORMATION,
            )
    
    def _on_save_messages(self, event) -> None:
        """Save diagnostic output messages to a file."""
        messages = self.output_messages.GetValue()
        if not messages.strip():
            wx.MessageBox(
                "No messages to save.",
                "Nothing to Save",
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        
        dlg = wx.FileDialog(
            self,
            "Save diagnostic messages",
            defaultDir=str(self.project_dir),
            defaultFile="k2f_export_log.txt",
            wildcard="Text files (*.txt)|*.txt|All files (*.*)|*.*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if dlg.ShowModal() == wx.ID_OK:
            try:
                Path(dlg.GetPath()).write_text(messages, encoding="utf-8")
                wx.MessageBox(
                    f"Diagnostic messages saved to:\n{dlg.GetPath()}",
                    "Save Successful",
                    wx.OK | wx.ICON_INFORMATION,
                )
            except OSError as e:
                wx.MessageBox(
                    f"Failed to save file:\n{e}",
                    "Save Failed",
                    wx.OK | wx.ICON_ERROR,
                )
        dlg.Destroy()
    
    def _on_generate(self, event) -> None:
        """Run the export process without closing the dialog."""
        self.clear_messages()
        self.generate_btn.Enable(False)
        try:
            self._run_export()
        finally:
            self.generate_btn.Enable(True)

    def _run_export(self) -> None:
        """Execute the full export pipeline and log diagnostics to the output panel."""
        import datetime

        self.append_message(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Starting export...")
        wx.GetApp().Yield()

        (
            part_name,
            out_dir,
            text_scale,
            pad_scale,
            soldermask_color,
            silkscreen_color,
            part_family,
            part_type,
            use_kicad_native_overlay,
            include_component_silkscreen,
            include_fab_layer,
            use_3d_render,
            kicad_cli_path,
        ) = self.get_values()

        effective_native_overlay = bool(use_kicad_native_overlay and not use_3d_render)

        self.append_message(f"  Part name:   {part_name}")
        self.append_message(f"  Output dir:  {out_dir}")
        self.append_message(f"  3D render (requested):             {use_3d_render}")
        self.append_message(f"  Native silk overlay (requested):   {use_kicad_native_overlay}")
        self.append_message(
            f"  Native silk overlay (effective):   {effective_native_overlay}"
        )
        if use_3d_render and use_kicad_native_overlay:
            self.append_message("  Mode note: native overlay disabled because 3D render is enabled.")
        wx.GetApp().Yield()

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.append_message(f"ERROR: Could not create output directory: {exc}")
            return

        render_options = {
            "soldermask_color": soldermask_color,
            "silkscreen_color": silkscreen_color,
            "pad_scale": pad_scale,
            "silk_text_scale": text_scale,
            "include_component_silkscreen": include_component_silkscreen,
            "include_fab_layer": include_fab_layer,
        }

        self.append_message("  Running base export (extractor)...")
        wx.GetApp().Yield()
        try:
            export_board_to_fritzing_stub(
                self.board_path,
                out_dir,
                part_name=part_name,
                render_options=render_options,
                part_family=part_family,
                part_type=part_type,
            )
            self.append_message("  Base export complete.")
        except Exception as exc:  # noqa: BLE001
            self.append_message(f"ERROR: Base export failed: {exc}")
            return
        wx.GetApp().Yield()

        native_overlay_applied = False
        if effective_native_overlay:
            self.append_message("  Plotting KiCad SVG layers...")
            wx.GetApp().Yield()
            plotted = plot_kicad_svg_layers(
                self.board,
                out_dir / "kicad_svg_plots",
                include_fab_layer=include_fab_layer,
            )
            self.append_message(f"  Plotted layers: {list(plotted.keys()) or 'none'}")
            overlaid_path = overlay_kicad_plots_on_breadboard(
                out_dir,
                plotted,
                replace_custom_silkscreen=True,
                silkscreen_color=silkscreen_color,
            )
            native_overlay_applied = overlaid_path is not None
            self.append_message(
                f"  Native silkscreen overlay: {'applied' if native_overlay_applied else 'not applied (no matching SVG bounds?)'}"
            )
            wx.GetApp().Yield()
        else:
            self.append_message("  Native silkscreen overlay: skipped.")

        write_overlay_mode_marker(
            out_dir,
            requested_native_overlay=use_kicad_native_overlay,
            applied_native_overlay=native_overlay_applied,
        )

        if use_3d_render:
            self.append_message("  Starting 3D render via kicad-cli...")
            cli_used = kicad_cli_path or _find_kicad_cli(None) or "(auto-detect)"
            self.append_message(f"  kicad-cli: {cli_used}")
            wx.GetApp().Yield()

            stripped = strip_silkscreen_overlays_for_3d(
                out_dir,
                silkscreen_color=silkscreen_color,
            )
            self.append_message(
                f"  2D silkscreen overlays removed for 3D mode: {'yes' if stripped else 'no (breadboard.svg not found?)'}"
            )

            board_bounds_mm = None
            model_json_path = out_dir / "board_model.json"
            if model_json_path.exists():
                model_data = json.loads(model_json_path.read_text(encoding="utf-8"))
                board_bounds_mm = model_data.get("board_outline", {}).get("bounds_mm")
                self.append_message(f"  Board bounds from model: {board_bounds_mm}")
            else:
                self.append_message("  WARNING: board_model.json not found; render may be uncropped.")

            render_diagnostics: list[str] = []
            render_png = render_board_3d(
                self.board_path,
                out_dir / "kicad_svg_plots",
                board_bounds_mm=board_bounds_mm,
                kicad_cli_path=kicad_cli_path or None,
                soldermask_color=soldermask_color,
                silkscreen_color=silkscreen_color,
                diagnostics=render_diagnostics,
            )
            for line in render_diagnostics:
                self.append_message(line)
            if render_png:
                self.append_message(f"  3D render saved: {render_png}")
                embedded = embed_3d_render_in_breadboard_svg(out_dir, render_png)
                self.append_message(
                    f"  Embedded into breadboard SVG: {'yes' if embedded else 'no (boardOutline not found?)'}"
                )
            else:
                self.append_message("  ERROR: 3D render failed. Check kicad-cli path and board file.")
            wx.GetApp().Yield()

        self.append_message("  Rebuilding .fzpz archive...")
        fzp_files = sorted(out_dir.glob("*.fzp"), key=lambda p: p.stat().st_mtime, reverse=True)
        if fzp_files:
            build_fritzing_package_zip(out_dir, part_basename=fzp_files[0].stem)
            self.append_message(f"  Package written: {fzp_files[0].stem}.fzpz")
        else:
            self.append_message("  WARNING: No .fzp file found; .fzpz not rebuilt.")

        self.append_message(
            f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Export complete."
        )

    def append_message(self, message: str) -> None:
        """Append a diagnostic message to the output panel."""
        current = self.output_messages.GetValue()
        if current:
            self.output_messages.SetValue(current + "\n" + message)
        else:
            self.output_messages.SetValue(message)
        self.output_messages.SetInsertionPointEnd()
    
    def clear_messages(self) -> None:
        """Clear all diagnostic messages from the output panel."""
        self.output_messages.SetValue("")
    
    def get_values(self) -> tuple[str, Path, float, float, str, str, str, str, bool, bool, bool, bool, str]:
        """Return dialog values including rendering options."""
        part_name = self.part_name_input.GetValue()
        part_family = self.part_family_input.GetValue().strip() or "KiCad2Fritzing Generated"
        part_type = self.part_type_input.GetValue().strip() or "Custom PCB"
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
        include_component_silkscreen = bool(self.include_component_silkscreen.GetValue())
        include_fab_layer = bool(self.include_fab_layer.GetValue())
        use_3d_render = bool(self.use_3d_render.GetValue())
        kicad_cli_path = self.kicad_cli_path_input.GetValue().strip()
        return (
            part_name,
            out_dir,
            text_scale,
            pad_scale,
            soldermask_color,
            silkscreen_color,
            part_family,
            part_type,
            use_kicad_native_overlay,
            include_component_silkscreen,
            include_fab_layer,
            use_3d_render,
            kicad_cli_path,
        )


class PCBtoFritzingPartActionPlugin(pcbnew.ActionPlugin if pcbnew else object):
    """Action plugin wrapper for KiCad's PCB Editor."""

    def defaults(self) -> None:
        self.name = "PCB to Fritzing Part"
        self.category = "Export"
        self.description = "Export current KiCad PCB into Fritzing starter assets"
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
        
        dlg = PCBtoFritzingPartDialog(None, board_path, board=board)
        dlg.ShowModal()
        dlg.Destroy()


def register_plugin() -> bool:
    """Register with KiCad if pcbnew runtime is available."""
    if pcbnew is None:
        return False

    PCBtoFritzingPartActionPlugin().register()
    return True
