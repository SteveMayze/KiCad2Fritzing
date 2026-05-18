"""Runtime adapter for KiCad SWIG and IPC access.

This module centralizes runtime probing so plugin code can transition away from
direct SWIG assumptions while IPC support is introduced incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    import pcbnew as _pcbnew  # type: ignore
except ImportError:  # pragma: no cover
    _pcbnew = None

try:
    import wx as _wx  # type: ignore
except ImportError:  # pragma: no cover
    _wx = None

try:
    import kipy as _kipy  # type: ignore
except ImportError:  # pragma: no cover
    _kipy = None

PCBNEW = _pcbnew
WX = _wx
KIPY = _kipy


@dataclass(frozen=True)
class RuntimeContext:
    """Resolved KiCad runtime state for current session."""

    runtime: str
    board_handle: object | None
    board_path: Path | None


def _resolve_swig_context() -> RuntimeContext | None:
    if PCBNEW is None:
        return None

    try:
        board = PCBNEW.GetBoard()
        if board is None:
            return None
        board_path = Path(board.GetFileName()).resolve()
        return RuntimeContext(runtime="swig", board_handle=board, board_path=board_path)
    except Exception:
        return None


def _resolve_ipc_context() -> RuntimeContext | None:
    if KIPY is None:
        return None

    try:
        kicad = KIPY.KiCad()
        board = kicad.get_board()
        board_name = getattr(board, "name", None)
        if not board_name:
            return None
        project = board.get_project()
        project_path = getattr(project, "path", None)
        if not project_path:
            return None
        board_path = (Path(project_path) / board_name).resolve()
        return RuntimeContext(runtime="ipc", board_handle=board, board_path=board_path)
    except Exception:
        return None


def resolve_runtime_context() -> RuntimeContext:
    """Resolve current board context from SWIG first, then IPC."""
    swig_context = _resolve_swig_context()
    if swig_context is not None:
        return swig_context

    ipc_context = _resolve_ipc_context()
    if ipc_context is not None:
        return ipc_context

    return RuntimeContext(runtime="none", board_handle=None, board_path=None)


def diagnose_runtime() -> list:
    """Return a list of diagnostic strings describing runtime probe results.

    Probes both SWIG and IPC independently so the caller can see exactly why a
    particular runtime was (or was not) chosen, without altering the resolution
    logic.
    """
    lines = []

    # --- SWIG probe ---
    if PCBNEW is None:
        lines.append("  pcbnew (SWIG): NOT available (ImportError)")
        swig_ok = False
    else:
        lines.append("  pcbnew (SWIG): available")
        try:
            board = PCBNEW.GetBoard()
            if board is None:
                lines.append("  pcbnew.GetBoard(): returned None (no board loaded)")
                swig_ok = False
            else:
                lines.append(
                    "  pcbnew.GetBoard(): OK — board loaded ({})".format(
                        Path(board.GetFileName()).name
                    )
                )
                swig_ok = True
        except Exception as exc:
            lines.append("  pcbnew.GetBoard(): ERROR — {}".format(exc))
            swig_ok = False

    # --- IPC probe ---
    if KIPY is None:
        lines.append("  kipy (IPC): NOT available (ImportError — install kicad-python)")
        ipc_ok = False
    else:
        lines.append("  kipy (IPC): available")
        try:
            kicad = KIPY.KiCad()
            board = kicad.get_board()
            board_name = getattr(board, "name", None)
            if board_name:
                lines.append("  kipy.KiCad().get_board(): OK — {}".format(board_name))
                ipc_ok = True
            else:
                lines.append("  kipy.KiCad().get_board(): no board name returned")
                ipc_ok = False
        except Exception as exc:
            lines.append("  kipy.KiCad(): ERROR — {}".format(exc))
            ipc_ok = False

    # --- Decision explanation ---
    if swig_ok:
        lines.append(
            "  => SWIG chosen (pcbnew available + board loaded; IPC never attempted)"
        )
        if not ipc_ok:
            lines.append(
                "  => IPC would NOT have succeeded (see above)"
            )
        else:
            lines.append(
                "  => IPC would also have succeeded, but SWIG takes priority"
            )
    elif ipc_ok:
        lines.append("  => IPC chosen (pcbnew unavailable or returned no board)")
    else:
        lines.append("  => runtime: NONE (neither SWIG nor IPC could resolve a board)")

    return lines


def get_current_board_path() -> Path | None:
    """Return current board path from available runtime."""
    return resolve_runtime_context().board_path


def get_board_runtime_handle() -> object | None:
    """Return runtime board handle from available runtime."""
    return resolve_runtime_context().board_handle


def supports_native_plot_overlay(board_handle: object | None) -> bool:
    """Return True when SWIG plot APIs can be used for native SVG overlay."""
    if PCBNEW is None or board_handle is None:
        return False

    return hasattr(PCBNEW, "PLOT_CONTROLLER") and hasattr(board_handle, "GetFileName")