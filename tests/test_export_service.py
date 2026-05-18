from pathlib import Path
from typing import Optional

from pcb2fritzing.kicad.export_service import ExportHooks, ExportRequest, run_export_pipeline


class HookState:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.export_calls = 0
        self.plot_calls = 0
        self.overlay_calls = 0
        self.marker_calls = 0
        self.strip_calls = 0
        self.render_calls = 0
        self.embed_calls = 0
        self.zip_calls = 0
        self.detect_calls = 0
        self.last_marker_requested = None
        self.last_marker_applied = None
        self.last_zip_part_basename = None
        self.fail_export = False
        self.overlay_result = tmp_path / "breadboard.svg"
        self.render_result = tmp_path / "kicad_svg_plots" / "board_render.png"


def _make_request(
    tmp_path: Path,
    *,
    use_native_overlay: bool,
    use_3d: bool,
    kicad_cli_path: str = "",
) -> ExportRequest:
    return ExportRequest(
        board_path=tmp_path / "demo.kicad_pcb",
        board_handle=object(),
        out_dir=tmp_path / "out",
        part_name="demo",
        text_scale=1.15,
        pad_scale=0.75,
        soldermask_color="#2b5f82",
        silkscreen_color="#f5f5f5",
        annular_color="#ffb300",
        hole_color="#d84315",
        part_family="KiCad2Fritzing Generated",
        part_type="Custom PCB",
        use_kicad_native_overlay=use_native_overlay,
        include_component_silkscreen=False,
        include_fab_layer=False,
        use_3d_render=use_3d,
        kicad_cli_path=kicad_cli_path,
    )


def _make_hooks(state: HookState) -> ExportHooks:
    def export_stub(board_path: Path, out_dir: Path, **_kwargs) -> None:
        state.export_calls += 1
        if state.fail_export:
            raise RuntimeError("boom")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "demo.fzp").write_text("<module></module>", encoding="utf-8")

    def plot_stub(_board_handle, out_dir: Path, **_kwargs) -> dict[str, Path]:
        state.plot_calls += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        silks = out_dir / "f_silks.svg"
        edge = out_dir / "edge_cuts.svg"
        silks.write_text("<svg/>", encoding="utf-8")
        edge.write_text("<svg/>", encoding="utf-8")
        return {"f_silks": silks, "edge_cuts": edge}

    def overlay_stub(_out_dir: Path, _plotted: dict[str, Path], **_kwargs) -> Optional[Path]:
        state.overlay_calls += 1
        return state.overlay_result

    def marker_stub(
        _out_dir: Path,
        *,
        requested_native_overlay: bool,
        applied_native_overlay: bool,
    ) -> Path:
        state.marker_calls += 1
        state.last_marker_requested = requested_native_overlay
        state.last_marker_applied = applied_native_overlay
        marker = state.tmp_path / "out" / "k2f_overlay_mode.json"
        marker.write_text("{}", encoding="utf-8")
        return marker

    def strip_stub(_out_dir: Path, **_kwargs) -> bool:
        state.strip_calls += 1
        return True

    def render_stub(_board_path: Path, out_dir: Path, **_kwargs) -> Optional[Path]:
        state.render_calls += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        state.render_result.parent.mkdir(parents=True, exist_ok=True)
        state.render_result.write_bytes(b"png")
        return state.render_result

    def embed_stub(_out_dir: Path, _render_path: Path) -> bool:
        state.embed_calls += 1
        return True

    def zip_stub(_out_dir: Path, *, part_basename: str) -> Path:
        state.zip_calls += 1
        state.last_zip_part_basename = part_basename
        z = state.tmp_path / "out" / f"{part_basename}.fzpz"
        z.write_bytes(b"zip")
        return z

    def detect_stub(_override: Optional[str]) -> Optional[str]:
        state.detect_calls += 1
        return "/usr/bin/kicad-cli"

    return ExportHooks(
        export_board_to_fritzing_stub=export_stub,
        plot_kicad_svg_layers=plot_stub,
        overlay_kicad_plots_on_breadboard=overlay_stub,
        write_overlay_mode_marker=marker_stub,
        strip_silkscreen_overlays_for_3d=strip_stub,
        render_board_3d=render_stub,
        embed_3d_render_in_breadboard_svg=embed_stub,
        build_fritzing_package_zip=zip_stub,
        detect_kicad_cli=detect_stub,
    )


def test_run_export_pipeline_basic_path(tmp_path: Path) -> None:
    state = HookState(tmp_path)
    request = _make_request(tmp_path, use_native_overlay=False, use_3d=False)
    hooks = _make_hooks(state)
    messages: list[str] = []

    run_export_pipeline(request, hooks, append_message=messages.append)

    assert state.export_calls == 1
    assert state.plot_calls == 0
    assert state.render_calls == 0
    assert state.marker_calls == 1
    assert state.last_marker_requested is False
    assert state.last_marker_applied is False
    assert state.zip_calls == 1
    assert state.last_zip_part_basename == "demo"
    assert any("Native silkscreen overlay: skipped (disabled by render mode)." in m for m in messages)
    assert any("Export complete." in m for m in messages)


def test_run_export_pipeline_applies_native_overlay(tmp_path: Path) -> None:
    state = HookState(tmp_path)
    request = _make_request(tmp_path, use_native_overlay=True, use_3d=False)
    hooks = _make_hooks(state)
    messages: list[str] = []

    run_export_pipeline(request, hooks, append_message=messages.append)

    assert state.plot_calls == 1
    assert state.overlay_calls == 1
    assert state.last_marker_requested is True
    assert state.last_marker_applied is True
    assert any("Native silkscreen overlay: applied" in m for m in messages)


def test_run_export_pipeline_3d_mode_skips_native_overlay_and_renders(tmp_path: Path) -> None:
    state = HookState(tmp_path)
    request = _make_request(tmp_path, use_native_overlay=True, use_3d=True, kicad_cli_path="/usr/bin/kicad-cli")
    hooks = _make_hooks(state)
    messages: list[str] = []

    run_export_pipeline(request, hooks, append_message=messages.append)

    assert state.plot_calls == 0
    assert state.last_marker_requested is True
    assert state.last_marker_applied is False
    assert state.render_calls == 1
    assert state.embed_calls == 1
    assert any("Mode note: native overlay disabled because 3D render is enabled." in m for m in messages)


def test_run_export_pipeline_stops_on_base_export_failure(tmp_path: Path) -> None:
    state = HookState(tmp_path)
    state.fail_export = True
    request = _make_request(tmp_path, use_native_overlay=True, use_3d=True)
    hooks = _make_hooks(state)
    messages: list[str] = []

    run_export_pipeline(request, hooks, append_message=messages.append)

    assert state.export_calls == 1
    assert state.marker_calls == 0
    assert state.zip_calls == 0
    assert state.plot_calls == 0
    assert state.render_calls == 0
    assert any("ERROR: Base export failed: boom" in m for m in messages)