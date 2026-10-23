from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ..application import APP_ID, VERSION

SHORTCUTS = (
    ("R", "Reload cameras"),
    ("S", "Snapshot"),
    ("M", "Mute the enlarged camera"),
    ("F11", "Fullscreen"),
    ("Ctrl+N", "Manage cameras"),
    ("Ctrl+P", "Preferences"),
    ("Ctrl+Q", "Quit"),
    ("Arrows / Enter", "Select and enlarge a tile"),
    ("Esc", "Return to the grid / leave fullscreen"),
)


def show_about(parent: Gtk.Window) -> None:
    dialog = Gtk.AboutDialog(transient_for=parent, modal=True)
    dialog.set_program_name("Wyze View")
    dialog.set_version(VERSION)
    dialog.set_comments("Live RTSP multi-view for your cameras.")
    dialog.set_website("https://github.com/robwestbrook/wyze-view")
    dialog.set_license_type(Gtk.License.MIT_X11)
    dialog.set_logo_icon_name("wyze-view")
    dialog.run()
    dialog.destroy()
