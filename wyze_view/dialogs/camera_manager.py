from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from ..config import (
    Camera,
    compose_rtsp_url,
    split_rtsp_url,
    test_camera,
)
from ..util import redact_url

FIELDS = ("name", "host", "port", "user", "password", "path", "sub_path")


class CameraManagerDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        cameras: list[Camera],
        on_save: Callable[[list[Camera]], None],
        start_add: bool = False,
    ) -> None:
        super().__init__(title="Cameras", transient_for=parent, modal=True)
        self.set_default_size(640, 480)
        self.on_save = on_save
        self.cameras: list[Camera] = list(cameras)

        self.add_button("Close", Gtk.ResponseType.CLOSE)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.listbox)

        buttons = Gtk.Box(spacing=6)
        for label, callback in (
            ("Add", self._add), ("Edit", self._edit), ("Remove", self._remove),
            ("Test", self._test), ("Move Up", self._move_up),
            ("Move Down", self._move_down),
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
        if start_add:
            GLib.idle_add(self._add)

    def _refresh(self) -> None:
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        for camera in self.cameras:
            self.listbox.add(Gtk.Label(label=f"{camera.name}  —  {redact_url(camera.url)}",
                                       xalign=0.0))

    def _selected(self) -> int | None:
        row = self.listbox.get_selected_row()
        return None if row is None else row.get_index()

    def _add(self) -> None:
        self._edit_form(None)

    def _edit(self) -> None:
        index = self._selected()
        if index is None:
            self.result.set_text("Select a camera to edit")
            return
        self._edit_form(index)

    def _edit_form(self, index: int | None) -> None:
        dialog = Gtk.Dialog(title="Camera", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                           "Test", Gtk.ResponseType.APPLY,
                           "Save", Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=6, margin=8)
        entries: dict[str, Gtk.Entry] = {}
        labels = {
            "name": "Name", "host": "Host / IP", "port": "Port",
            "user": "Username", "password": "Password",
            "path": "Main path", "sub_path": "Sub path (optional)",
        }
        values = self._form_values(index)

        grid.attach(Gtk.Label(label="Name", xalign=0.0), 0, 0, 1, 1)
        name_entry = Gtk.Entry()
        name_entry.set_text(values["name"])
        entries["name"] = name_entry
        grid.attach(name_entry, 1, 0, 1, 1)

        full_check = Gtk.CheckButton(label="Paste a full URL instead")
        grid.attach(full_check, 0, 1, 2, 1)

        compose_widgets: list[Gtk.Widget] = []
        row = 2
        for field in ("host", "port", "user", "password", "path", "sub_path"):
            widget_label = Gtk.Label(label=labels[field], xalign=0.0)
            grid.attach(widget_label, 0, row, 1, 1)
            entry = Gtk.Entry()
            entry.set_text(values[field])
            entries[field] = entry
            if field == "password":
                entry.set_visibility(False)
                entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
                show = Gtk.CheckButton(label="Show")
                show.connect(
                    "toggled", lambda button, e=entry: e.set_visibility(button.get_active())
                )
                box = Gtk.Box(spacing=6)
                box.pack_start(entry, True, True, 0)
                box.pack_start(show, False, False, 0)
                grid.attach(box, 1, row, 1, 1)
                compose_widgets += [widget_label, box]
            else:
                grid.attach(entry, 1, row, 1, 1)
                compose_widgets += [widget_label, entry]
            row += 1

        main_url_label = Gtk.Label(label="Main URL", xalign=0.0)
        main_url_entry = Gtk.Entry()
        main_url_entry.set_text(values["url"])
        sub_url_label = Gtk.Label(label="Sub URL (optional)", xalign=0.0)
        sub_url_entry = Gtk.Entry()
        sub_url_entry.set_text(values["sub_url"])
        grid.attach(main_url_label, 0, row, 1, 1)
        grid.attach(main_url_entry, 1, row, 1, 1)
        row += 1
        grid.attach(sub_url_label, 0, row, 1, 1)
        grid.attach(sub_url_entry, 1, row, 1, 1)
        url_widgets = [main_url_label, main_url_entry, sub_url_label, sub_url_entry]

        def on_full_mode(check: Gtk.CheckButton) -> None:
            active = check.get_active()
            for widget in compose_widgets:
                widget.set_visible(not active)
            for widget in url_widgets:
                widget.set_visible(active)

        full_check.connect("toggled", on_full_mode)

        result = Gtk.Label(label="", xalign=0.0)
        dialog.get_content_area().add(grid)
        dialog.get_content_area().add(result)
        dialog.show_all()
        on_full_mode(full_check)

        while True:
            response = dialog.run()
            if response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.DELETE_EVENT):
                break
            name = entries["name"].get_text().strip()
            if not name:
                result.set_text("Name is required")
                continue
            duplicate = any(
                camera.name == name
                for other, camera in enumerate(self.cameras)
                if other != index
            )
            if duplicate:
                result.set_text("Name already used")
                continue
            if full_check.get_active():
                url = main_url_entry.get_text().strip()
                if not url:
                    result.set_text("Main URL is required")
                    continue
                camera = Camera(name, url, sub_url_entry.get_text().strip() or None)
            else:
                camera = self._camera_from(entries, name)
                if camera is None:
                    result.set_text("Host is required")
                    continue
            if response == Gtk.ResponseType.APPLY:
                ok, detail = test_camera(camera)
                result.set_text(("OK  " if ok else "FAIL ") + detail)
                continue
            if index is None:
                self.cameras.append(camera)
            else:
                self.cameras[index] = camera
            self._refresh()
            self.on_save(list(self.cameras))
            break
        dialog.destroy()

    def _form_values(self, index: int | None) -> dict[str, str]:
        if index is None:
            empty = {field: ("554" if field == "port" else
                             ("/stream0/" if field == "path" else
                              ("/stream1/" if field == "sub_path" else "")))
                     for field in FIELDS}
            empty["url"] = ""
            empty["sub_url"] = ""
            return empty
        camera = self.cameras[index]
        parts = split_rtsp_url(camera.url)
        sub = split_rtsp_url(camera.sub_url) if camera.sub_url else None
        return {
            "name": camera.name,
            "host": parts["host"],
            "port": str(parts["port"]),
            "user": parts["user"],
            "password": parts["password"],
            "path": parts["path"],
            "sub_path": sub["path"] if sub else "",
            "url": camera.url,
            "sub_url": camera.sub_url or "",
        }

    def _camera_from(self, entries: dict[str, Gtk.Entry], name: str) -> Camera | None:
        values = {field: entry.get_text() for field, entry in entries.items()}
        if not values["host"].strip():
            return None
        try:
            port = int(values["port"] or "554")
        except ValueError:
            port = 554
        url = compose_rtsp_url(values["host"].strip(), port, values["user"],
                               values["password"], values["path"] or "/stream0/")
        sub_url = None
        if values["sub_path"].strip():
            sub_url = compose_rtsp_url(values["host"].strip(), port, values["user"],
                                       values["password"], values["sub_path"])
        return Camera(name, url, sub_url)

    def _remove(self) -> None:
        index = self._selected()
        if index is None:
            self.result.set_text("Select a camera to remove")
            return
        del self.cameras[index]
        self._refresh()
        self.on_save(list(self.cameras))

    def _move(self, delta: int) -> None:
        index = self._selected()
        if index is None:
            return
        target = index + delta
        if not 0 <= target < len(self.cameras):
            return
        self.cameras[index], self.cameras[target] = (
            self.cameras[target], self.cameras[index],
        )
        self._refresh()
        self.listbox.select_row(self.listbox.get_row_at_index(target))
        self.on_save(list(self.cameras))

    def _move_up(self) -> None:
        self._move(-1)

    def _move_down(self) -> None:
        self._move(1)

    def _test(self) -> None:
        index = self._selected()
        if index is None:
            self.result.set_text("Select a camera to test")
            return
        self.result.set_text("Testing…")
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        ok, detail = test_camera(self.cameras[index])
        self.result.set_text(("OK  " if ok else "FAIL ") + detail)
