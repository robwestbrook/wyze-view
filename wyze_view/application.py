from __future__ import annotations

import shutil
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk

from .config import DEFAULT_CONFIG
from .settings import DEFAULT_SETTINGS, load_settings

APP_ID = "io.github.robwestbrook.WyzeView"
VERSION = "0.1.0"


def preferred_backend(session_type: str | None) -> str:
    """Always X11: mpv's --wid embedding needs it; Wayland runs via XWayland."""
    return "x11"


class WyzeViewApp(Gtk.Application):
    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG,
        settings_path: Path = DEFAULT_SETTINGS,
    ) -> None:
        super().__init__(application_id=APP_ID)
        self.config_path = config_path
        self.settings_path = settings_path
        self.settings = load_settings(settings_path)
        self.window = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        if shutil.which("mpv") is None:
            self._fatal(
                "mpv is required",
                "Install it with: sudo apt install mpv",
            )
            return
        action = Gio.SimpleAction.new("quit", None)
        action.connect("activate", lambda *_: self.quit())
        self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self) -> None:
        if self.window is None:
            from .app import MainWindow

            self.window = MainWindow(self, self.config_path, self.settings)
        self.window.present()

    def _fatal(self, text: str, detail: str) -> None:
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=text,
        )
        dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()
        self.quit()
