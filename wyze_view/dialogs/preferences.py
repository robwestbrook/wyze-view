from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ..settings import Settings


class PreferencesDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        settings: Settings,
        on_save: Callable[[Settings], None],
    ) -> None:
        super().__init__(title="Preferences", transient_for=parent, modal=True)
        self.on_save = on_save
        self.settings = settings
        self.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                         "Save", Gtk.ResponseType.OK)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=12)
        row = 0

        grid.attach(Gtk.Label(label="Snapshot folder", xalign=0.0), 0, row, 1, 1)
        self.folder = Gtk.FileChooserButton(
            title="Snapshot folder", action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        self.folder.set_filename(str(settings.snapshot_dir))
        grid.attach(self.folder, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Recordings folder", xalign=0.0), 0, row, 1, 1)
        self.record_folder = Gtk.FileChooserButton(
            title="Recordings folder", action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        self.record_folder.set_filename(str(getattr(settings, "record_dir", settings.snapshot_dir)))
        grid.attach(self.record_folder, 1, row, 1, 1)
        row += 1

        self.start_fullscreen = Gtk.Switch(active=settings.start_fullscreen)
        grid.attach(Gtk.Label(label="Start fullscreen", xalign=0.0), 0, row, 1, 1)
        grid.attach(self.start_fullscreen, 1, row, 1, 1)
        row += 1

        self.unmute = Gtk.Switch(active=settings.unmute_on_enlarge)
        grid.attach(Gtk.Label(label="Unmute when enlarging", xalign=0.0), 0, row, 1, 1)
        grid.attach(self.unmute, 1, row, 1, 1)
        row += 1

        self.columns = Gtk.ComboBoxText()
        for value, label in ((0, "Auto"), (1, "1 column"), (2, "2 columns"),
                             (3, "3 columns"), (4, "4 columns")):
            self.columns.append(str(value), label)
        self.columns.set_active_id(str(settings.grid_columns))
        grid.attach(Gtk.Label(label="Grid columns", xalign=0.0), 0, row, 1, 1)
        grid.attach(self.columns, 1, row, 1, 1)
        row += 1

        self.autostart = Gtk.Switch(active=settings.autostart)
        grid.attach(Gtk.Label(label="Autostart on login", xalign=0.0), 0, row, 1, 1)
        grid.attach(self.autostart, 1, row, 1, 1)

        self.get_content_area().add(grid)
        self.show_all()

    def result_settings(self) -> Settings:
        self.settings.snapshot_dir = Path(self.folder.get_filename())
        self.settings.record_dir = Path(self.record_folder.get_filename())
        self.settings.start_fullscreen = self.start_fullscreen.get_active()
        self.settings.unmute_on_enlarge = self.unmute.get_active()
        self.settings.grid_columns = int(self.columns.get_active_id() or "0")
        self.settings.autostart = self.autostart.get_active()
        return self.settings

    def run_and_save(self) -> None:
        if self.run() == Gtk.ResponseType.OK:
            self.on_save(self.result_settings())
        self.destroy()
