"""SWIG-only ActionPlugin registration shim."""

from __future__ import annotations

from pcb2fritzing.kicad.plugin import _plugin_icon_path, launch_plugin_from_context
from pcb2fritzing.kicad.runtime_adapter import PCBNEW as pcbnew, resolve_runtime_context


class PCBtoFritzingPartActionPlugin(pcbnew.ActionPlugin if pcbnew else object):
    """Action plugin wrapper for KiCad's PCB Editor."""

    def defaults(self) -> None:
        self.name = "PCB to Fritzing Part"
        self.category = "Export"
        self.description = "Export current KiCad PCB into Fritzing starter assets"
        self.show_toolbar_button = True

        light_icon = _plugin_icon_path("icon_light.png")
        dark_icon = _plugin_icon_path("icon_dark.png")
        if light_icon:
            self.icon_file_name = light_icon
        if dark_icon:
            self.dark_icon_file_name = dark_icon

    def Run(self) -> None:  # noqa: N802 - KiCad API uses Run
        context = resolve_runtime_context()
        if context.board_path is None:
            return

        board = context.board_handle if context.runtime == "swig" else None
        launch_plugin_from_context(context.board_path, board=board)


def register_plugin() -> bool:
    """Register with KiCad when SWIG runtime is available."""
    if pcbnew is None:
        return False

    PCBtoFritzingPartActionPlugin().register()
    return True