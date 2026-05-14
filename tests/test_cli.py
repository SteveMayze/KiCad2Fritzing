from pathlib import Path

from pcb2fritzing import cli


def test_cli_main_generates_output(monkeypatch, tmp_path: Path) -> None:
    board_file = tmp_path / "board.kicad_pcb"
    board_file.write_text("(kicad_pcb)", encoding="utf-8")

    out_dir = tmp_path / "fritzing-out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pcb2fritzing",
            str(board_file),
            "--out-dir",
            str(out_dir),
        ],
    )

    exit_code = cli.main()

    assert exit_code == 0
    assert (out_dir / "README.txt").exists()


def test_cli_main_generates_output_with_part_name(monkeypatch, tmp_path: Path) -> None:
    board_file = tmp_path / "board.kicad_pcb"
    board_file.write_text("(kicad_pcb)", encoding="utf-8")

    out_dir = tmp_path / "fritzing-out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pcb2fritzing",
            str(board_file),
            "--out-dir",
            str(out_dir),
            "--part-name",
            "custom-board-part",
        ],
    )

    exit_code = cli.main()

    assert exit_code == 0
    assert (out_dir / "custom-board-part.fzpz").exists()


def test_cli_parser_defaults() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["board.kicad_pcb"])

    assert args.board_file == Path("board.kicad_pcb")
    assert args.out_dir == Path("build/fritzing-part")
    assert args.part_name is None
    assert args.verbose is False


def test_cli_parser_part_name() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["board.kicad_pcb", "--part-name", "my-board"])

    assert args.part_name == "my-board"
