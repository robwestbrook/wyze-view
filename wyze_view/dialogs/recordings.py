from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from ..recorder import list_recordings


class RecordingsDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, directory: Path) -> None:
        super().__init__(title="Recordings", transient_for=parent, modal=True)
        self.set_default_size(560, 400)
        self.directory = Path(directory)
        self.paths: list[Path] = []

        self.add_button("Close", Gtk.ResponseType.CLOSE)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", lambda *_: self._play())
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.listbox)

        buttons = Gtk.Box(spacing=6)
        for label, callback in (
            ("Play", self._play), ("Open folder", self._open_folder),
            ("Refresh", self._refresh),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _b, cb=callback: cb())
            buttons.pack_start(button, False, False, 0)

        self.result = Gtk.Label(label="")
        self.result.set_xalign(0.0)

        content = self.get_content_area()
        content.pack_start(scroller, True, True, 6)
        content.pack_start(buttons, False, False, 6)
        content.pack_start(self.result, False, False, 6)
        self._refresh()
        self.show_all()

    def run_and_destroy(self) -> None:
        self.run()
        self.destroy()

    def _refresh(self) -> None:
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.paths = list_recordings(self.directory)
        if not self.paths:
            self.listbox.add(Gtk.Label(label="No recordings yet", xalign=0.0))
        for path in self.paths:
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime)
            self.listbox.add(Gtk.Label(
                label=(f"{path.name}  —  {stat.st_size / (1024 * 1024):.1f} MB"
                       f"  —  {modified:%Y-%m-%d %H:%M}"),
                xalign=0.0,
            ))
        self.listbox.show_all()

    def _selected_path(self) -> Path | None:
        if not self.paths:
            return None
        row = self.listbox.get_selected_row()
        return None if row is None else self.paths[row.get_index()]

    def _play(self) -> None:
        path = self._selected_path()
        if path is None:
            self.result.set_text("Select a recording to play")
            return
        try:
            subprocess.Popen(
                ["mpv", "--force-window=yes", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.result.set_text(f"Could not play: {exc}")
            return
        self.result.set_text(f"Playing {path.name}")

    def _open_folder(self) -> None:
        Gtk.show_uri_on_window(self, self.directory.as_uri(), Gdk.CURRENT_TIME)
