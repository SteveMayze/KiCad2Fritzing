"""Command-line entry point for KiCad2Fritzing."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from kicad2fritzing.core.extractor import export_board_to_fritzing_stub


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kicad2fritzing",
        description="Generate Fritzing-friendly outputs from KiCad board layouts.",
    )
    parser.add_argument("board_file", type=Path, help="Path to a KiCad .kicad_pcb file")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("build/fritzing-part"),
        help="Output folder for generated Fritzing artifacts",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    output_file = export_board_to_fritzing_stub(args.board_file, args.out_dir)
    logging.info("Wrote placeholder output: %s", output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
