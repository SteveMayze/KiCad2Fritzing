"""Command-line entry point for pcb2fritzing."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pcb2fritzing.core.extractor import build_fritzing_package_zip, export_board_to_fritzing_stub
from pcb2fritzing.kicad.export_service import ExportHooks, ExportRequest, run_export_pipeline
from pcb2fritzing.kicad.plugin import (
    _find_kicad_cli,
    embed_3d_render_in_breadboard_svg,
    overlay_kicad_plots_on_breadboard,
    plot_kicad_svg_layers,
    render_board_3d,
    strip_silkscreen_overlays_for_3d,
    write_overlay_mode_marker,
)
from pcb2fritzing.kicad.runtime_adapter import resolve_runtime_context


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

    runtime_context = resolve_runtime_context()
    if runtime_context.runtime == "none":
        logging.info("--- Execution mode ---")
        logging.info(
            "No live KiCad runtime detected; using explicit board-file mode, so SWIG/IPC are not available and the export runs from the .kicad_pcb directly."
        )
    else:
        logging.info("--- Runtime diagnostics ---")
        logging.info("Live KiCad runtime detected: %s", runtime_context.runtime.upper())

    request = ExportRequest(
        board_path=args.board_file,
        board_handle=runtime_context.board_handle,
        out_dir=args.out_dir,
        part_name=args.part_name or args.board_file.stem,
        text_scale=1.15,
        pad_scale=0.75,
        soldermask_color="#2b5f82",
        silkscreen_color="#f5f5f5",
        annular_color="#ffb300",
        hole_color="#d84315",
        part_family="KiCad2Fritzing Generated",
        part_type="Custom PCB",
        use_kicad_native_overlay=False,
        include_component_silkscreen=args.include_component_silkscreen,
        include_fab_layer=args.include_fab_layer,
        use_3d_render=args.render_3d,
        kicad_cli_path=args.kicad_cli_path or "",
    )

    hooks = ExportHooks(
        export_board_to_fritzing_stub=export_board_to_fritzing_stub,
        plot_kicad_svg_layers=plot_kicad_svg_layers,
        overlay_kicad_plots_on_breadboard=overlay_kicad_plots_on_breadboard,
        write_overlay_mode_marker=write_overlay_mode_marker,
        strip_silkscreen_overlays_for_3d=strip_silkscreen_overlays_for_3d,
        render_board_3d=render_board_3d,
        embed_3d_render_in_breadboard_svg=embed_3d_render_in_breadboard_svg,
        build_fritzing_package_zip=build_fritzing_package_zip,
        detect_kicad_cli=_find_kicad_cli,
    )

    run_export_pipeline(request, hooks, append_message=logging.info)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
