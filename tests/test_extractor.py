import json
from pathlib import Path

from pcb2fritzing.core.extractor import (
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
    import zipfile
    
    board_file = tmp_path / "demo.kicad_pcb"
    board_file.write_text("(kicad_pcb)", encoding="utf-8")

    out_dir = tmp_path / "result"
    output_file = export_board_to_fritzing_stub(board_file, out_dir)

    # Now returns the board-named .fzpz package file instead of README.txt
    assert output_file == out_dir / "demo.fzpz"
    assert output_file.exists()
    
    # Verify it's a valid ZIP archive
    with zipfile.ZipFile(output_file) as zf:
        names = zf.namelist()
        assert "part.demo.fzp" in names
        assert "svg.icon.icon.svg" in names
        assert "svg.breadboard.breadboard.svg" in names
        assert "svg.schematic.schematic.svg" in names
        assert "svg.pcb.pcb.svg" in names
    
    # README.txt should still exist
    readme = out_dir / "README.txt"
    assert readme.exists()
    content = readme.read_text(encoding="utf-8")
    assert "KiCad2Fritzing conversion output" in content
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


def test_parse_board_silkscreen_from_gr_text_and_footprint_poly(tmp_path: Path) -> None:
    board_file = tmp_path / "silkscreen_board.kicad_pcb"
    board_file.write_text(
        """
(kicad_pcb
    (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)) (fill none))
    (gr_text "IN"
        (at 5 5 0)
        (layer "F.SilkS")
        (effects (font (size 1.27 1.27) (thickness 0.15)))
    )
    (footprint "my:logo"
        (layer "F.Cu")
        (at 10 4 90)
        (property "Reference" "LG1" (at 0 0 0) (layer "F.Fab"))
        (property "Value" "logo" (at 0 0 0) (layer "F.Fab"))
        (fp_poly
            (pts (xy 0 0) (xy 2 0) (xy 2 1))
            (stroke (width 0.01) (type solid))
            (fill yes)
            (layer "F.SilkS")
        )
    )
)
""".strip(),
        encoding="utf-8",
    )

    model = parse_kicad_board_to_model(board_file)

    assert model["silkscreen"]["texts"][0]["text"] == "IN"
    polygon = model["silkscreen"]["polygons"][0]["points_mm"]
    assert polygon == [
        {"x": 10.0, "y": 4.0},
        {"x": 10.0, "y": 2.0},
        {"x": 11.0, "y": 2.0},
    ]


def test_component_footprint_silkscreen_is_not_exported(tmp_path: Path) -> None:
    board_file = tmp_path / "component_silk.kicad_pcb"
    board_file.write_text(
        """
(kicad_pcb
    (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)) (fill none))
    (footprint "Device:R_0805"
        (layer "F.Cu")
        (at 10 5 0)
        (property "Reference" "R1" (at 0 0 0) (layer "F.Fab"))
        (property "Value" "10k" (at 0 0 0) (layer "F.Fab"))
        (fp_line (start -1 0) (end 1 0) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
        (pad "1" smd rect (at -0.95 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        (pad "2" smd rect (at 0.95 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        )
    )
)
""".strip(),
        encoding="utf-8",
    )

    model = parse_kicad_board_to_model(board_file)

    assert model["silkscreen"]["lines"] == []


def test_component_footprint_silkscreen_is_exported_when_option_enabled(tmp_path: Path) -> None:
    board_file = tmp_path / "component_silk.kicad_pcb"
    board_file.write_text(
        """
(kicad_pcb
    (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)) (fill none))
    (footprint "Device:R_0805"
        (layer "F.Cu")
        (at 10 5 0)
        (property "Reference" "R1" (at 0 0 0) (layer "F.Fab"))
        (property "Value" "10k" (at 0 0 0) (layer "F.Fab"))
        (fp_line (start -1 0) (end 1 0) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
        (pad "1" smd rect (at -0.95 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        (pad "2" smd rect (at 0.95 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        )
    )
)
""".strip(),
        encoding="utf-8",
    )

    model = parse_kicad_board_to_model(board_file, include_component_silkscreen=True)

    assert len(model["silkscreen"]["lines"]) > 0


def test_fab_layer_is_not_exported_by_default(tmp_path: Path) -> None:
    board_file = tmp_path / "fab_test.kicad_pcb"
    board_file.write_text(
        """
(kicad_pcb
    (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)) (fill none))
    (footprint "Device:R_0805"
        (layer "F.Cu")
        (at 10 5 0)
        (fp_line (start -1.6 -0.9) (end 1.6 -0.9) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
        (fp_line (start -1.6 -0.9) (end -1.6 0.9) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
        (pad "1" smd rect (at -0.95 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        )
    )
)
""".strip(),
        encoding="utf-8",
    )

    model = parse_kicad_board_to_model(board_file)

    assert model["fab_layer"]["lines"] == []
    assert model["fab_layer"]["polygons"] == []


def test_fab_layer_is_exported_when_option_enabled(tmp_path: Path) -> None:
    board_file = tmp_path / "fab_test.kicad_pcb"
    board_file.write_text(
        """
(kicad_pcb
    (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)) (fill none))
    (footprint "Device:R_0805"
        (layer "F.Cu")
        (at 10 5 0)
        (fp_line (start -1.6 -0.9) (end 1.6 -0.9) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
        (fp_line (start -1.6 -0.9) (end -1.6 0.9) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
        (pad "1" smd rect (at -0.95 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        )
    )
)
""".strip(),
        encoding="utf-8",
    )

    model = parse_kicad_board_to_model(board_file, include_fab_layer=True)

    assert len(model["fab_layer"]["lines"]) == 2


def test_fab_layer_poly_is_exported_when_option_enabled(tmp_path: Path) -> None:
    board_file = tmp_path / "fab_poly.kicad_pcb"
    board_file.write_text(
        """
(kicad_pcb
    (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)) (fill none))
    (footprint "Device:IC_SOIC8"
        (layer "F.Cu")
        (at 10 5 0)
        (fp_poly
            (pts (xy -2.5 -2.5) (xy 2.5 -2.5) (xy 2.5 2.5) (xy -2.5 2.5))
            (stroke (width 0.1) (type solid)) (layer "F.Fab") (fill none)
        )
        (pad "1" smd rect (at -1 0) (size 0.6 1.6) (layers "F.Cu" "F.Paste" "F.Mask"))
        )
    )
)
""".strip(),
        encoding="utf-8",
    )

    model = parse_kicad_board_to_model(board_file, include_fab_layer=True)

    assert len(model["fab_layer"]["polygons"]) == 1
    assert len(model["fab_layer"]["polygons"][0]["points_mm"]) == 4


def test_fab_layer_not_in_silkscreen_when_enabled(tmp_path: Path) -> None:
    board_file = tmp_path / "fab_isolation.kicad_pcb"
    board_file.write_text(
        """
(kicad_pcb
    (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)) (fill none))
    (footprint "Device:R_0805"
        (layer "F.Cu")
        (at 10 5 0)
        (fp_line (start -1.6 -0.9) (end 1.6 -0.9) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
        (fp_line (start -1 0) (end 1 0) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
        (pad "1" smd rect (at -0.95 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        )
    )
)
""".strip(),
        encoding="utf-8",
    )

    model = parse_kicad_board_to_model(
        board_file, include_component_silkscreen=True, include_fab_layer=True
    )

    assert len(model["silkscreen"]["lines"]) == 1
    assert len(model["fab_layer"]["lines"]) == 1


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

    assert connector_model["connector_count"] == 2
    connector_by_id = {
        c["id"]: c for c in connector_model["connectors"]
    }

    assert connector_by_id["J1_pad1"]["net"] == "V+"
    assert connector_by_id["J1_pad2"]["net"] == "GND"
    assert connector_by_id["J1_pad1"]["position_mm"] == {"x": 120.0, "y": 100.0}
    assert connector_by_id["J1_pad2"]["position_mm"] == {"x": 120.0, "y": 102.54}
    assert connector_by_id["J1_pad1"]["header_label"] == "J1"
    assert connector_by_id["J1_pad1"]["name"] == "V+"
    assert connector_by_id["J1_pad2"]["name"] == "GND"

    roles = {c["role"] for c in connector_model["connectors"]}
    assert roles == {"power"}


def test_map_model_to_fritzing_connectors_includes_custom_j_reference_connectors() -> None:
    model = {
        "source_board": "demo.kicad_pcb",
        "footprints": [
            {
                "reference": "J4",
                "value": "A_INA219",
                "footprint": "lps-1-Footprints:Adafruit-INA219_2xM2.5",
                "at": [10.0, 20.0, 0.0],
                "silkscreen_user_labels": [],
                "pads": [
                    {"pad": "1", "net": "GND", "pinfunction": "", "at": [0.0, 0.0, 0.0]},
                    {"pad": "2", "net": "VCC", "pinfunction": "", "at": [2.54, 0.0, 0.0]},
                ],
            }
        ],
    }

    connector_model = map_model_to_fritzing_connectors(model)

    assert connector_model["connector_count"] == 2
    ids = {c["id"] for c in connector_model["connectors"]}
    assert ids == {"J4_pad1", "J4_pad2"}


def test_parse_gr_text_preserves_justify_alignment(tmp_path: Path) -> None:
    board_file = tmp_path / "silk_justify.kicad_pcb"
    board_file.write_text(
        """
(kicad_pcb
    (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)) (fill none))
    (gr_text "Vcc"
        (at 12 7 0)
        (layer "F.SilkS")
        (effects
            (font (size 0.762 1.016) (thickness 0.1524) (bold yes))
            (justify right bottom)
        )
    )
)
""".strip(),
        encoding="utf-8",
    )

    model = parse_kicad_board_to_model(board_file)
    text_item = model["silkscreen"]["texts"][0]

    assert text_item["text"] == "Vcc"
    assert text_item["h_align"] == "right"
    assert text_item["v_align"] == "bottom"


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
    assert connector_model["connector_count"] == 2


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


def test_build_fritzing_part_fzp_includes_default_family_and_type() -> None:
    connector_model = {
        "source_board": "demo.kicad_pcb",
        "connector_count": 0,
        "connectors": [],
    }

    fzp = build_fritzing_part_fzp(connector_model)

    assert '<property name="family">KiCad2Fritzing Generated</property>' in fzp
    assert '<property name="type">Custom PCB</property>' in fzp


def test_build_fritzing_part_fzp_allows_family_and_type_overrides() -> None:
    connector_model = {
        "source_board": "demo.kicad_pcb",
        "connector_count": 0,
        "connectors": [],
    }

    fzp = build_fritzing_part_fzp(
        connector_model,
        part_family="Sensor Board",
        part_type="Current Monitor",
    )

    assert '<property name="family">Sensor Board</property>' in fzp
    assert '<property name="type">Current Monitor</property>' in fzp


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
    assert "kicad2fritzing.generated_part" in content
    assert "Pin_1" in content


def test_export_board_to_fritzing_stub_creates_fzp_file(tmp_path: Path) -> None:
    board_file = Path(
        "references/kicad-projects/basic-led-power/basic-led-power.kicad_pcb"
    )
    export_board_to_fritzing_stub(board_file, tmp_path)

    part_file = tmp_path / "basic-led-power.fzp"
    assert part_file.exists()

    content = part_file.read_text(encoding="utf-8")
    assert "<module" in content
    assert "<connectors>" in content


def test_export_board_to_fritzing_stub_part_name_override(tmp_path: Path) -> None:
    board_file = tmp_path / "demo-board.kicad_pcb"
    board_file.write_text("(kicad_pcb)", encoding="utf-8")

    output = export_board_to_fritzing_stub(board_file, tmp_path, part_name="My Custom Part")

    assert output == tmp_path / "My_Custom_Part.fzpz"
    assert (tmp_path / "My_Custom_Part.fzp").exists()


def test_export_board_to_fritzing_stub_writes_family_and_type_overrides(tmp_path: Path) -> None:
    board_file = tmp_path / "demo-board.kicad_pcb"
    board_file.write_text("(kicad_pcb)", encoding="utf-8")

    export_board_to_fritzing_stub(
        board_file,
        tmp_path,
        part_name="My Custom Part",
        part_family="Power Module",
        part_type="Buck Regulator",
    )

    content = (tmp_path / "My_Custom_Part.fzp").read_text(encoding="utf-8")
    assert '<property name="family">Power Module</property>' in content
    assert '<property name="type">Buck Regulator</property>' in content


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
        },
        "silkscreen": {
            "texts": [
                {"text": "IN", "x_mm": 5.0, "y_mm": 6.0, "rotation_deg": 0.0, "size_mm": 1.27}
            ],
            "lines": [],
            "polygons": [],
        },
    }

    outputs = write_placeholder_svg_views(connector_model, tmp_path, board_model=board_model)

    assert len(outputs) == 4
    for name in ("icon.svg", "breadboard.svg", "schematic.svg", "pcb.svg"):
        assert (tmp_path / name).exists()

    breadboard = (tmp_path / "breadboard.svg").read_text(encoding="utf-8")
    assert 'id="connector0pin"' in breadboard
    assert 'id="connector1pin"' in breadboard
    assert 'id="boardOutline"' in breadboard
    assert '>IN<' in breadboard
    assert 'x="47.5" y="53.0"' in breadboard


def test_write_placeholder_svg_views_crops_square_board_width(tmp_path: Path) -> None:
    connector_model = {
        "source_board": "demo.kicad_pcb",
        "connector_count": 1,
        "connectors": [
            {"id": "J1_pad1", "name": "Pin_1", "position_mm": {"x": 10.0, "y": 10.0}},
        ],
    }

    board_model = {
        "board_outline": {
            "polygons": [
                [
                    {"x": 0.0, "y": 0.0},
                    {"x": 20.0, "y": 0.0},
                    {"x": 20.0, "y": 20.0},
                    {"x": 0.0, "y": 20.0},
                ]
            ],
            "bounds_mm": {"min_x": 0.0, "min_y": 0.0, "max_x": 20.0, "max_y": 20.0},
        },
        "silkscreen": {"texts": [], "lines": [], "polygons": []},
    }

    write_placeholder_svg_views(connector_model, tmp_path, board_model=board_model)

    breadboard = (tmp_path / "breadboard.svg").read_text(encoding="utf-8")
    assert 'width="160" height="160"' in breadboard


def test_write_placeholder_svg_views_applies_render_options(tmp_path: Path) -> None:
    connector_model = {
        "source_board": "demo.kicad_pcb",
        "connector_count": 1,
        "connectors": [
            {"id": "J1_pad1", "name": "Pin_1", "position_mm": {"x": 10.0, "y": 10.0}},
        ],
    }

    board_model = {
        "board_outline": {
            "polygons": [
                [
                    {"x": 0.0, "y": 0.0},
                    {"x": 20.0, "y": 0.0},
                    {"x": 20.0, "y": 20.0},
                    {"x": 0.0, "y": 20.0},
                ]
            ],
            "bounds_mm": {"min_x": 0.0, "min_y": 0.0, "max_x": 20.0, "max_y": 20.0},
        },
        "silkscreen": {
            "texts": [
                {"text": "IN", "x_mm": 5.0, "y_mm": 6.0, "rotation_deg": 0.0, "size_mm": 1.27}
            ],
            "lines": [],
            "polygons": [],
        },
    }

    write_placeholder_svg_views(
        connector_model,
        tmp_path,
        board_model=board_model,
        render_options={
            "soldermask_color": "#123456",
            "silkscreen_color": "#ddeeff",
            "annular_color": "#00aa55",
            "hole_color": "#2244cc",
            "pad_scale": 0.5,
            "silk_text_scale": 1.2,
        },
    )

    breadboard = (tmp_path / "breadboard.svg").read_text(encoding="utf-8")
    pcb = (tmp_path / "pcb.svg").read_text(encoding="utf-8")

    assert 'fill="#123456"' in breadboard
    assert 'fill="#123456"' in pcb
    assert 'fill="#ddeeff"' in breadboard
    assert 'r="1.9" fill="#2244cc"' in breadboard
    assert 'stroke="#00aa55"' in breadboard
    assert 'stroke="#00aa55"' in pcb


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


def test_pin_header_uses_silkscreen_user_label_for_fallback_name(tmp_path: Path) -> None:
    board_file = tmp_path / "header_label.kicad_pcb"
    board_file.write_text(
        """
(kicad_pcb
  (footprint "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
    (layer "F.Cu")
    (at 10 10 0)
    (property "Reference" "P1" (at 0 -2 0) (layer "F.SilkS"))
    (property "Value" "CONN_01X02" (at 0 2 0) (layer "F.Fab"))
    (fp_text user "PWR" (at 0 -4 0) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15)))
    )
    (pad "1" thru_hole rect (at 0 0) (size 1.5 1.5) (drill 0.8) (layers "*.Cu" "*.Mask"))
    (pad "2" thru_hole oval (at 0 2.54) (size 1.5 1.5) (drill 0.8) (layers "*.Cu" "*.Mask"))
  )
)
""".strip(),
        encoding="utf-8",
    )

    model = parse_kicad_board_to_model(board_file)
    connector_model = map_model_to_fritzing_connectors(model)

    assert connector_model["connector_count"] == 2
    assert connector_model["connectors"][0]["header_label"] == "PWR"
    assert connector_model["connectors"][0]["name"] == "PWR_1"
    assert connector_model["connectors"][1]["name"] == "PWR_2"
