import json
from pathlib import Path

from kicad2fritzing.core.extractor import (
    build_fritzing_part_fzp,
    export_board_to_fritzing_stub,
    map_model_to_fritzing_connectors,
    parse_kicad_board_to_model,
    validate_generated_artifacts,
    write_artifact_validation_report,
    write_board_model_json,
    write_fritzing_connector_model_json,
    write_fritzing_part_fzp,
    write_placeholder_svg_views,
)


def test_export_board_to_fritzing_stub_creates_readme(tmp_path: Path) -> None:
    board_file = tmp_path / "demo.kicad_pcb"
    board_file.write_text("(kicad_pcb)", encoding="utf-8")

    out_dir = tmp_path / "result"
    output_file = export_board_to_fritzing_stub(board_file, out_dir)

    assert output_file == out_dir / "README.txt"
    assert output_file.exists()

    content = output_file.read_text(encoding="utf-8")
    assert "KiCad2Fritzing placeholder output" in content
    assert f"Source board: {board_file}" in content


def test_write_board_model_json_creates_file(tmp_path: Path) -> None:
    model = {
        "source_board": "demo.kicad_pcb",
        "nets": [{"name": "GND"}, {"name": "V+"}],
        "footprints": [],
    }

    output = write_board_model_json(model, tmp_path)

    assert output == tmp_path / "board_model.json"
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["source_board"] == "demo.kicad_pcb"
    assert {n["name"] for n in loaded["nets"]} == {"GND", "V+"}


def test_parse_kicad_board_to_model_on_basic_fixture() -> None:
    board_file = Path(
        "references/kicad-projects/basic-led-power/basic-led-power.kicad_pcb"
    )

    model = parse_kicad_board_to_model(board_file)

    assert model["source_board"].endswith("basic-led-power.kicad_pcb")

    net_names = {n["name"] for n in model["nets"]}
    assert "V+" in net_names
    assert "GND" in net_names

    footprints_by_ref = {fp["reference"]: fp for fp in model["footprints"]}
    assert "J1" in footprints_by_ref
    assert "D1" in footprints_by_ref

    j1_pads = {pad["pad"]: pad["net"] for pad in footprints_by_ref["J1"]["pads"]}
    d1_pads = {pad["pad"]: pad["net"] for pad in footprints_by_ref["D1"]["pads"]}

    assert j1_pads["1"] == "V+"
    assert j1_pads["2"] == "GND"
    assert d1_pads["1"] == "V+"
    assert d1_pads["2"] == "GND"

    j1_pad2 = next(p for p in footprints_by_ref["J1"]["pads"] if p["pad"] == "2")
    d1_pad2 = next(p for p in footprints_by_ref["D1"]["pads"] if p["pad"] == "2")
    assert j1_pad2["at"] == [0.0, 2.54, 0.0]
    assert d1_pad2["at"][0] > 2.5

    outline = model["board_outline"]
    assert outline["bounds_mm"] == {
        "min_x": 114.0,
        "min_y": 94.0,
        "max_x": 160.0,
        "max_y": 110.0,
    }
    assert len(outline["polygons"]) == 1
    assert len(outline["polygons"][0]) == 4


def test_export_board_to_fritzing_stub_creates_intermediate_model(tmp_path: Path) -> None:
    board_file = Path(
        "references/kicad-projects/basic-led-power/basic-led-power.kicad_pcb"
    )

    export_board_to_fritzing_stub(board_file, tmp_path)

    model_file = tmp_path / "board_model.json"
    assert model_file.exists()

    model = json.loads(model_file.read_text(encoding="utf-8"))
    assert {n["name"] for n in model["nets"]} == {"GND", "V+"}


def test_map_model_to_fritzing_connectors_on_fixture() -> None:
    board_file = Path(
        "references/kicad-projects/basic-led-power/basic-led-power.kicad_pcb"
    )
    model = parse_kicad_board_to_model(board_file)

    connector_model = map_model_to_fritzing_connectors(model)

    assert connector_model["connector_count"] == 4
    connector_by_id = {
        c["id"]: c for c in connector_model["connectors"]
    }

    assert connector_by_id["J1_pad1"]["net"] == "V+"
    assert connector_by_id["J1_pad2"]["net"] == "GND"
    assert connector_by_id["D1_pad1"]["pinfunction"] == "K"
    assert connector_by_id["D1_pad2"]["pinfunction"] == "A"
    assert connector_by_id["J1_pad1"]["position_mm"] == {"x": 120.0, "y": 100.0}
    assert connector_by_id["J1_pad2"]["position_mm"] == {"x": 120.0, "y": 102.54}

    roles = {c["role"] for c in connector_model["connectors"]}
    assert roles == {"power"}


def test_write_fritzing_connector_model_json(tmp_path: Path) -> None:
    connector_model = {
        "source_board": "demo.kicad_pcb",
        "connector_count": 1,
        "connectors": [
            {
                "id": "J1_pad1",
                "footprint_reference": "J1",
                "footprint_name": "Connector",
                "pad": "1",
                "name": "Pin_1",
                "net": "V+",
                "pinfunction": "Pin_1",
                "role": "power",
            }
        ],
    }

    output = write_fritzing_connector_model_json(connector_model, tmp_path)

    assert output == tmp_path / "fritzing_connectors.json"
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["connector_count"] == 1
    assert loaded["connectors"][0]["id"] == "J1_pad1"


def test_export_board_to_fritzing_stub_creates_connector_model(tmp_path: Path) -> None:
    board_file = Path(
        "references/kicad-projects/basic-led-power/basic-led-power.kicad_pcb"
    )
    export_board_to_fritzing_stub(board_file, tmp_path)

    connector_file = tmp_path / "fritzing_connectors.json"
    assert connector_file.exists()

    connector_model = json.loads(connector_file.read_text(encoding="utf-8"))
    assert connector_model["connector_count"] == 4


def test_build_fritzing_part_fzp_contains_expected_connectors() -> None:
    connector_model = {
        "source_board": "demo.kicad_pcb",
        "connector_count": 2,
        "connectors": [
            {
                "id": "J1_pad1",
                "footprint_reference": "J1",
                "pad": "1",
                "name": "Pin_1",
            },
            {
                "id": "J1_pad2",
                "footprint_reference": "J1",
                "pad": "2",
                "name": "Pin_2",
            },
        ],
    }

    fzp = build_fritzing_part_fzp(connector_model)

    assert "<module" in fzp
    assert "<connectors>" in fzp
    assert 'id="connector0"' in fzp
    assert 'id="connector1"' in fzp
    assert "Pin_1" in fzp
    assert "Pin_2" in fzp


def test_write_fritzing_part_fzp(tmp_path: Path) -> None:
    connector_model = {
        "source_board": "demo.kicad_pcb",
        "connector_count": 1,
        "connectors": [
            {
                "id": "J1_pad1",
                "footprint_reference": "J1",
                "pad": "1",
                "name": "Pin_1",
            }
        ],
    }

    output_file = write_fritzing_part_fzp(connector_model, tmp_path)

    assert output_file == tmp_path / "generated_part.fzp"
    content = output_file.read_text(encoding="utf-8")
    assert "kicad2fritzing.generated.part" in content
    assert "Pin_1" in content


def test_export_board_to_fritzing_stub_creates_fzp_file(tmp_path: Path) -> None:
    board_file = Path(
        "references/kicad-projects/basic-led-power/basic-led-power.kicad_pcb"
    )
    export_board_to_fritzing_stub(board_file, tmp_path)

    part_file = tmp_path / "generated_part.fzp"
    assert part_file.exists()

    content = part_file.read_text(encoding="utf-8")
    assert "<module" in content
    assert "<connectors>" in content


def test_write_placeholder_svg_views(tmp_path: Path) -> None:
    connector_model = {
        "source_board": "demo.kicad_pcb",
        "connector_count": 2,
        "connectors": [
            {"id": "J1_pad1", "name": "Pin_1"},
            {"id": "J1_pad2", "name": "Pin_2"},
        ],
    }

    board_model = {
        "board_outline": {
            "polygons": [
                [
                    {"x": 0.0, "y": 0.0},
                    {"x": 40.0, "y": 0.0},
                    {"x": 40.0, "y": 20.0},
                    {"x": 0.0, "y": 20.0},
                ]
            ],
            "bounds_mm": {"min_x": 0.0, "min_y": 0.0, "max_x": 40.0, "max_y": 20.0},
        }
    }

    outputs = write_placeholder_svg_views(connector_model, tmp_path, board_model=board_model)

    assert len(outputs) == 4
    for name in ("icon.svg", "breadboard.svg", "schematic.svg", "pcb.svg"):
        assert (tmp_path / name).exists()

    breadboard = (tmp_path / "breadboard.svg").read_text(encoding="utf-8")
    assert 'id="connector0pin"' in breadboard
    assert 'id="connector1pin"' in breadboard
    assert 'id="boardOutline"' in breadboard


def test_validate_generated_artifacts_success(tmp_path: Path) -> None:
    connector_model = {
        "source_board": "demo.kicad_pcb",
        "connector_count": 2,
        "connectors": [
            {"id": "J1_pad1", "name": "Pin_1"},
            {"id": "J1_pad2", "name": "Pin_2"},
        ],
    }
    write_fritzing_part_fzp(connector_model, tmp_path)
    write_placeholder_svg_views(connector_model, tmp_path, board_model={"board_outline": {"polygons": [], "bounds_mm": None}})

    report = validate_generated_artifacts(connector_model, tmp_path)
    assert report["is_valid"] is True
    assert report["missing_files"] == []
    assert report["missing_in_fzp"] == []
    assert report["missing_in_svg"] == []


def test_write_artifact_validation_report(tmp_path: Path) -> None:
    report = {
        "expected_connector_pins": ["connector0pin"],
        "fzp_svg_ids": ["connector0pin"],
        "svg_ids": ["connector0pin"],
        "missing_files": [],
        "missing_in_svg": [],
        "missing_in_fzp": [],
        "is_valid": True,
    }
    output = write_artifact_validation_report(report, tmp_path)
    assert output == tmp_path / "artifact_validation.json"

    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["is_valid"] is True


def test_export_board_to_fritzing_stub_creates_svg_and_validation_files(tmp_path: Path) -> None:
    board_file = Path(
        "references/kicad-projects/basic-led-power/basic-led-power.kicad_pcb"
    )
    export_board_to_fritzing_stub(board_file, tmp_path)

    for name in (
        "icon.svg",
        "breadboard.svg",
        "schematic.svg",
        "pcb.svg",
        "artifact_validation.json",
    ):
        assert (tmp_path / name).exists()

    report = json.loads((tmp_path / "artifact_validation.json").read_text(encoding="utf-8"))
    assert report["is_valid"] is True


def test_parse_board_outline_from_edge_cuts_gr_line(tmp_path: Path) -> None:
        board_file = tmp_path / "line_outline.kicad_pcb"
        board_file.write_text(
                """
(kicad_pcb
    (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)))
    (gr_line (start 40 0) (end 40 20) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)))
    (gr_line (start 40 20) (end 0 20) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)))
    (gr_line (start 0 20) (end 0 0) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)))
)
""".strip(),
                encoding="utf-8",
        )

        model = parse_kicad_board_to_model(board_file)
        outline = model["board_outline"]

        assert outline["bounds_mm"] == {
                "min_x": 0.0,
                "min_y": 0.0,
                "max_x": 40.0,
                "max_y": 20.0,
        }
        assert len(outline["polygons"]) == 1
        assert len(outline["polygons"][0]) >= 4


def test_parse_board_outline_from_edge_cuts_gr_poly(tmp_path: Path) -> None:
        board_file = tmp_path / "poly_outline.kicad_pcb"
        board_file.write_text(
                """
(kicad_pcb
    (gr_poly
        (pts
            (xy 1 1)
            (xy 10 1)
            (xy 12 5)
            (xy 1 6)
        )
        (layer "Edge.Cuts")
        (stroke (width 0.1) (type solid))
        (fill none)
    )
)
""".strip(),
                encoding="utf-8",
        )

        model = parse_kicad_board_to_model(board_file)
        outline = model["board_outline"]

        assert outline["bounds_mm"] == {
                "min_x": 1.0,
                "min_y": 1.0,
                "max_x": 12.0,
                "max_y": 6.0,
        }
        assert len(outline["polygons"]) == 1
        assert len(outline["polygons"][0]) == 4


def test_parse_board_outline_from_edge_cuts_gr_arc(tmp_path: Path) -> None:
        board_file = tmp_path / "arc_outline.kicad_pcb"
        board_file.write_text(
                """
(kicad_pcb
    (gr_arc
        (start 0 0)
        (mid 5 5)
        (end 10 0)
        (layer "Edge.Cuts")
        (stroke (width 0.1) (type solid))
    )
)
""".strip(),
                encoding="utf-8",
        )

        model = parse_kicad_board_to_model(board_file)
        outline = model["board_outline"]
        assert outline["bounds_mm"] is not None
        assert outline["bounds_mm"]["min_x"] <= 0.0
        assert outline["bounds_mm"]["max_x"] >= 10.0
        assert outline["bounds_mm"]["max_y"] >= 5.0
