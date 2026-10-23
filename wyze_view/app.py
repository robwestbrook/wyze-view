from __future__ import annotations

import ctypes
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk  # noqa: F401 - registers X11Window

from .config import Camera, load_cameras
from .grid import grid_dims, grid_position
from .player import Player
from .util import sanitize_name, snapshot_path

CSS = b"""
.tile { background-color: #000000; }
.status { padding: 4px 8px; }
headerbar { padding: 0; }
headerbar button { padding: 4px 0; min-width: 0; }
"""


# mpv's X11 video output creates a child X window inside each tile and selects
# button events on it, so the GTK EventBox never sees the click. Clearing that
# child's X input shape lets pointer events fall through to the tile.
_X11 = ctypes.CDLL("libX11.so.6")
_Xext = ctypes.CDLL("libXext.so.6")
_X11.XOpenDisplay.restype = ctypes.c_void_p
_X11.XOpenDisplay.argtypes = [ctypes.c_char_p]
_X11.XCloseDisplay.argtypes = [ctypes.c_void_p]
_X11.XQueryTree.argtypes = [
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong),
    ctypes.POINTER(ctypes.c_ulong),
    ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
    ctypes.POINTER(ctypes.c_uint),
]
_X11.XFree.argtypes = [ctypes.c_void_p]
_X11.XFlush.argtypes = [ctypes.c_void_p]
_Xext.XShapeCombineRectangles.argtypes = [
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
]

_SHAPE_INPUT = 2
_SHAPE_SET = 0
_UNSORTED = 0


def _first_child(display: int, parent: int) -> int:
    root = ctypes.c_ulong()
    parent_ret = ctypes.c_ulong()
    children = ctypes.POINTER(ctypes.c_ulong)()
    count = ctypes.c_uint()
    ok = _X11.XQueryTree(
        display,
        ctypes.c_ulong(parent),
        ctypes.byref(root),
        ctypes.byref(parent_ret),
        ctypes.byref(children),
        ctypes.byref(count),
    )
    if not ok or count.value == 0:
        return 0
    child = int(children[0])
    _X11.XFree(children)
    return child


def _ignore_input(display: int, window: int) -> None:
    _Xext.XShapeCombineRectangles(
        display, ctypes.c_ulong(window), _SHAPE_INPUT, 0, 0, None, 0,
        _SHAPE_SET, _UNSORTED,
    )
    _X11.XFlush(display)


class _Fixed(Gtk.Fixed):
    def do_get_preferred_width(self):
        return 1, 1

    def do_get_preferred_height(self):
        return 1, 1


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application, config_path: Path, settings) -> None:
        super().__init__(application=application, title="Wyze View")
        self.settings = settings
        self.set_default_size(settings.window_width, settings.window_height)
        self.set_icon_name("wyze-view")
        self.config_path = config_path
        self.cameras: list[Camera] = []
        self.tiles: dict[str, Gtk.EventBox] = {}
        self.players: dict[str, Player] = {}
        self.enlarged: str | None = None
        self.enlarged_muted = False
        self._tmp = tempfile.TemporaryDirectory(prefix="wyze-view-")
        self._xdisplay = _X11.XOpenDisplay(None)
        self._ignored_children: dict[int, int] = {}

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "Wyze View"
        self.set_titlebar(header)

        self.status = Gtk.Label(label="")
        self.status.set_xalign(0.0)
        self.status.get_style_context().add_class("status")

        self.fixed = _Fixed()
        self._last_alloc = (0, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(self._build_menu_bar(), False, False, 0)
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
        self.connect("destroy", self._on_destroy)
        GLib.timeout_add(500, self._apply_click_through)

        self._add_actions()
        self.reload()

    def _build_menu_bar(self) -> Gtk.MenuBar:
        bar = Gtk.MenuBar()
        file_item = Gtk.MenuItem(label="File")
        file_menu = Gtk.Menu()
        file_menu.append(self._menu_item("Reload", "reload"))
        file_menu.append(Gtk.SeparatorMenuItem())
        file_menu.append(self._menu_item("Quit", "app.quit"))
        file_item.set_submenu(file_menu)
        bar.append(file_item)

        view_item = Gtk.MenuItem(label="View")
        view_menu = Gtk.Menu()
        view_menu.append(self._menu_item("Fullscreen", "fullscreen"))
        view_item.set_submenu(view_menu)
        bar.append(view_item)

        camera_item = Gtk.MenuItem(label="Camera")
        camera_menu = Gtk.Menu()
        camera_menu.append(self._menu_item("Snapshot", "snapshot"))
        camera_menu.append(self._menu_item("Mute", "mute"))
        camera_item.set_submenu(camera_menu)
        bar.append(camera_item)
        return bar

    def _menu_item(self, label: str, action: str) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda _w, a=action: self.activate_action(a))
        return item

    def _add_actions(self) -> None:
        for name, handler, accel in (
            ("reload", lambda *_: self.reload(), "r"),
            ("snapshot", lambda *_: self.snapshot(), "s"),
            ("mute", lambda *_: self.toggle_mute(), "m"),
            ("fullscreen", lambda *_: self.toggle_fullscreen(), "F11"),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
            self.get_application().set_accels_for_action(
                f"win.{name}", [accel if len(accel) > 1 else f"<Primary>{accel}"]
            )

    def _set_status(self, text: str) -> None:
        self.status.set_text(text)

    def _on_destroy(self, _widget) -> None:
        for player in self.players.values():
            player.stop()
        self.players.clear()
        self._tmp.cleanup()
        if self._xdisplay:
            _X11.XCloseDisplay(self._xdisplay)
            self._xdisplay = None

    def reload(self) -> None:
        try:
            cameras = load_cameras(self.config_path)
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
            self._set_status(f"Config error: {exc}")
            return

        for player in self.players.values():
            player.stop()
        self.players.clear()
        self.tiles.clear()
        self._ignored_children.clear()
        self.enlarged = None
        self.enlarged_muted = False
        for child in self.fixed.get_children():
            self.fixed.remove(child)

        self.cameras = cameras
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
            GLib.idle_add(self._layout)
        return False

    def _apply_click_through(self) -> bool:
        if not self._xdisplay:
            return True
        for tile in self.tiles.values():
            window = tile.get_window()
            if window is None:
                continue
            parent = window.get_xid()
            child = _first_child(self._xdisplay, parent)
            if child and self._ignored_children.get(parent) != child:
                _ignore_input(self._xdisplay, child)
                self._ignored_children[parent] = child
        return True

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

        for index, (camera, window) in enumerate(zip(pending, windows)):
            socket_path = (
                Path(self._tmp.name) / f"{index}-{sanitize_name(camera.name)}.sock"
            )
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
