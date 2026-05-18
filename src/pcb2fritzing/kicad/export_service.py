"""Runtime-agnostic export orchestration for KiCad to Fritzing conversion."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ExportRequest:
    board_path: Path
    board_handle: object | None
    out_dir: Path
    part_name: str
    text_scale: float
    pad_scale: float
    soldermask_color: str
    silkscreen_color: str
    annular_color: str
    hole_color: str
    part_family: str
    part_type: str
    use_kicad_native_overlay: bool
    include_component_silkscreen: bool
    include_fab_layer: bool
    use_3d_render: bool
    kicad_cli_path: str


@dataclass(frozen=True)
class ExportHooks:
    export_board_to_fritzing_stub: Callable[..., None]
    plot_kicad_svg_layers: Callable[..., dict[str, Path]]
    overlay_kicad_plots_on_breadboard: Callable[..., Path | None]
    write_overlay_mode_marker: Callable[..., Path]
    strip_silkscreen_overlays_for_3d: Callable[..., bool]
    render_board_3d: Callable[..., Path | None]
    embed_3d_render_in_breadboard_svg: Callable[..., bool]
    build_fritzing_package_zip: Callable[..., Path]
    detect_kicad_cli: Callable[[str | None], str | None]


def run_export_pipeline(
    request: ExportRequest,
    hooks: ExportHooks,
    append_message: Callable[[str], None],
    yield_control: Callable[[], None] | None = None,
) -> None:
    """Run the full export pipeline with UI-agnostic callbacks."""

    def _yield() -> None:
        if yield_control is not None:
            yield_control()

    append_message(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Starting export...")
    _yield()

    effective_native_overlay = bool(request.use_kicad_native_overlay and not request.use_3d_render)

    append_message(f"  Part name:   {request.part_name}")
    append_message(f"  Output dir:  {request.out_dir}")
    append_message(f"  3D render (requested):             {request.use_3d_render}")
    append_message(
        f"  Native silk overlay (requested):   {request.use_kicad_native_overlay}"
    )
    append_message(
        f"  Native silk overlay (effective):   {effective_native_overlay}"
    )
    if request.use_3d_render and request.use_kicad_native_overlay:
        append_message("  Mode note: native overlay disabled because 3D render is enabled.")
    _yield()

    try:
        request.out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        append_message(f"ERROR: Could not create output directory: {exc}")
        return

    render_options = {
        "soldermask_color": request.soldermask_color,
        "silkscreen_color": request.silkscreen_color,
        "annular_color": request.annular_color,
        "hole_color": request.hole_color,
        "pad_scale": request.pad_scale,
        "silk_text_scale": request.text_scale,
        "include_component_silkscreen": request.include_component_silkscreen,
        "include_fab_layer": request.include_fab_layer,
    }

    append_message("  Running base export (extractor)...")
    _yield()
    try:
        hooks.export_board_to_fritzing_stub(
            request.board_path,
            request.out_dir,
            part_name=request.part_name,
            render_options=render_options,
            part_family=request.part_family,
            part_type=request.part_type,
        )
        append_message("  Base export complete.")
    except Exception as exc:  # noqa: BLE001
        import traceback

        append_message(f"ERROR: Base export failed: {exc}\n{traceback.format_exc()}")
        return
    _yield()

    native_overlay_applied = False
    native_overlay_skip_reason: str | None = None
    if effective_native_overlay:
        append_message("  Plotting KiCad SVG layers...")
        _yield()
        plot_diagnostics: list[str] = []
        plotted = hooks.plot_kicad_svg_layers(
            request.board_handle,
            request.out_dir / "kicad_svg_plots",
            include_fab_layer=request.include_fab_layer,
            board_file=request.board_path,
            kicad_cli_path=request.kicad_cli_path or None,
            diagnostics=plot_diagnostics,
        )
        for line in plot_diagnostics:
            append_message(line)

        append_message(f"  Plotted layers: {list(plotted.keys()) or 'none'}")
        overlaid_path = hooks.overlay_kicad_plots_on_breadboard(
            request.out_dir,
            plotted,
            replace_custom_silkscreen=True,
            silkscreen_color=request.silkscreen_color,
        )
        native_overlay_applied = overlaid_path is not None
        if not native_overlay_applied:
            native_overlay_skip_reason = "no matching SVG bounds or no plottable layers"
        append_message(
            f"  Native silkscreen overlay: {'applied' if native_overlay_applied else 'not applied (no matching SVG bounds?)'}"
        )
        _yield()
    else:
        native_overlay_skip_reason = "disabled by render mode"

    if not effective_native_overlay:
        if native_overlay_skip_reason:
            append_message(
                f"  Native silkscreen overlay: skipped ({native_overlay_skip_reason})."
            )
        else:
            append_message("  Native silkscreen overlay: skipped.")

    hooks.write_overlay_mode_marker(
        request.out_dir,
        requested_native_overlay=request.use_kicad_native_overlay,
        applied_native_overlay=native_overlay_applied,
    )

    if request.use_3d_render:
        append_message("  Starting 3D render via kicad-cli...")
        cli_used = request.kicad_cli_path or hooks.detect_kicad_cli(None) or "(auto-detect)"
        append_message(f"  kicad-cli: {cli_used}")
        _yield()

        stripped = hooks.strip_silkscreen_overlays_for_3d(
            request.out_dir,
            silkscreen_color=request.silkscreen_color,
        )
        append_message(
            f"  2D silkscreen overlays removed for 3D mode: {'yes' if stripped else 'no (breadboard.svg not found?)'}"
        )

        board_bounds_mm = None
        model_json_path = request.out_dir / "board_model.json"
        if model_json_path.exists():
            model_data = json.loads(model_json_path.read_text(encoding="utf-8"))
            board_bounds_mm = model_data.get("board_outline", {}).get("bounds_mm")
            append_message(f"  Board bounds from model: {board_bounds_mm}")
        else:
            append_message("  WARNING: board_model.json not found; render may be uncropped.")

        render_diagnostics: list[str] = []
        render_png = hooks.render_board_3d(
            request.board_path,
            request.out_dir / "kicad_svg_plots",
            board_bounds_mm=board_bounds_mm,
            kicad_cli_path=request.kicad_cli_path or None,
            soldermask_color=request.soldermask_color,
            silkscreen_color=request.silkscreen_color,
            diagnostics=render_diagnostics,
        )
        for line in render_diagnostics:
            append_message(line)
        if render_png:
            append_message(f"  3D render saved: {render_png}")
            embedded = hooks.embed_3d_render_in_breadboard_svg(request.out_dir, render_png)
            append_message(
                f"  Embedded into breadboard SVG: {'yes' if embedded else 'no (boardOutline not found?)'}"
            )
        else:
            append_message("  ERROR: 3D render failed. Check kicad-cli path and board file.")
        _yield()

    append_message("  Rebuilding .fzpz archive...")
    fzp_files = sorted(
        request.out_dir.glob("*.fzp"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if fzp_files:
        hooks.build_fritzing_package_zip(request.out_dir, part_basename=fzp_files[0].stem)
        append_message(f"  Package written: {fzp_files[0].stem}.fzpz")
    else:
        append_message("  WARNING: No .fzp file found; .fzpz not rebuilt.")

    append_message(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Export complete.")