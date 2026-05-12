"""KiCad Action Plugin bridge for launching KiCad2Fritzing from PCB Editor."""

from __future__ import annotations

import os
from pathlib import Path

from kicad2fritzing.core.extractor import export_board_to_fritzing_stub

try:
    import pcbnew  # type: ignore
    import wx  # type: ignore
except ImportError:  # pragma: no cover
    pcbnew = None
    wx = None


class KiCad2FritzingDialog(wx.Dialog if wx else object):  # type: ignore
    """Dialog for KiCad2Fritzing part generation settings."""

    def __init__(self, parent, board_path: Path) -> None:
        """Initialize dialog with default values from board path."""
        if wx is None:
            raise RuntimeError("wxPython not available")
        
        wx.Dialog.__init__(self, parent, title="KiCad2Fritzing Part Generation", size=(550, 280))
        self.board_path = board_path
        
        # Panel setup
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Part Name
        part_label = wx.StaticText(panel, label="Part Name:")
        self.part_name_input = wx.TextCtrl(
            panel,
            value=board_path.stem,
            size=(400, -1)
        )
        part_sizer = wx.BoxSizer(wx.HORIZONTAL)
        part_sizer.Add(part_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        part_sizer.Add(self.part_name_input, 1, wx.EXPAND)
        sizer.Add(part_sizer, 0, wx.ALL | wx.EXPAND, 10)
        
        # Directory
        dir_label = wx.StaticText(panel, label="Output Directory:")
        self.dir_input = wx.TextCtrl(
            panel,
            value=str(board_path.parent / "fritzing-part"),
            size=(350, -1)
        )
        self.browse_btn = wx.Button(panel, label="Browse", size=(80, -1))
        self.browse_btn.Bind(wx.EVT_BUTTON, self._on_browse)
        dir_sizer = wx.BoxSizer(wx.HORIZONTAL)
        dir_sizer.Add(dir_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        dir_sizer.Add(self.dir_input, 1, wx.EXPAND | wx.RIGHT, 5)
        dir_sizer.Add(self.browse_btn, 0)
        sizer.Add(dir_sizer, 0, wx.ALL | wx.EXPAND, 10)
        
        # Text Scaling
        scale_label = wx.StaticText(panel, label="Text Scaling:")
        self.scale_input = wx.TextCtrl(panel, value="1.15", size=(400, -1))
        scale_sizer = wx.BoxSizer(wx.HORIZONTAL)
        scale_sizer.Add(scale_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        scale_sizer.Add(self.scale_input, 1, wx.EXPAND)
        sizer.Add(scale_sizer, 0, wx.ALL | wx.EXPAND, 10)
        
        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        generate_btn = wx.Button(panel, wx.ID_OK, "Generate")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        btn_sizer.Add(generate_btn, 0, wx.RIGHT, 10)
        btn_sizer.Add(cancel_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        
        panel.SetSizer(sizer)
    
    def _on_browse(self, event) -> None:
        """Handle browse button click."""
        dlg = wx.DirDialog(
            self,
            "Choose output directory",
            defaultPath=self.dir_input.GetValue(),
            style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.dir_input.SetValue(dlg.GetPath())
        dlg.Destroy()
    
    def get_values(self) -> tuple[str, Path, float]:
        """Return (part_name, out_dir, text_scale) from dialog inputs."""
        part_name = self.part_name_input.GetValue()
        out_dir = Path(self.dir_input.GetValue())
        try:
            text_scale = float(self.scale_input.GetValue())
        except ValueError:
            text_scale = 1.15
        return part_name, out_dir, text_scale


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
        
        # Show dialog to collect user settings
        if wx is None:
            # Fallback if wxPython unavailable
            out_dir = board_path.parent / "fritzing-part"
            export_board_to_fritzing_stub(board_path, out_dir)
            return
        
        dlg = KiCad2FritzingDialog(None, board_path)
        if dlg.ShowModal() == wx.ID_OK:
            part_name, out_dir, text_scale = dlg.get_values()
            out_dir.mkdir(parents=True, exist_ok=True)
            
            # Set text scaling as environment variable
            os.environ["K2F_SILK_TEXT_SCALE"] = str(text_scale)
            
            try:
                export_board_to_fritzing_stub(board_path, out_dir, part_name=part_name)
            finally:
                # Restore default text scaling
                os.environ["K2F_SILK_TEXT_SCALE"] = "1.15"
        
        dlg.Destroy()


def register_plugin() -> bool:
    """Register with KiCad if pcbnew runtime is available."""
    if pcbnew is None:
        return False

    KiCad2FritzingActionPlugin().register()
    return True
