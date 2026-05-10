"""KiCad Action Plugin bridge for launching KiCad2Fritzing from PCB Editor."""

from __future__ import annotations

from pathlib import Path

from kicad2fritzing.core.extractor import export_board_to_fritzing_stub

try:
    import pcbnew  # type: ignore
except ImportError:  # pragma: no cover
    pcbnew = None


class KiCad2FritzingActionPlugin(pcbnew.ActionPlugin if pcbnew else object):
    """Action plugin wrapper for KiCad's PCB Editor."""

    def defaults(self) -> None:
        self.name = "KiCad2Fritzing"
        self.category = "Export"
        self.description = "Export current KiCad board into Fritzing starter assets"
        self.show_toolbar_button = True

    def Run(self) -> None:  # noqa: N802 - KiCad API uses Run
        board = pcbnew.GetBoard()
        board_path = Path(board.GetFileName())
        out_dir = board_path.parent / "fritzing-part"
        export_board_to_fritzing_stub(board_path, out_dir)


def register_plugin() -> bool:
    """Register with KiCad if pcbnew runtime is available."""
    if pcbnew is None:
        return False

    KiCad2FritzingActionPlugin().register()
    return True
