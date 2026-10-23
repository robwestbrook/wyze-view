from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk  # noqa: F401 - registers X11Window

from .config import Camera, load_cameras
from .grid import grid_dims, grid_position
from .player import Player
from .util import snapshot_path

CSS = b"""
.tile { background-color: #000000; }
.status { padding: 4px 8px; }
"""


class MainWindow(Gtk.Window):
    def __init__(self, config_path: Path) -> None:
        super().__init__(title="Wyze View")
        self.set_default_size(1280, 720)
        self.config_path = config_path
        self.cameras: list[Camera] = []
        self.tiles: dict[str, Gtk.EventBox] = {}
        self.players: dict[str, Player] = {}
        self.enlarged: str | None = None
        self.enlarged_muted = False
        self._tmp = tempfile.TemporaryDirectory(prefix="wyze-view-")

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "Wyze View"
        self.set_titlebar(header)
        for label, callback in (
            ("Reload", self.reload),
            ("Snapshot", self.snapshot),
            ("Mute", self.toggle_mute),
            ("Fullscreen", self.toggle_fullscreen),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _b, cb=callback: cb())
            header.pack_start(button)

        self.status = Gtk.Label(label="")
        self.status.set_xalign(0.0)
        self.status.get_style_context().add_class("status")

        self.fixed = Gtk.Fixed()
        self._last_alloc = (0, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(self.fixed, True, True, 0)
        box.pack_start(self.status, False, False, 0)
        self.add(box)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.fixed.connect("size-allocate", self._on_fixed_allocate)
        self.connect("key-press-event", self._on_key)
        self.connect("destroy", Gtk.main_quit)

        self.reload()

    def _set_status(self, text: str) -> None:
        self.status.set_text(text)

    def reload(self) -> None:
        for player in self.players.values():
            player.stop()
        self.players.clear()
        self.tiles.clear()
        self.enlarged = None
        self.enlarged_muted = False
        for child in self.fixed.get_children():
            self.fixed.remove(child)

        self.cameras = load_cameras(self.config_path)
        if not self.cameras:
            self._set_status(f"No cameras configured in {self.config_path}")
            return

        for camera in self.cameras:
            tile = Gtk.EventBox()
            tile.get_style_context().add_class("tile")
            tile.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            tile.connect("button-press-event", self._on_tile_click, camera.name)
            tile.set_size_request(320, 180)
            self.fixed.put(tile, 0, 0)
            self.tiles[camera.name] = tile

        self._layout()
        self.show_all()
        GLib.idle_add(self._start_players)
        self._set_status(f"{len(self.cameras)} cameras")

    def _on_fixed_allocate(self, _widget, allocation) -> bool:
        size = (allocation.width, allocation.height)
        if size != self._last_alloc:
            self._last_alloc = size
            self._layout()
        return False

    def _layout(self) -> None:
        if not self.cameras:
            return
        width = max(self._last_alloc[0], 1)
        height = max(self._last_alloc[1], 1)

        if self.enlarged:
            for name, tile in self.tiles.items():
                tile.set_visible(name == self.enlarged)
            tile = self.tiles[self.enlarged]
            tile.set_size_request(width, height)
            self.fixed.move(tile, 0, 0)
            return

        rows, cols = grid_dims(len(self.cameras))
        cell_w = max(width // max(cols, 1), 1)
        cell_h = max(height // max(rows, 1), 1)
        for index, camera in enumerate(self.cameras):
            row, col = grid_position(index, rows, cols)
            tile = self.tiles[camera.name]
            tile.set_visible(True)
            tile.set_size_request(cell_w, cell_h)
            self.fixed.move(tile, col * cell_w, row * cell_h)

    def _start_players(self) -> bool:
        pending = [c for c in self.cameras if c.name not in self.players]
        if not pending:
            return False
        windows = [self.tiles[c.name].get_window() for c in pending]
        if any(window is None for window in windows):
            GLib.timeout_add(100, self._start_players)
            return False

        for camera, window in zip(pending, windows):
            socket_path = Path(self._tmp.name) / f"{camera.name}.sock"
            player = Player(
                camera,
                window.get_xid(),
                socket_path,
                on_state=lambda state, name=camera.name: GLib.idle_add(
                    self._on_state, name, state
                ),
            )
            player.start()
            self.players[camera.name] = player
        if self.enlarged:
            self._enlarge(self.enlarged)
        return False

    def _on_state(self, name: str, state: str) -> None:
        if name not in self.players:
            return
        if state == "offline":
            self._set_status(f"{name}: offline — retrying")
        elif state == "playing" and self.enlarged:
            player = self.players[name]
            if name == self.enlarged:
                if not self.enlarged_muted:
                    player.set_muted(False)
            else:
                player.set_muted(True)
                player.set_paused(True)

    def _on_tile_click(self, _widget, event, name: str) -> bool:
        if event.button != 1:
            return False
        if self.enlarged == name:
            self._collapse()
        else:
            self._enlarge(name)
        return True

    def _enlarge(self, name: str) -> None:
        self.enlarged = name
        camera = next(c for c in self.cameras if c.name == name)
        for other_name, other in self.players.items():
            if other_name != name:
                other.set_muted(True)
                other.set_paused(True)
        player = self.players.get(name)
        if player:
            player.load(camera.url)
            player.set_paused(False)
            player.set_muted(False)
        self.enlarged_muted = False
        self._layout()
        self._set_status(f"Viewing {name}")

    def _collapse(self) -> None:
        name = self.enlarged
        self.enlarged = None
        camera = next((c for c in self.cameras if c.name == name), None)
        player = self.players.get(name) if name else None
        if camera and player:
            player.load(camera.sub_url or camera.url)
        for other in self.players.values():
            other.set_muted(True)
            other.set_paused(False)
        self._layout()
        self._set_status(f"{len(self.cameras)} cameras")

    def snapshot(self) -> None:
        name = self.enlarged or (self.cameras[0].name if self.cameras else None)
        if not name:
            self._set_status("No camera to snapshot")
            return
        player = self.players.get(name)
        if not player:
            self._set_status("Camera not ready")
            return
        directory = Path.home() / "Pictures" / "cameras"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = snapshot_path(directory, name, datetime.now())
            player.snapshot(path)
            self._set_status(f"Saved {path}")
        except Exception as exc:  # noqa: BLE001 - never raise into the GTK loop
            self._set_status(f"Snapshot failed: {exc}")

    def toggle_mute(self) -> None:
        if not self.enlarged:
            self._set_status("Enlarge a camera to control audio")
            return
        player = self.players.get(self.enlarged)
        if not player:
            return
        self.enlarged_muted = not self.enlarged_muted
        player.set_muted(self.enlarged_muted)
        self._set_status("Muted" if self.enlarged_muted else "Audio on")

    def toggle_fullscreen(self) -> None:
        window = self.get_window()
        if window and window.get_state() & Gdk.WindowState.FULLSCREEN:
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_key(self, _widget, event) -> bool:
        key = event.keyval
        if key == Gdk.KEY_Escape:
            if self.enlarged:
                self._collapse()
            else:
                self.unfullscreen()
            return True
        if key in (Gdk.KEY_r, Gdk.KEY_R):
            self.reload()
            return True
        if key in (Gdk.KEY_s, Gdk.KEY_S):
            self.snapshot()
            return True
        if key in (Gdk.KEY_m, Gdk.KEY_M):
            self.toggle_mute()
            return True
        if key == Gdk.KEY_F11:
            self.toggle_fullscreen()
            return True
        return False
