"""Extraction primitives for converting KiCad data into Fritzing artifacts."""

from __future__ import annotations

import json
import math
import os
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from pathlib import Path


FOOTPRINT_START_RE = re.compile(r'^\(footprint\s+"([^"]+)"')
GR_RECT_START_RE = re.compile(r'^\(gr_rect\b')
GR_TEXT_START_RE = re.compile(r'^\(gr_text\s+"((?:[^"\\]|\\.)*)"')
GR_LINE_START_RE = re.compile(r'^\(gr_line\b')
GR_POLY_START_RE = re.compile(r'^\(gr_poly\b')
GR_ARC_START_RE = re.compile(r'^\(gr_arc\b')
PROPERTY_RE = re.compile(r'^\(property\s+"([^"]+)"\s+"([^"]*)"')
AT_RE = re.compile(r'^\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)')
AT_ANY_RE = re.compile(r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)')
PAD_START_RE = re.compile(r'^\(pad\s+"([^"]+)"')
NET_DECL_RE = re.compile(r'^\(net\s+\d+\s+"([^"]+)"\)')
NET_ANY_RE = re.compile(r'\(net(?:\s+\d+)?\s+"([^"]+)"\)')
PINFUNCTION_RE = re.compile(r'\(pinfunction\s+"([^"]+)"\)')
FP_TEXT_RE = re.compile(r'^\(fp_text\s+(\w+)\s+"([^"]*)"')
FP_LINE_START_RE = re.compile(r'^\(fp_line\b')
FP_POLY_START_RE = re.compile(r'^\(fp_poly\b')
START_RE = re.compile(r'\(start\s+([-\d.]+)\s+([-\d.]+)\)')
MID_RE = re.compile(r'\(mid\s+([-\d.]+)\s+([-\d.]+)\)')
END_RE = re.compile(r'\(end\s+([-\d.]+)\s+([-\d.]+)\)')
XY_RE = re.compile(r'\(xy\s+([-\d.]+)\s+([-\d.]+)\)')
LAYER_RE = re.compile(r'\(layer\s+"([^"]+)"\)')
SIZE_RE = re.compile(r'\(size\s+([-\d.]+)\s+([-\d.]+)\)')
WIDTH_RE = re.compile(r'\(width\s+([-\d.]+)\)')
THICKNESS_RE = re.compile(r'\(thickness\s+([-\d.]+)\)')
JUSTIFY_RE = re.compile(r'\(justify\s+([^\)]+)\)')
HIDE_RE = re.compile(r'\(hide\s+yes\)')
POWER_NET_HINTS = {"V+", "VCC", "VIN", "5V", "3V3", "3.3V", "GND"}
POWER_NET_HINTS_UPPER = {n.upper() for n in POWER_NET_HINTS}
VIEW_IMAGE_PATHS = {
    "icon": "icon/icon.svg",
    "breadboard": "breadboard/breadboard.svg",
    "schematic": "schematic/schematic.svg",
    "pcb": "pcb/pcb.svg",
}
GENERIC_PINFUNCTION_RE = re.compile(r"^pin[_\s-]*\d+$", re.IGNORECASE)
SILK_TEXT_SIZE_MULTIPLIER = float(os.getenv("K2F_SILK_TEXT_SCALE", "1.15"))


def _rotate_point(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    radians = math.radians(angle_deg)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)
    return (x * cos_a - y * sin_a, x * sin_a + y * cos_a)


def _extract_block(lines: list[str], start_index: int) -> tuple[list[str], int]:
    block: list[str] = []
    depth = 0
    idx = start_index

    while idx < len(lines):
        line = lines[idx]
        depth += line.count("(")
        depth -= line.count(")")
        block.append(line)
        idx += 1
        if depth == 0:
            break

    return block, idx


def _is_edge_cuts_block(block_text: str) -> bool:
    layer_match = LAYER_RE.search(block_text)
    return bool(layer_match and layer_match.group(1) == "Edge.Cuts")


def _is_front_silks_block(block_text: str) -> bool:
    layer_match = LAYER_RE.search(block_text)
    return bool(layer_match and layer_match.group(1) == "F.SilkS")


def _decode_kicad_text(text: str) -> str:
    return text.replace(r'\n', '\n').replace(r'\"', '"').strip()


def _transform_to_board_space(
    x: float,
    y: float,
    footprint_at: list[float],
) -> tuple[float, float]:
    fp_x = float(footprint_at[0])
    fp_y = float(footprint_at[1])
    fp_rot = float(footprint_at[2])
    # KiCad footprint-local coordinates map correctly here with inverse sign.
    rotated_x, rotated_y = _rotate_point(x, y, -fp_rot)
    return fp_x + rotated_x, fp_y + rotated_y


def _text_style_from_block(block_text: str) -> tuple[float, float]:
    size_match = SIZE_RE.search(block_text)
    thickness_match = THICKNESS_RE.search(block_text)
    size_y = float(size_match.group(2)) if size_match else 1.0
    thickness = float(thickness_match.group(1)) if thickness_match else 0.15
    return size_y, thickness


def _text_justify_from_block(block_text: str) -> tuple[str, str]:
    justify_match = JUSTIFY_RE.search(block_text)
    if not justify_match:
        return "center", "middle"

    tokens = {token.strip().lower() for token in justify_match.group(1).split() if token.strip()}
    h_align = "center"
    v_align = "middle"

    if "left" in tokens:
        h_align = "left"
    elif "right" in tokens:
        h_align = "right"

    if "top" in tokens:
        v_align = "top"
    elif "bottom" in tokens:
        v_align = "bottom"

    return h_align, v_align


def _project_mm_to_svg(
    x_mm: float,
    y_mm: float,
    bounds: tuple[float, float, float, float],
    height: int,
    margin: int,
    scale: float,
) -> tuple[float, float]:
    min_x, min_y, _, _ = bounds
    x = margin + ((x_mm - min_x) * scale)
    y = margin + ((y_mm - min_y) * scale)
    return round(x, 3), round(y, 3)


def _render_svg_silkscreen(
    board_model: dict | None,
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    margin: int,
    scale: float,
    color: str,
) -> str:
    silkscreen = (board_model or {}).get("silkscreen", {})
    parts: list[str] = []

    for polygon in silkscreen.get("polygons", []):
        points_mm = polygon.get("points_mm", [])
        if len(points_mm) < 3:
            continue
        projected = [
            _project_mm_to_svg(float(pt["x"]), float(pt["y"]), bounds, height, margin, scale)
            for pt in points_mm
        ]
        points_attr = " ".join(f"{x},{y}" for x, y in projected)
        parts.append(
            f'<polygon points="{points_attr}" fill="{color}" stroke="none" opacity="0.92"/>'
        )

    for line in silkscreen.get("lines", []):
        start = line.get("start_mm")
        end = line.get("end_mm")
        if not start or not end:
            continue
        x1, y1 = _project_mm_to_svg(float(start["x"]), float(start["y"]), bounds, height, margin, scale)
        x2, y2 = _project_mm_to_svg(float(end["x"]), float(end["y"]), bounds, height, margin, scale)
        stroke_width = max(0.5, round(float(line.get("stroke_width_mm", 0.15)) * scale, 3))
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" opacity="0.92"/>'
        )

    for text_item in silkscreen.get("texts", []):
        raw_text = str(text_item.get("text", "")).strip()
        if not raw_text:
            continue
        x, y = _project_mm_to_svg(
            float(text_item.get("x_mm", 0.0)),
            float(text_item.get("y_mm", 0.0)),
            bounds,
            height,
            margin,
            scale,
        )
        font_size = max(
            3.0,
            round(float(text_item.get("size_mm", 1.0)) * scale * SILK_TEXT_SIZE_MULTIPLIER, 3),
        )
        angle = -float(text_item.get("rotation_deg", 0.0))
        h_align = str(text_item.get("h_align", "center"))
        v_align = str(text_item.get("v_align", "middle"))
        text_anchor = {"left": "start", "center": "middle", "right": "end"}.get(h_align, "middle")
        dominant_baseline = {
            "top": "hanging",
            "middle": "middle",
            "bottom": "alphabetic",
        }.get(v_align, "middle")
        transform = f' transform="rotate({round(angle, 3)} {x} {y})"' if angle else ""
        lines = raw_text.splitlines() or [raw_text]
        line_step = round(font_size * 1.15, 3)
        if v_align == "top":
            first_dy = 0.0
        elif v_align == "bottom":
            first_dy = round(-((len(lines) - 1) * line_step), 3)
        else:
            first_dy = round(-((len(lines) - 1) * line_step) / 2, 3)
        tspans: list[str] = []
        for idx, line_text in enumerate(lines):
            dy = first_dy if idx == 0 else line_step
            tspans.append(f'<tspan x="{x}" dy="{dy}">{escape(line_text)}</tspan>')
        parts.append(
            f'<text x="{x}" y="{y}" font-size="{font_size}" font-family="sans-serif" '
            f'fill="{color}" text-anchor="{text_anchor}" dominant-baseline="{dominant_baseline}" opacity="0.96"{transform}>'
            + "".join(tspans)
            + '</text>'
        )

    return "".join(parts)


def _approximate_arc_points(
    start: tuple[float, float],
    mid: tuple[float, float],
    end: tuple[float, float],
    steps: int = 16,
) -> list[tuple[float, float]]:
    x1, y1 = start
    x2, y2 = mid
    x3, y3 = end

    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return [start, end]

    ux = (
        ((x1 * x1 + y1 * y1) * (y2 - y3))
        + ((x2 * x2 + y2 * y2) * (y3 - y1))
        + ((x3 * x3 + y3 * y3) * (y1 - y2))
    ) / d
    uy = (
        ((x1 * x1 + y1 * y1) * (x3 - x2))
        + ((x2 * x2 + y2 * y2) * (x1 - x3))
        + ((x3 * x3 + y3 * y3) * (x2 - x1))
    ) / d

    radius = math.hypot(x1 - ux, y1 - uy)
    if radius < 1e-9:
        return [start, end]

    start_a = math.atan2(y1 - uy, x1 - ux)
    mid_a = math.atan2(y2 - uy, x2 - ux)
    end_a = math.atan2(y3 - uy, x3 - ux)

    def normalize_angle(angle: float) -> float:
        while angle < 0:
            angle += 2 * math.pi
        while angle >= 2 * math.pi:
            angle -= 2 * math.pi
        return angle

    sa = normalize_angle(start_a)
    ma = normalize_angle(mid_a)
    ea = normalize_angle(end_a)

    def in_ccw_range(a_start: float, a_mid: float, a_end: float) -> bool:
        if a_end < a_start:
            a_end += 2 * math.pi
        if a_mid < a_start:
            a_mid += 2 * math.pi
        return a_start <= a_mid <= a_end

    ccw = in_ccw_range(sa, ma, ea)
    if ccw:
        end_angle = ea
        if end_angle < sa:
            end_angle += 2 * math.pi
        delta = (end_angle - sa) / steps
        angles = [sa + (delta * i) for i in range(steps + 1)]
    else:
        end_angle = ea
        if end_angle > sa:
            end_angle -= 2 * math.pi
        delta = (end_angle - sa) / steps
        angles = [sa + (delta * i) for i in range(steps + 1)]

    points: list[tuple[float, float]] = []
    for angle in angles:
        points.append((ux + (radius * math.cos(angle)), uy + (radius * math.sin(angle))))
    return points


def _build_polygon_from_segments(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> list[dict[str, float]]:
    if not segments:
        return []

    remaining = segments.copy()
    first = remaining.pop(0)
    chain: list[tuple[float, float]] = [first[0], first[1]]
    tolerance = 1e-3

    while remaining:
        current = chain[-1]
        found_idx = -1
        reverse = False
        for i, (s, e) in enumerate(remaining):
            if math.hypot(current[0] - s[0], current[1] - s[1]) <= tolerance:
                found_idx = i
                reverse = False
                break
            if math.hypot(current[0] - e[0], current[1] - e[1]) <= tolerance:
                found_idx = i
                reverse = True
                break

        if found_idx < 0:
            break

        s, e = remaining.pop(found_idx)
        chain.append(s if reverse else e)

    if len(chain) < 4:
        return []

    if math.hypot(chain[0][0] - chain[-1][0], chain[0][1] - chain[-1][1]) <= tolerance:
        chain = chain[:-1]

    return [{"x": round(p[0], 6), "y": round(p[1], 6)} for p in chain]


def _is_pin_header_footprint(footprint: dict) -> bool:
    footprint_name = str(footprint.get("footprint", "")).lower()
    value = str(footprint.get("value", "")).lower()
    reference = str(footprint.get("reference", "")).strip().upper()
    pad_count = len(footprint.get("pads", []))

    if "pinheader" in footprint_name or "pin_header" in footprint_name:
        return True

    if "pinsocket" in footprint_name or "pin_socket" in footprint_name:
        return True

    if "connector" in footprint_name and pad_count >= 2:
        return True

    # Many projects use custom connector footprints with J* references.
    if reference.startswith("J") and pad_count >= 2:
        return True

    # Common legacy naming when symbol/footprint text doesn't include "PinHeader".
    if value.startswith("conn_01x") and "connector" in footprint_name:
        return True

    return False


def _normalize_pinfunction(pinfunction: str) -> str:
    text = pinfunction.strip()
    if not text:
        return ""
    if GENERIC_PINFUNCTION_RE.match(text):
        return ""
    # Many connector symbols emit auto tokens like P1_1 or 1_1.
    if re.match(r"^[A-Za-z]*\d*_\d+$", text):
        return ""
    return text


def _choose_header_label(footprint: dict) -> str:
    user_labels_raw = [s.strip() for s in footprint.get("silkscreen_user_labels", []) if s.strip()]
    user_labels: list[str] = []
    for label in user_labels_raw:
        if "${" in label:
            continue
        # Ignore labels that are usually per-pin markers or numeric pin hints.
        if label.isdigit():
            continue
        if len(label) < 2:
            continue
        user_labels.append(label)

    unique_labels = list(dict.fromkeys(user_labels))
    if len(unique_labels) == 1:
        return unique_labels[0]

    reference = str(footprint.get("reference", "")).strip()
    if reference:
        return reference

    return "HEADER"


def _is_public_silkscreen_footprint(footprint: dict) -> bool:
    return not bool(footprint.get("pads"))


def _tight_canvas_size(
    points: list[tuple[float, float]],
    fallback: tuple[int, int],
    padding: int,
) -> tuple[int, int]:
    if not points:
        return fallback

    max_x = max(point[0] for point in points)
    max_y = max(point[1] for point in points)
    width = max(int(math.ceil(max_x + padding)), 2 * padding)
    height = max(int(math.ceil(max_y + padding)), 2 * padding)
    return width, height


def _scaled_pad_radius(scale: float, min_radius: float = 1.6, max_radius: float = 3.8) -> float:
    # Through-hole annulus is roughly 1.8-2.0 mm on many boards; keep a sane screen clamp.
    return round(max(min_radius, min(max_radius, 0.95 * scale)), 3)


def parse_kicad_board_to_model(board_file: Path) -> dict:
    """Parse a KiCad PCB file into a small intermediate model for conversion."""
    text = board_file.read_text(encoding="utf-8")
    lines = text.splitlines()

    footprints: list[dict] = []
    nets: set[str] = set()
    board_outline_polygons: list[list[dict[str, float]]] = []
    board_outline_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    silkscreen_texts: list[dict] = []
    silkscreen_lines: list[dict] = []
    silkscreen_polygons: list[dict] = []

    for line in lines:
        stripped = line.strip()
        net_match = NET_DECL_RE.match(stripped)
        if net_match:
            nets.add(net_match.group(1))

    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()

        if GR_RECT_START_RE.match(stripped):
            gr_block, idx = _extract_block(lines, idx)
            gr_text = " ".join(s.strip() for s in gr_block)
            start_match = START_RE.search(gr_text)
            end_match = END_RE.search(gr_text)

            if (
                _is_edge_cuts_block(gr_text)
                and start_match
                and end_match
            ):
                sx = float(start_match.group(1))
                sy = float(start_match.group(2))
                ex = float(end_match.group(1))
                ey = float(end_match.group(2))
                min_x, max_x = (sx, ex) if sx <= ex else (ex, sx)
                min_y, max_y = (sy, ey) if sy <= ey else (ey, sy)
                board_outline_polygons.append(
                    [
                        {"x": min_x, "y": min_y},
                        {"x": max_x, "y": min_y},
                        {"x": max_x, "y": max_y},
                        {"x": min_x, "y": max_y},
                    ]
                )
            elif _is_front_silks_block(gr_text) and start_match and end_match:
                sx = float(start_match.group(1))
                sy = float(start_match.group(2))
                ex = float(end_match.group(1))
                ey = float(end_match.group(2))
                min_x, max_x = (sx, ex) if sx <= ex else (ex, sx)
                min_y, max_y = (sy, ey) if sy <= ey else (ey, sy)
                silkscreen_polygons.append(
                    {
                        "points_mm": [
                            {"x": min_x, "y": min_y},
                            {"x": max_x, "y": min_y},
                            {"x": max_x, "y": max_y},
                            {"x": min_x, "y": max_y},
                        ]
                    }
                )
            continue

        if GR_TEXT_START_RE.match(stripped):
            gr_block, idx = _extract_block(lines, idx)
            gr_text = " ".join(s.strip() for s in gr_block)
            text_match = GR_TEXT_START_RE.match(stripped)
            at_match = AT_ANY_RE.search(gr_text)
            if text_match and at_match and _is_front_silks_block(gr_text) and not HIDE_RE.search(gr_text):
                size_y, thickness = _text_style_from_block(gr_text)
                h_align, v_align = _text_justify_from_block(gr_text)
                silkscreen_texts.append(
                    {
                        "text": _decode_kicad_text(text_match.group(1)),
                        "x_mm": float(at_match.group(1)),
                        "y_mm": float(at_match.group(2)),
                        "rotation_deg": float(at_match.group(3) or 0.0),
                        "size_mm": size_y,
                        "thickness_mm": thickness,
                        "h_align": h_align,
                        "v_align": v_align,
                    }
                )
            continue

        if GR_LINE_START_RE.match(stripped):
            gr_block, idx = _extract_block(lines, idx)
            gr_text = " ".join(s.strip() for s in gr_block)
            start_match = START_RE.search(gr_text)
            end_match = END_RE.search(gr_text)
            if _is_edge_cuts_block(gr_text) and start_match and end_match:
                sx = float(start_match.group(1))
                sy = float(start_match.group(2))
                ex = float(end_match.group(1))
                ey = float(end_match.group(2))
                board_outline_segments.append(((sx, sy), (ex, ey)))
            elif _is_front_silks_block(gr_text) and start_match and end_match:
                width_match = WIDTH_RE.search(gr_text)
                silkscreen_lines.append(
                    {
                        "start_mm": {"x": float(start_match.group(1)), "y": float(start_match.group(2))},
                        "end_mm": {"x": float(end_match.group(1)), "y": float(end_match.group(2))},
                        "stroke_width_mm": float(width_match.group(1)) if width_match else 0.15,
                    }
                )
            continue

        if GR_ARC_START_RE.match(stripped):
            gr_block, idx = _extract_block(lines, idx)
            gr_text = " ".join(s.strip() for s in gr_block)
            start_match = START_RE.search(gr_text)
            mid_match = MID_RE.search(gr_text)
            end_match = END_RE.search(gr_text)
            if _is_edge_cuts_block(gr_text) and start_match and mid_match and end_match:
                start_pt = (float(start_match.group(1)), float(start_match.group(2)))
                mid_pt = (float(mid_match.group(1)), float(mid_match.group(2)))
                end_pt = (float(end_match.group(1)), float(end_match.group(2)))
                arc_points = _approximate_arc_points(start_pt, mid_pt, end_pt, steps=16)
                for i in range(len(arc_points) - 1):
                    board_outline_segments.append((arc_points[i], arc_points[i + 1]))
            continue

        if GR_POLY_START_RE.match(stripped):
            gr_block, idx = _extract_block(lines, idx)
            gr_text = " ".join(s.strip() for s in gr_block)
            if _is_edge_cuts_block(gr_text):
                points = [
                    {"x": float(x), "y": float(y)}
                    for x, y in XY_RE.findall(gr_text)
                ]
                if len(points) >= 3:
                    board_outline_polygons.append(points)
            elif _is_front_silks_block(gr_text):
                points = [
                    {"x": float(x), "y": float(y)}
                    for x, y in XY_RE.findall(gr_text)
                ]
                if len(points) >= 3:
                    silkscreen_polygons.append({"points_mm": points})
            continue

        fp_match = FOOTPRINT_START_RE.match(stripped)
        if not fp_match:
            idx += 1
            continue

        footprint_name = fp_match.group(1)
        fp_block, idx = _extract_block(lines, idx)

        footprint = {
            "reference": "",
            "value": "",
            "footprint": footprint_name,
            "at": [0.0, 0.0, 0.0],
            "silkscreen_user_labels": [],
            "pads": [],
        }
        footprint_silkscreen_texts: list[dict] = []
        footprint_silkscreen_lines: list[dict] = []
        footprint_silkscreen_polygons: list[dict] = []

        fp_line_index = 0
        while fp_line_index < len(fp_block):
            fp_line = fp_block[fp_line_index].strip()

            prop_match = PROPERTY_RE.match(fp_line)
            if prop_match:
                prop_block, new_fp_idx = _extract_block(fp_block, fp_line_index)
                key, value = prop_match.group(1), prop_match.group(2)
                if key == "Reference":
                    footprint["reference"] = value
                elif key == "Value":
                    footprint["value"] = value
                fp_line_index = new_fp_idx
                continue

            fp_text_match = FP_TEXT_RE.match(fp_line)
            if fp_text_match:
                fp_text_block, new_fp_idx = _extract_block(fp_block, fp_line_index)
                fp_text_kind = fp_text_match.group(1)
                fp_text_value = _decode_kicad_text(fp_text_match.group(2))
                fp_text_all = " ".join(s.strip() for s in fp_text_block)
                layer_match = LAYER_RE.search(fp_text_all)
                if (
                    fp_text_kind == "user"
                    and fp_text_value.strip()
                    and "${" not in fp_text_value
                    and layer_match
                    and layer_match.group(1) == "F.SilkS"
                ):
                    footprint["silkscreen_user_labels"].append(fp_text_value.strip())
                if (
                    fp_text_value.strip()
                    and "${" not in fp_text_value
                    and fp_text_kind not in {"reference", "value"}
                    and _is_front_silks_block(fp_text_all)
                    and not HIDE_RE.search(fp_text_all)
                ):
                    at_match = AT_ANY_RE.search(fp_text_all)
                    if at_match:
                        size_y, thickness = _text_style_from_block(fp_text_all)
                        h_align, v_align = _text_justify_from_block(fp_text_all)
                        footprint_silkscreen_texts.append(
                            {
                                "text": fp_text_value,
                                "x_mm": float(at_match.group(1)),
                                "y_mm": float(at_match.group(2)),
                                "rotation_deg": float(at_match.group(3) or 0.0),
                                "size_mm": size_y,
                                "thickness_mm": thickness,
                                "h_align": h_align,
                                "v_align": v_align,
                            }
                        )
                fp_line_index = new_fp_idx
                continue

            at_match = AT_RE.match(fp_line)
            if at_match and footprint["at"] == [0.0, 0.0, 0.0]:
                rotation = float(at_match.group(3) or 0.0)
                footprint["at"] = [
                    float(at_match.group(1)),
                    float(at_match.group(2)),
                    rotation,
                ]

            pad_match = PAD_START_RE.match(fp_line)
            if pad_match:
                pad_block, new_fp_idx = _extract_block(fp_block, fp_line_index)
                pad_text = " ".join(s.strip() for s in pad_block)
                net_any_match = NET_ANY_RE.search(pad_text)
                pinfunction_match = PINFUNCTION_RE.search(pad_text)

                pad = {
                    "pad": pad_match.group(1),
                    "net": net_any_match.group(1) if net_any_match else "",
                    "pinfunction": pinfunction_match.group(1) if pinfunction_match else "",
                    "at": [0.0, 0.0, 0.0],
                }
                pad_at_match = AT_ANY_RE.search(pad_text)
                if pad_at_match:
                    pad["at"] = [
                        float(pad_at_match.group(1)),
                        float(pad_at_match.group(2)),
                        float(pad_at_match.group(3) or 0.0),
                    ]
                if pad["net"]:
                    nets.add(pad["net"])
                footprint["pads"].append(pad)
                fp_line_index = new_fp_idx
                continue

            if FP_LINE_START_RE.match(fp_line):
                fp_graphic_block, new_fp_idx = _extract_block(fp_block, fp_line_index)
                fp_graphic_text = " ".join(s.strip() for s in fp_graphic_block)
                start_match = START_RE.search(fp_graphic_text)
                end_match = END_RE.search(fp_graphic_text)
                if _is_front_silks_block(fp_graphic_text) and start_match and end_match:
                    width_match = WIDTH_RE.search(fp_graphic_text)
                    footprint_silkscreen_lines.append(
                        {
                            "start_mm": {"x": float(start_match.group(1)), "y": float(start_match.group(2))},
                            "end_mm": {"x": float(end_match.group(1)), "y": float(end_match.group(2))},
                            "stroke_width_mm": float(width_match.group(1)) if width_match else 0.15,
                        }
                    )
                fp_line_index = new_fp_idx
                continue

            if FP_POLY_START_RE.match(fp_line):
                fp_graphic_block, new_fp_idx = _extract_block(fp_block, fp_line_index)
                fp_graphic_text = " ".join(s.strip() for s in fp_graphic_block)
                if _is_front_silks_block(fp_graphic_text):
                    points = [
                        {"x": float(x), "y": float(y)}
                        for x, y in XY_RE.findall(fp_graphic_text)
                    ]
                    if len(points) >= 3:
                        footprint_silkscreen_polygons.append({"points_mm": points})
                fp_line_index = new_fp_idx
                continue

            fp_line_index += 1

        footprint_at = footprint.get("at", [0.0, 0.0, 0.0])
        fp_rot = float(footprint_at[2])
        if _is_public_silkscreen_footprint(footprint):
            for text_item in footprint_silkscreen_texts:
                board_x, board_y = _transform_to_board_space(
                    float(text_item["x_mm"]),
                    float(text_item["y_mm"]),
                    footprint_at,
                )
                silkscreen_texts.append(
                    {
                        **text_item,
                        "x_mm": round(board_x, 6),
                        "y_mm": round(board_y, 6),
                        "rotation_deg": round(fp_rot + float(text_item.get("rotation_deg", 0.0)), 6),
                    }
                )

            for line_item in footprint_silkscreen_lines:
                start_x, start_y = _transform_to_board_space(
                    float(line_item["start_mm"]["x"]),
                    float(line_item["start_mm"]["y"]),
                    footprint_at,
                )
                end_x, end_y = _transform_to_board_space(
                    float(line_item["end_mm"]["x"]),
                    float(line_item["end_mm"]["y"]),
                    footprint_at,
                )
                silkscreen_lines.append(
                    {
                        "start_mm": {"x": round(start_x, 6), "y": round(start_y, 6)},
                        "end_mm": {"x": round(end_x, 6), "y": round(end_y, 6)},
                        "stroke_width_mm": line_item["stroke_width_mm"],
                    }
                )

            for polygon_item in footprint_silkscreen_polygons:
                transformed_points = []
                for point in polygon_item["points_mm"]:
                    point_x, point_y = _transform_to_board_space(
                        float(point["x"]),
                        float(point["y"]),
                        footprint_at,
                    )
                    transformed_points.append({"x": round(point_x, 6), "y": round(point_y, 6)})
                silkscreen_polygons.append({"points_mm": transformed_points})

        footprints.append(footprint)

    if not board_outline_polygons and board_outline_segments:
        chained_polygon = _build_polygon_from_segments(board_outline_segments)
        if chained_polygon:
            board_outline_polygons.append(chained_polygon)

    outline_points = [pt for poly in board_outline_polygons for pt in poly]
    if not outline_points and board_outline_segments:
        for segment in board_outline_segments:
            outline_points.append({"x": segment[0][0], "y": segment[0][1]})
            outline_points.append({"x": segment[1][0], "y": segment[1][1]})
    if outline_points:
        min_x = min(pt["x"] for pt in outline_points)
        max_x = max(pt["x"] for pt in outline_points)
        min_y = min(pt["y"] for pt in outline_points)
        max_y = max(pt["y"] for pt in outline_points)
        board_outline = {
            "polygons": board_outline_polygons,
            "bounds_mm": {
                "min_x": min_x,
                "min_y": min_y,
                "max_x": max_x,
                "max_y": max_y,
            },
        }
    else:
        board_outline = {"polygons": [], "bounds_mm": None}

    model = {
        "source_board": str(board_file),
        "nets": [{"name": name} for name in sorted(nets)],
        "footprints": footprints,
        "board_outline": board_outline,
        "silkscreen": {
            "texts": silkscreen_texts,
            "lines": silkscreen_lines,
            "polygons": silkscreen_polygons,
        },
    }
    return model


def write_board_model_json(model: dict, out_dir: Path) -> Path:
    """Write intermediate board model JSON to disk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / "board_model.json"
    output_file.write_text(json.dumps(model, indent=2), encoding="utf-8")
    return output_file


def map_model_to_fritzing_connectors(model: dict) -> dict:
    """Map parsed board model into a starter Fritzing connector model.

    Current behavior focuses on pin header footprints only. This keeps
    generated parts aligned with common Fritzing wiring use-cases.
    """
    connectors: list[dict] = []

    for footprint in model.get("footprints", []):
        if not _is_pin_header_footprint(footprint):
            continue

        reference = footprint.get("reference", "")
        header_label = _choose_header_label(footprint)
        fp_at = footprint.get("at", [0.0, 0.0, 0.0])
        fp_x, fp_y, fp_rot = float(fp_at[0]), float(fp_at[1]), float(fp_at[2])
        for pad in footprint.get("pads", []):
            net_name = pad.get("net", "")
            pinfunction = pad.get("pinfunction", "")
            clean_pinfunction = _normalize_pinfunction(pinfunction)
            pad_at = pad.get("at", [0.0, 0.0, 0.0])
            pad_x, pad_y = float(pad_at[0]), float(pad_at[1])
            rotated_x, rotated_y = _rotate_point(pad_x, pad_y, -fp_rot)
            connector_role = (
                "power" if net_name.upper() in POWER_NET_HINTS_UPPER else "signal"
            )
            abs_x = fp_x + rotated_x
            abs_y = fp_y + rotated_y
            pad_number = str(pad.get("pad", "")).strip()

            connector_name = clean_pinfunction
            if not connector_name and net_name and not net_name.startswith("Net-"):
                connector_name = net_name
            if not connector_name:
                connector_name = f"{header_label}_{pad_number}" if pad_number else header_label

            connectors.append(
                {
                    "id": f"{reference}_pad{pad.get('pad', '')}",
                    "footprint_reference": reference,
                    "footprint_name": footprint.get("footprint", ""),
                    "pad": pad.get("pad", ""),
                    "name": connector_name,
                    "net": net_name,
                    "pinfunction": pinfunction,
                    "header_label": header_label,
                    "role": connector_role,
                    "position_mm": {
                        "x": round(abs_x, 6),
                        "y": round(abs_y, 6),
                    },
                }
            )

    return {
        "source_board": model.get("source_board", ""),
        "connector_count": len(connectors),
        "connectors": connectors,
    }


def write_fritzing_connector_model_json(connector_model: dict, out_dir: Path) -> Path:
    """Write starter Fritzing connector model JSON to disk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / "fritzing_connectors.json"
    output_file.write_text(json.dumps(connector_model, indent=2), encoding="utf-8")
    return output_file


def _sanitize_part_basename(name: str) -> str:
    """Return a filesystem-safe Fritzing part basename."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return cleaned or "generated_part"


def build_fritzing_part_fzp(connector_model: dict, part_basename: str = "generated_part") -> str:
    """Build a minimal Fritzing part XML document from connector model data."""
    safe_basename = _sanitize_part_basename(part_basename)
    part_title = safe_basename.replace("_", " ")
    module = ET.Element(
        "module",
        {
            "fritzingVersion": "0.9.3b",
            "moduleId": f"kicad2fritzing.{safe_basename}",
        },
    )
    ET.SubElement(module, "version").text = "0.1"
    ET.SubElement(module, "author").text = "KiCad2Fritzing"
    ET.SubElement(module, "title").text = part_title
    ET.SubElement(module, "label").text = part_title
    ET.SubElement(module, "date").text = "2026-05-10"
    ET.SubElement(module, "description").text = (
        "Auto-generated starter part from KiCad board connector model."
    )

    tags = ET.SubElement(module, "tags")
    ET.SubElement(tags, "tag").text = "kicad"
    ET.SubElement(tags, "tag").text = "generated"
    ET.SubElement(tags, "tag").text = "fritzing"

    ET.SubElement(module, "properties")

    views = ET.SubElement(module, "views")
    for view_name, layer_ids, image_name in (
        ("iconView", ["icon"], VIEW_IMAGE_PATHS["icon"]),
        ("breadboardView", ["breadboard"], VIEW_IMAGE_PATHS["breadboard"]),
        ("schematicView", ["schematic"], VIEW_IMAGE_PATHS["schematic"]),
        ("pcbView", ["copper0", "copper1"], VIEW_IMAGE_PATHS["pcb"]),
    ):
        view_elem = ET.SubElement(views, view_name)
        layers = ET.SubElement(view_elem, "layers", {"image": image_name})
        for layer_id in layer_ids:
            ET.SubElement(layers, "layer", {"layerId": layer_id})

    connectors_elem = ET.SubElement(module, "connectors")
    for index, connector in enumerate(connector_model.get("connectors", [])):
        connector_id = f"connector{index}"
        connector_elem = ET.SubElement(
            connectors_elem,
            "connector",
            {
                "id": connector_id,
                "name": connector.get("name", connector.get("id", connector_id)),
                "type": "male",
            },
        )
        ET.SubElement(connector_elem, "description").text = (
            f"{connector.get('footprint_reference', '')} "
            f"pad {connector.get('pad', '')} "
            f"(source: {connector.get('id', '')})"
        ).strip()

        connector_views = ET.SubElement(connector_elem, "views")
        svg_pin_id = f"{connector_id}pin"
        for view_name, layer in (
            ("breadboardView", "breadboard"),
            ("schematicView", "schematic"),
            ("pcbView", "copper1"),
        ):
            view_ref = ET.SubElement(connector_views, view_name)
            ET.SubElement(view_ref, "p", {"layer": layer, "svgId": svg_pin_id})

    return ET.tostring(module, encoding="unicode", xml_declaration=True)


def write_fritzing_part_fzp(
    connector_model: dict,
    out_dir: Path,
    part_basename: str = "generated_part",
) -> Path:
    """Write a minimal generated Fritzing .fzp part file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_basename = _sanitize_part_basename(part_basename)
    output_file = out_dir / f"{safe_basename}.fzp"
    fzp_xml = build_fritzing_part_fzp(connector_model, part_basename=safe_basename)
    output_file.write_text(fzp_xml, encoding="utf-8")
    return output_file


def _project_connector_positions(
    connector_model: dict,
    width: int,
    height: int,
    margin: int,
    bounds_mm: dict | None,
) -> tuple[list[tuple[float, float]], tuple[float, float, float, float], float]:
    connectors = connector_model.get("connectors", [])
    points = [
        (
            float(c.get("position_mm", {}).get("x", 0.0)),
            float(c.get("position_mm", {}).get("y", 0.0)),
        )
        for c in connectors
    ]

    if not points:
        return [], (0.0, 0.0, 1.0, 1.0), 1.0

    if bounds_mm:
        min_x = float(bounds_mm["min_x"])
        min_y = float(bounds_mm["min_y"])
        max_x = float(bounds_mm["max_x"])
        max_y = float(bounds_mm["max_y"])
    else:
        min_x = min(p[0] for p in points)
        max_x = max(p[0] for p in points)
        min_y = min(p[1] for p in points)
        max_y = max(p[1] for p in points)

    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    drawable_w = max(width - (2 * margin), 1)
    drawable_h = max(height - (2 * margin), 1)

    scale = min(drawable_w / span_x, drawable_h / span_y)

    projected: list[tuple[float, float]] = []
    for x_mm, y_mm in points:
        x = margin + ((x_mm - min_x) * scale)
        y = margin + ((y_mm - min_y) * scale)
        projected.append((round(x, 3), round(y, 3)))

    return projected, (min_x, min_y, max_x, max_y), scale


def _project_outline_polygon(
    polygon_mm: list[dict[str, float]],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    margin: int,
    scale: float,
) -> list[tuple[float, float]]:
    min_x, min_y, _, _ = bounds
    projected: list[tuple[float, float]] = []
    for pt in polygon_mm:
        x = margin + ((float(pt["x"]) - min_x) * scale)
        y = margin + ((float(pt["y"]) - min_y) * scale)
        projected.append((round(x, 3), round(y, 3)))
    return projected


def _svg_for_view(view_name: str, connector_model: dict, board_model: dict | None = None) -> str:
    connector_count = int(connector_model.get("connector_count", 0))
    board_outline = (board_model or {}).get("board_outline", {})
    bounds_mm = board_outline.get("bounds_mm")
    polygons = board_outline.get("polygons", [])

    if view_name == "icon":
        width, height = 160, 100
        projected, bounds, scale = _project_connector_positions(
            connector_model,
            width,
            height,
            16,
            bounds_mm,
        )
        pins = []
        for i, (x, y) in enumerate(projected):
            pins.append(
                f'<circle id="connector{i}pin" cx="{x}" cy="{y}" r="3" fill="#455a64"/>'
            )
        silkscreen_svg = _render_svg_silkscreen(board_model, bounds, width, height, 16, scale, "#37474f")
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
            '<rect x="6" y="6" width="148" height="88" fill="#eceff1" '
            'stroke="#607d8b" stroke-width="2"/>'
            + silkscreen_svg
            + "".join(pins)
            + '</svg>'
        )

    if view_name == "breadboard":
        width, height = 260, 160
        projected, bounds, scale = _project_connector_positions(
            connector_model,
            width,
            height,
            20,
            bounds_mm,
        )
        if polygons:
            outline = _project_outline_polygon(polygons[0], bounds, width, height, 20, scale)
        else:
            outline = [(10.0, 150.0), (250.0, 150.0), (250.0, 10.0), (10.0, 10.0)]
        width, height = _tight_canvas_size(outline, (width, height), 20)
        outline_points = " ".join(f"{x},{y}" for x, y in outline)
        pad_radius = _scaled_pad_radius(scale)
        pad_stroke = round(max(0.7, pad_radius * 0.34), 3)
        pins = []
        for i, (x, y) in enumerate(projected):
            pins.append(
                f'<circle id="connector{i}pin" cx="{x}" cy="{y}" r="{pad_radius}" '
                f'fill="#ffb300" stroke="#d84315" stroke-width="{pad_stroke}"/>'
            )
        silkscreen_svg = _render_svg_silkscreen(board_model, bounds, width, height, 20, scale, "#f5f5f5")
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
            f'<polygon id="boardOutline" points="{outline_points}" fill="#2b5f82" '
            'stroke="#0f4c81" stroke-width="2"/>'
            + silkscreen_svg
            + "".join(pins)
            + '</svg>'
        )

    if view_name == "schematic":
        width, height = 240, max(140, 20 + (connector_count * 18))
        projected, _, _ = _project_connector_positions(
            connector_model,
            width,
            height,
            20,
            bounds_mm,
        )
        body_left, body_right = 40, width - 60
        pins = []
        wires = []
        for i, (_, y) in enumerate(projected):
            pin_x = body_right + 8
            wires.append(
                f'<line x1="{body_right}" y1="{y}" x2="{pin_x}" y2="{y}" '
                'stroke="#000" stroke-width="2"/>'
            )
            pins.append(
                f'<rect id="connector{i}pin" x="{pin_x}" y="{round(y - 3, 3)}" '
                'width="6" height="6" fill="#000"/>'
            )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
            f'<rect x="{body_left}" y="12" width="{body_right - body_left}" '
            f'height="{height - 24}" fill="none" stroke="#000" stroke-width="2"/>'
            + "".join(wires)
            + "".join(pins)
            + '</svg>'
        )

    width, height = 260, 160
    projected, bounds, scale = _project_connector_positions(
        connector_model,
        width,
        height,
        20,
        bounds_mm,
    )
    if polygons:
        outline = _project_outline_polygon(polygons[0], bounds, width, height, 20, scale)
    else:
        outline = [(10.0, 150.0), (250.0, 150.0), (250.0, 10.0), (10.0, 10.0)]
    width, height = _tight_canvas_size(outline, (width, height), 20)
    outline_points = " ".join(f"{x},{y}" for x, y in outline)
    pad_radius = _scaled_pad_radius(scale, min_radius=1.2, max_radius=3.2)
    pad_stroke = round(max(0.8, pad_radius * 0.5), 3)
    top_pins = []
    bottom_pins = []
    for i, (x, y) in enumerate(projected):
        pin_svg = (
            f'<circle id="connector{i}pin" cx="{x}" cy="{y}" r="{pad_radius}" fill="none" '
            f'stroke="#ff9800" stroke-width="{pad_stroke}"/>'
        )
        top_pins.append(pin_svg)
        bottom_pins.append(pin_svg)
    silkscreen_svg = _render_svg_silkscreen(board_model, bounds, width, height, 20, scale, "#f5f5f5")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<g id="board">'
        f'<polygon id="boardOutline" points="{outline_points}" fill="#2e7d32" '
        'stroke="#1b5e20" stroke-width="2"/>'
        + silkscreen_svg
        + '</g>'
        '<g id="copper1">'
        + "".join(top_pins)
        + '</g>'
        '<g id="copper0">'
        + "".join(bottom_pins)
        + '</g>'
        '</svg>'
    )


def write_placeholder_svg_views(
    connector_model: dict,
    out_dir: Path,
    board_model: dict | None = None,
) -> list[Path]:
    """Write SVG view files aligned with generated connector IDs and geometry."""
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    for view_name in ("icon", "breadboard", "schematic", "pcb"):
        path = out_dir / f"{view_name}.svg"
        path.write_text(
            _svg_for_view(view_name, connector_model, board_model),
            encoding="utf-8",
        )
        outputs.append(path)

    return outputs


def validate_generated_artifacts(
    connector_model: dict,
    out_dir: Path,
    part_basename: str = "generated_part",
) -> dict:
    """Validate connector references across generated .fzp and SVG view files."""
    safe_basename = _sanitize_part_basename(part_basename)
    fzp_file = out_dir / f"{safe_basename}.fzp"
    expected = {f"connector{i}pin" for i in range(int(connector_model.get("connector_count", 0)))}
    required_files = [
        fzp_file,
        out_dir / "breadboard.svg",
        out_dir / "schematic.svg",
        out_dir / "pcb.svg",
    ]
    missing_files = [str(p) for p in required_files if not p.exists()]

    fzp_ids: set[str] = set()
    if fzp_file.exists():
        fzp_text = fzp_file.read_text(encoding="utf-8")
        fzp_ids = set(re.findall(r'svgId="([^"]+)"', fzp_text))

    svg_ids: set[str] = set()
    for name in ("breadboard.svg", "schematic.svg", "pcb.svg"):
        path = out_dir / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            svg_ids.update(re.findall(r'id="([^"]+)"', text))

    missing_in_svg = sorted((expected | fzp_ids) - svg_ids)
    missing_in_fzp = sorted(expected - fzp_ids)

    return {
        "expected_connector_pins": sorted(expected),
        "fzp_svg_ids": sorted(fzp_ids),
        "svg_ids": sorted(svg_ids),
        "missing_files": missing_files,
        "missing_in_svg": missing_in_svg,
        "missing_in_fzp": missing_in_fzp,
        "is_valid": not missing_files and not missing_in_svg and not missing_in_fzp,
    }


def write_artifact_validation_report(report: dict, out_dir: Path) -> Path:
    """Write artifact consistency report to disk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / "artifact_validation.json"
    output_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_file


def build_fritzing_package_zip(out_dir: Path, part_basename: str = "generated_part") -> Path:
    """Build a .fzpz (Fritzing shareable part) ZIP package from generated files.
    
    A .fzpz file is a ZIP archive containing:
    - <part_basename>.fzp (part definition)
    - SVG view files (breadboard.svg, schematic.svg, pcb.svg, icon.svg)
    
    Args:
        out_dir: Output directory containing .fzp and .svg files.
    
    Returns:
        Path to generated .fzpz file.
    
    Raises:
        FileNotFoundError: If required .fzp or SVG files are missing.
    """
    safe_basename = _sanitize_part_basename(part_basename)
    fzp_file = out_dir / f"{safe_basename}.fzp"
    if not fzp_file.exists():
        raise FileNotFoundError(f"Missing {fzp_file}")
    
    required_svgs = {
        "icon": "icon.svg",
        "breadboard": "breadboard.svg",
        "schematic": "schematic.svg",
        "pcb": "pcb.svg",
    }
    missing_svgs = [svg for svg in required_svgs.values() if not (out_dir / svg).exists()]
    if missing_svgs:
        raise FileNotFoundError(f"Missing SVG files: {missing_svgs}")
    
    package_path = out_dir / f"{safe_basename}.fzpz"
    
    # Create ZIP archive with proper Fritzing package structure.
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Fritzing bundled-part loader expects these prefixes.
        zf.write(fzp_file, arcname=f"part.{safe_basename}.fzp")

        # Flatten view paths (e.g. icon/icon.svg -> svg.icon.icon.svg).
        for view_name, svg_file in required_svgs.items():
            source_svg = out_dir / svg_file
            image_path = VIEW_IMAGE_PATHS[view_name]
            archive_name = f"svg.{image_path.replace('/', '.')}"
            zf.write(source_svg, arcname=archive_name)
    
    return package_path


def export_board_to_fritzing_stub(
    board_file: Path,
    out_dir: Path,
    part_name: str | None = None,
) -> Path:
    """Create a placeholder conversion output for initial project wiring.
    
    Generates:
    - board_model.json: Intermediate representation of KiCad board
    - fritzing_connectors.json: Connector and pin mapping
    - <board-name>.fzp: Fritzing part definition (XML)
    - SVG view files: breadboard, schematic, pcb, icon
    - <board-name>.fzpz: Packaged Fritzing shareable part (ZIP archive)
    - artifact_validation.json: Consistency checks
    
    Args:
        board_file: Path to .kicad_pcb file.
        out_dir: Output directory for generated files.
        part_name: Optional override for generated part/package basename.
    
    Returns:
        Path to <board-name>.fzpz (Fritzing shareable part package).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    part_basename = _sanitize_part_basename(part_name or board_file.stem)

    model = parse_kicad_board_to_model(board_file)
    write_board_model_json(model, out_dir)
    connector_model = map_model_to_fritzing_connectors(model)
    write_fritzing_connector_model_json(connector_model, out_dir)
    write_fritzing_part_fzp(connector_model, out_dir, part_basename=part_basename)
    write_placeholder_svg_views(connector_model, out_dir, board_model=model)
    report = validate_generated_artifacts(connector_model, out_dir, part_basename=part_basename)
    write_artifact_validation_report(report, out_dir)

    # Build Fritzing shareable part package (.fzpz = ZIP archive).
    package_path = build_fritzing_package_zip(out_dir, part_basename=part_basename)

    output_file = out_dir / "README.txt"
    output_file.write_text(
        "KiCad2Fritzing conversion output\n"
        f"Source board: {board_file}\n"
        "\n"
        "Generated files:\n"
        "  Intermediate model: board_model.json\n"
        "  Connector model: fritzing_connectors.json\n"
        f"  Fritzing part definition: {part_basename}.fzp\n"
        "  SVG views: icon.svg, breadboard.svg, schematic.svg, pcb.svg\n"
        f"  Fritzing shareable part: {part_basename}.fzpz (ready to import)\n"
        "  Validation report: artifact_validation.json\n"
        "\n"
        "To import into Fritzing:\n"
        "  1. Open Fritzing\n"
        "  2. Right-click in the 'Mine' bin → Import...\n"
        f"  3. Select {part_basename}.fzpz\n"
        "\n"
        "Next step: refine SVG geometry from real board footprint data.\n",
        encoding="utf-8",
    )
    return package_path
