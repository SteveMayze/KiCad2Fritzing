"""Command-line entry point for pcb2fritzing."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pcb2fritzing.core.extractor import build_fritzing_package_zip, export_board_to_fritzing_stub
from pcb2fritzing.kicad.plugin import embed_3d_render_in_breadboard_svg, render_board_3d


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcb2fritzing",
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
        "--part-name",
        type=str,
        default=None,
        help="Optional output part base name (defaults to board filename)",
    )
    parser.add_argument(
        "--include-component-silkscreen",
        action="store_true",
        help="Include component footprint silkscreen (outlines and labels) in generated SVG views (mainly for non-3D exports)",
    )
    parser.add_argument(
        "--include-fab-layer",
        action="store_true",
        help="Include component body layer (F.Fab) outlines in generated SVG views (mainly for non-3D exports)",
    )
    parser.add_argument(
        "--render-3d",
        action="store_true",
        help="Embed a 3D render into the breadboard SVG (requires kicad-cli)",
    )
    parser.add_argument(
        "--kicad-cli-path",
        type=str,
        default=None,
        help="Path to kicad-cli executable (auto-detected when omitted)",
    )
    parser.add_argument(
        "--soldermask-color",
        type=str,
        default=None,
        metavar="COLOR",
        help="Soldermask hex colour for the 3D render, e.g. #2b5f82 (uses board default when omitted)",
    )
    parser.add_argument(
        "--silkscreen-color",
        type=str,
        default=None,
        metavar="COLOR",
        help="Silkscreen hex colour for the 3D render, e.g. #f5f5f5 (uses board default when omitted)",
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

    output_file = export_board_to_fritzing_stub(
        args.board_file,
        args.out_dir,
        part_name=args.part_name,
        render_options={
            "include_component_silkscreen": args.include_component_silkscreen,
            "include_fab_layer": args.include_fab_layer,
        },
    )
    logging.info("Wrote placeholder output: %s", output_file)

    if args.render_3d:
        import json
        board_bounds_mm = None
        model_json_path = args.out_dir / "board_model.json"
        if model_json_path.exists():
            model_data = json.loads(model_json_path.read_text(encoding="utf-8"))
            board_bounds_mm = model_data.get("board_outline", {}).get("bounds_mm")
        render_diagnostics: list[str] = []
        render_png = render_board_3d(
            args.board_file,
            args.out_dir / "kicad_svg_plots",
            board_bounds_mm=board_bounds_mm,
            kicad_cli_path=args.kicad_cli_path,
            soldermask_color=args.soldermask_color,
            silkscreen_color=args.silkscreen_color,
            diagnostics=render_diagnostics,
        )
        for line in render_diagnostics:
            logging.info(line.strip())
        if render_png:
            embed_3d_render_in_breadboard_svg(args.out_dir, render_png)
            logging.info("Embedded 3D render: %s", render_png)
            # Rebuild fzpz so Fritzing loads the version with the embedded render.
            fzp_files = list(args.out_dir.glob("*.fzp"))
            if fzp_files:
                build_fritzing_package_zip(args.out_dir, part_basename=fzp_files[0].stem)
        else:
            logging.warning("3D render failed or kicad-cli not found; breadboard SVG unchanged")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
