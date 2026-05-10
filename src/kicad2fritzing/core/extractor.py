"""Extraction primitives for converting KiCad data into Fritzing artifacts."""

from __future__ import annotations

import json
import math
import re
from xml.etree import ElementTree as ET
from pathlib import Path


FOOTPRINT_START_RE = re.compile(r'^\(footprint\s+"([^"]+)"')
GR_RECT_START_RE = re.compile(r'^\(gr_rect\b')
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
START_RE = re.compile(r'\(start\s+([-\d.]+)\s+([-\d.]+)\)')
MID_RE = re.compile(r'\(mid\s+([-\d.]+)\s+([-\d.]+)\)')
END_RE = re.compile(r'\(end\s+([-\d.]+)\s+([-\d.]+)\)')
XY_RE = re.compile(r'\(xy\s+([-\d.]+)\s+([-\d.]+)\)')
LAYER_RE = re.compile(r'\(layer\s+"([^"]+)"\)')
POWER_NET_HINTS = {"V+", "VCC", "VIN", "5V", "3V3", "3.3V", "GND"}
POWER_NET_HINTS_UPPER = {n.upper() for n in POWER_NET_HINTS}


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


def parse_kicad_board_to_model(board_file: Path) -> dict:
    """Parse a KiCad PCB file into a small intermediate model for conversion."""
    text = board_file.read_text(encoding="utf-8")
    lines = text.splitlines()

    footprints: list[dict] = []
    nets: set[str] = set()
    board_outline_polygons: list[list[dict[str, float]]] = []
    board_outline_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []

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
            "pads": [],
        }

        fp_line_index = 0
        while fp_line_index < len(fp_block):
            fp_line = fp_block[fp_line_index].strip()

            prop_match = PROPERTY_RE.match(fp_line)
            if prop_match:
                key, value = prop_match.group(1), prop_match.group(2)
                if key == "Reference":
                    footprint["reference"] = value
                elif key == "Value":
                    footprint["value"] = value

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

            fp_line_index += 1

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
    }
    return model


def write_board_model_json(model: dict, out_dir: Path) -> Path:
    """Write intermediate board model JSON to disk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / "board_model.json"
    output_file.write_text(json.dumps(model, indent=2), encoding="utf-8")
    return output_file


def map_model_to_fritzing_connectors(model: dict) -> dict:
    """Map parsed board model into a starter Fritzing connector model."""
    connectors: list[dict] = []

    for footprint in model.get("footprints", []):
        reference = footprint.get("reference", "")
        fp_at = footprint.get("at", [0.0, 0.0, 0.0])
        fp_x, fp_y, fp_rot = float(fp_at[0]), float(fp_at[1]), float(fp_at[2])
        for pad in footprint.get("pads", []):
            net_name = pad.get("net", "")
            pinfunction = pad.get("pinfunction", "")
            pad_at = pad.get("at", [0.0, 0.0, 0.0])
            pad_x, pad_y = float(pad_at[0]), float(pad_at[1])
            rotated_x, rotated_y = _rotate_point(pad_x, pad_y, fp_rot)
            connector_role = (
                "power" if net_name.upper() in POWER_NET_HINTS_UPPER else "signal"
            )
            abs_x = fp_x + rotated_x
            abs_y = fp_y + rotated_y
            connectors.append(
                {
                    "id": f"{reference}_pad{pad.get('pad', '')}",
                    "footprint_reference": reference,
                    "footprint_name": footprint.get("footprint", ""),
                    "pad": pad.get("pad", ""),
                    "name": pinfunction or net_name or f"{reference}_{pad.get('pad', '')}",
                    "net": net_name,
                    "pinfunction": pinfunction,
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


def build_fritzing_part_fzp(connector_model: dict) -> str:
    """Build a minimal Fritzing part XML document from connector model data."""
    module = ET.Element(
        "module",
        {
            "fritzingVersion": "0.9.3b",
            "moduleId": "kicad2fritzing.generated.part",
        },
    )
    ET.SubElement(module, "version").text = "0.1"
    ET.SubElement(module, "author").text = "KiCad2Fritzing"
    ET.SubElement(module, "title").text = "KiCad2Fritzing Generated Part"
    ET.SubElement(module, "label").text = "KiCad2Fritzing Part"
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
    for view_name, layer_id, image_name in (
        ("iconView", "icon", "icon.svg"),
        ("breadboardView", "breadboard", "breadboard.svg"),
        ("schematicView", "schematic", "schematic.svg"),
        ("pcbView", "copper0", "pcb.svg"),
    ):
        view_elem = ET.SubElement(views, view_name)
        layers = ET.SubElement(view_elem, "layers", {"image": image_name})
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
            ("pcbView", "copper0"),
        ):
            view_ref = ET.SubElement(connector_views, view_name)
            ET.SubElement(view_ref, "p", {"layer": layer, "svgId": svg_pin_id})

    return ET.tostring(module, encoding="unicode")


def write_fritzing_part_fzp(connector_model: dict, out_dir: Path) -> Path:
    """Write a minimal generated Fritzing .fzp part file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / "generated_part.fzp"
    fzp_xml = build_fritzing_part_fzp(connector_model)
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
        # Flip Y so larger KiCad Y appears lower on screen.
        y = height - margin - ((y_mm - min_y) * scale)
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
        y = height - margin - ((float(pt["y"]) - min_y) * scale)
        projected.append((round(x, 3), round(y, 3)))
    return projected


def _svg_for_view(view_name: str, connector_model: dict, board_model: dict | None = None) -> str:
    connector_count = int(connector_model.get("connector_count", 0))
    board_outline = (board_model or {}).get("board_outline", {})
    bounds_mm = board_outline.get("bounds_mm")
    polygons = board_outline.get("polygons", [])

    if view_name == "icon":
        width, height = 160, 100
        projected, _, _ = _project_connector_positions(
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
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
            '<rect x="6" y="6" width="148" height="88" fill="#eceff1" '
            'stroke="#607d8b" stroke-width="2"/>'
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
        outline_points = " ".join(f"{x},{y}" for x, y in outline)
        pins = []
        labels = []
        for i, (x, y) in enumerate(projected):
            pins.append(
                f'<circle id="connector{i}pin" cx="{x}" cy="{y}" r="5" '
                'fill="#ffd54f" stroke="#8d6e63" stroke-width="1.2"/>'
            )
            labels.append(
                f'<text x="{x + 6}" y="{y - 6}" font-size="8" fill="#263238">{i}</text>'
            )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
            f'<polygon id="boardOutline" points="{outline_points}" fill="#90caf9" '
            'stroke="#1565c0" stroke-width="2"/>'
            + "".join(pins)
            + "".join(labels)
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
    outline_points = " ".join(f"{x},{y}" for x, y in outline)
    pins = []
    for i, (x, y) in enumerate(projected):
        pins.append(
            f'<circle id="connector{i}pin" cx="{x}" cy="{y}" r="4" fill="none" '
            'stroke="#ff9800" stroke-width="2"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<g id="board">'
        f'<polygon id="boardOutline" points="{outline_points}" fill="#2e7d32" '
        'stroke="#1b5e20" stroke-width="2"/>'
        '</g>'
        '<g id="copper0">'
        + "".join(pins)
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


def validate_generated_artifacts(connector_model: dict, out_dir: Path) -> dict:
    """Validate connector references across generated .fzp and SVG view files."""
    expected = {f"connector{i}pin" for i in range(int(connector_model.get("connector_count", 0)))}
    required_files = [
        out_dir / "generated_part.fzp",
        out_dir / "breadboard.svg",
        out_dir / "schematic.svg",
        out_dir / "pcb.svg",
    ]
    missing_files = [str(p) for p in required_files if not p.exists()]

    fzp_ids: set[str] = set()
    if (out_dir / "generated_part.fzp").exists():
        fzp_text = (out_dir / "generated_part.fzp").read_text(encoding="utf-8")
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


def export_board_to_fritzing_stub(board_file: Path, out_dir: Path) -> Path:
    """Create a placeholder conversion output for initial project wiring."""
    out_dir.mkdir(parents=True, exist_ok=True)

    model = parse_kicad_board_to_model(board_file)
    write_board_model_json(model, out_dir)
    connector_model = map_model_to_fritzing_connectors(model)
    write_fritzing_connector_model_json(connector_model, out_dir)
    write_fritzing_part_fzp(connector_model, out_dir)
    write_placeholder_svg_views(connector_model, out_dir, board_model=model)
    report = validate_generated_artifacts(connector_model, out_dir)
    write_artifact_validation_report(report, out_dir)

    output_file = out_dir / "README.txt"
    output_file.write_text(
        "KiCad2Fritzing placeholder output\n"
        f"Source board: {board_file}\n"
        "\n"
        "Intermediate model: board_model.json\n"
        "Connector model: fritzing_connectors.json\n"
        "Generated part: generated_part.fzp\n"
        "Generated SVG views: icon.svg, breadboard.svg, schematic.svg, pcb.svg\n"
        "Artifact validation: artifact_validation.json\n"
        "Next step: refine SVG geometry from real board footprint data.\n",
        encoding="utf-8",
    )
    return output_file
