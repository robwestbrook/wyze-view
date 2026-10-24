from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .about import SHORTCUTS


def show_shortcuts(parent: Gtk.Window) -> None:
    text = "\n".join(f"{key}    {description}" for key, description in SHORTCUTS)
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.CLOSE, text="Keyboard Shortcuts",
    )
    dialog.format_secondary_text(text)
    dialog.run()
    dialog.destroy()
