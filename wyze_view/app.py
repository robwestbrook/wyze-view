from __future__ import annotations

import ctypes
import tempfile
import threading
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
from .util import redact_url, sanitize_name, snapshot_path

CSS = b"""
.tile { background-color: #000000; }
.status { padding: 4px 8px; }
headerbar { padding: 0; }
headerbar button { padding: 4px 0; min-width: 0; }
"""

AUTOSTART_DESKTOP = Path.home() / ".config" / "autostart" / "wyze-view.desktop"
AUTOSTART_CONTENT = (
    "[Desktop Entry]\nType=Application\nName=Wyze View\n"
    "Exec=wyze-view\nIcon=wyze-view\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"
)


def apply_autostart(enabled: bool) -> str | None:
    try:
        if enabled:
            AUTOSTART_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART_DESKTOP.write_text(AUTOSTART_CONTENT)
        else:
            AUTOSTART_DESKTOP.unlink(missing_ok=True)
    except OSError as exc:
        return str(exc)
    return None


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
        self.set_size_request(800, 450)
        self.set_icon_name("wyze-view")
        self.config_path = config_path
        self.cameras: list[Camera] = []
        self.tiles: dict[str, Gtk.EventBox] = {}
        self.offline_overlays: dict[str, Gtk.Widget] = {}
        self.offline: set[str] = set()
        self.players: dict[str, Player] = {}
        self.selected = 0
        self.enlarged: str | None = None
        self.enlarged_muted = False
        self.recording: str | None = None
        self._recording_path: Path | None = None
        self._recording_timer: int | None = None
        self._tmp = tempfile.TemporaryDirectory(prefix="wyze-view-")
        self._xdisplay = _X11.XOpenDisplay(None)
        self._ignored_children: dict[int, int] = {}
        self._torn_down = False

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "Wyze View"
        self.set_titlebar(header)

        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.volume.set_value(100)
        self.volume.set_size_request(120, -1)
        self.volume.set_tooltip_text("Volume (enlarged camera)")
        self.volume.connect("value-changed", self._on_volume)
        header.pack_end(self.volume)

        self.record_button = Gtk.ToggleButton(label="Rec")
        self.record_button.set_tooltip_text("Record selected/enlarged camera")
        self.record_button.connect("toggled", self._on_record_toggled)
        header.pack_end(self.record_button)

        self._record_style = Gtk.CssProvider()
        self._record_style.load_from_data(b".recording { color: #ff4444; font-weight: bold; }")
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            self._record_style,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

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

        if settings.start_fullscreen:
            self.fullscreen()
        else:
            self.maximize()

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
        file_menu.append(self._menu_item("Add Camera…", "add-camera"))
        file_menu.append(self._menu_item("Manage Cameras…", "manage-cameras"))
        file_menu.append(self._menu_item("Reload", "reload"))
        file_menu.append(Gtk.SeparatorMenuItem())
        file_menu.append(self._menu_item("Preferences", "preferences"))
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
        camera_menu.append(self._menu_item("Record", "record"))
        camera_menu.append(self._menu_item("Mute", "mute"))
        camera_item.set_submenu(camera_menu)
        bar.append(camera_item)

        help_item = Gtk.MenuItem(label="Help")
        help_menu = Gtk.Menu()
        help_menu.append(self._menu_item("Keyboard Shortcuts", "shortcuts"))
        help_menu.append(self._menu_item("About", "about"))
        help_item.set_submenu(help_menu)
        bar.append(help_item)
        return bar

    def _menu_item(self, label: str, action: str) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda _w, a=action: self._activate_action(a))
        return item

    def _activate_action(self, name: str) -> None:
        if name.startswith("app."):
            self.get_application().activate_action(name[4:])
        else:
            self.activate_action(name)

    def _add_actions(self) -> None:
        for name, handler, accel in (
            ("reload", lambda *_: self.reload(), "r"),
            ("snapshot", lambda *_: self.snapshot(), "s"),
            ("record", lambda *_: self.toggle_record(), "t"),
            ("mute", lambda *_: self.toggle_mute(), "m"),
            ("fullscreen", lambda *_: self.toggle_fullscreen(), "F11"),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
            self.get_application().set_accels_for_action(
                f"win.{name}", [accel if len(accel) > 1 else f"<Primary>{accel}"]
            )

        action = Gio.SimpleAction.new("manage-cameras", None)
        action.connect("activate", lambda *_: self._manage_cameras())
        self.add_action(action)

        action = Gio.SimpleAction.new("add-camera", None)
        action.connect("activate", lambda *_: self._manage_cameras(start_add=True))
        self.add_action(action)
        self.get_application().set_accels_for_action(
            "win.add-camera", ["<Primary>n"]
        )

        action = Gio.SimpleAction.new("preferences", None)
        action.connect("activate", lambda *_: self._preferences())
        self.add_action(action)
        self.get_application().set_accels_for_action(
            "win.preferences", ["<Primary>p"]
        )

        for name, handler in (
            ("shortcuts", lambda *_: self._shortcuts()),
            ("about", lambda *_: self._about()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        self.get_application().set_accels_for_action("win.shortcuts", ["F1"])

    def _shortcuts(self) -> None:
        from .dialogs.shortcuts import show_shortcuts

        show_shortcuts(self)

    def _about(self) -> None:
        from .dialogs.about import show_about

        show_about(self)

    def _preferences(self) -> None:
        from .dialogs.preferences import PreferencesDialog
        from .settings import save_settings

        def on_save(settings) -> None:
            self.settings = settings
            save_settings(settings, self.get_application().settings_path)
            error = apply_autostart(settings.autostart)
            if error:
                self._set_status(f"Autostart: {error}")
            self._layout()

        PreferencesDialog(self, self.settings, on_save).run_and_save()

    def _manage_cameras(self, start_add: bool = False) -> None:
        from .dialogs.camera_manager import CameraManagerDialog

        def on_save(cameras: list[Camera]) -> None:
            from .config import save_cameras

            try:
                save_cameras(cameras, self.config_path)
            except OSError as exc:
                self._set_status(f"Could not save cameras: {exc}")
                return
            self.reload()

        CameraManagerDialog(self, self.cameras, on_save, start_add=start_add).run()

    def _set_status(self, text: str) -> None:
        self.status.set_text(text)

    def _reconnect(self, name: str) -> None:
        player = self.players.get(name)
        if player:
            player.stop()
            player.start()

    def _update_status(self) -> None:
        audio = self.enlarged if (self.enlarged and not self.enlarged_muted) else "none"
        parts = [f"{len(self.cameras)} cameras", f"{len(self.offline)} offline"]
        if self.cameras:
            parts.append(f"selected: {self.cameras[self.selected].name}")
        parts.append(f"audio: {audio}")
        if self.recording:
            parts.append(f"REC: {self.recording}")
        self._set_status(" · ".join(parts))

    def _set_offline(self, name: str, offline: bool) -> None:
        if offline:
            self.offline.add(name)
        else:
            self.offline.discard(name)
        overlay = self.offline_overlays.get(name)
        if overlay is not None:
            overlay.set_visible(offline)
        self._update_status()

    def _show_empty_state(self) -> None:
        for child in self.fixed.get_children():
            self.fixed.remove(child)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.pack_start(Gtk.Label(label="No cameras yet"), False, False, 0)
        button = Gtk.Button(label="Add your first camera")
        button.connect("clicked", lambda _b: self._manage_cameras())
        box.pack_start(button, False, False, 0)
        self.fixed.put(box, 0, 0)
        self.show_all()
        self._set_status("No cameras configured")

    def _on_destroy(self, _widget) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        for player in self.players.values():
            player.stop()
        self.players.clear()
        self._tmp.cleanup()
        if self._xdisplay:
            _X11.XCloseDisplay(self._xdisplay)
            self._xdisplay = None

        width, height = self.get_size()
        self.settings.window_width = width
        self.settings.window_height = height
        from .settings import save_settings

        try:
            save_settings(self.settings, self.get_application().settings_path)
        except OSError:
            pass

    def reload(self) -> None:
        try:
            cameras = load_cameras(self.config_path)
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
            self._set_status(f"Config error: {exc}")
            return

        if self.recording and self._recording_path:
            try:
                player = self.players.get(self.recording)
                if player:
                    player.stop_recording()
            except Exception:
                pass
            if self._recording_timer is not None:
                GLib.source_remove(self._recording_timer)
                self._recording_timer = None
            self.recording = None
            self._recording_path = None
            if hasattr(self, "record_button"):
                self.record_button.set_active(False)
        for player in self.players.values():
            player.stop()
        self.players.clear()
        self.tiles.clear()
        self.offline_overlays.clear()
        self.offline.clear()
        self._ignored_children.clear()
        self.selected = 0
        self.enlarged = None
        self.enlarged_muted = False
        for child in self.fixed.get_children():
            self.fixed.remove(child)

        self.cameras = cameras
        if not self.cameras:
            self._show_empty_state()
            return

        for camera in self.cameras:
            tile = Gtk.EventBox()
            tile.get_style_context().add_class("tile")
            tile.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            tile.connect("button-press-event", self._on_tile_click, camera.name)
            tile.connect("button-press-event", self._on_tile_right_click, camera.name)
            tile.set_size_request(320, 180)

            overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            overlay.set_valign(Gtk.Align.CENTER)
            overlay.set_halign(Gtk.Align.CENTER)
            label = Gtk.Label(label=camera.name)
            reconnect = Gtk.Button(label="Reconnect")
            reconnect.connect("clicked", lambda _b, n=camera.name: self._reconnect(n))
            overlay.pack_start(label, False, False, 0)
            overlay.pack_start(reconnect, False, False, 0)
            tile.add(overlay)

            tile.set_tooltip_text(
                f"{camera.name}\n{redact_url(camera.url)}"
            )
            self.tiles[camera.name] = tile
            self.offline_overlays[camera.name] = overlay
            self.fixed.put(tile, 0, 0)

        self._layout()
        self.show_all()
        GLib.idle_add(self._start_players)
        self._update_status()

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

        rows, cols = self._grid_shape()
        cell_w = max(width // max(cols, 1), 1)
        cell_h = max(height // max(rows, 1), 1)
        for index, camera in enumerate(self.cameras):
            row, col = grid_position(index, rows, cols)
            tile = self.tiles[camera.name]
            tile.set_visible(True)
            tile.set_size_request(cell_w, cell_h)
            self.fixed.move(tile, col * cell_w, row * cell_h)

    def _grid_shape(self) -> tuple[int, int]:
        rows, cols = grid_dims(len(self.cameras))
        if self.settings.grid_columns:
            cols = min(self.settings.grid_columns, max(len(self.cameras), 1))
            rows = -(-len(self.cameras) // cols)
        return rows, cols

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
            self._set_offline(name, True)
        elif state == "playing":
            self._set_offline(name, False)
            player = self.players[name]
            if self.enlarged:
                if name == self.enlarged:
                    if not self.enlarged_muted:
                        player.set_muted(False)
                else:
                    player.set_muted(True)
                    player.set_paused(True)
            self._probe_async(name)

    def _probe_async(self, name: str) -> None:
        player = self.players.get(name)
        if player is None:
            return

        def worker() -> None:
            info = player.info()
            GLib.idle_add(self._apply_info, name, info)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_info(self, name: str, info: dict) -> bool:
        if name in self.tiles and info.get("width"):
            self.tiles[name].set_tooltip_text(
                f"{name} · {info['width']}x{info['height']} · {info['fps']:.0f} fps"
            )
        return False

    def _on_tile_click(self, _widget, event, name: str) -> bool:
        if event.button != 1:
            return False
        for index, camera in enumerate(self.cameras):
            if camera.name == name:
                self.selected = index
                break
        if self.enlarged == name:
            self._collapse()
        else:
            self._enlarge(name)
        return True

    def _on_tile_right_click(self, _widget, event, name: str) -> bool:
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        for label, callback in (
            ("Enlarge", lambda: self._enlarge(name)),
            ("Snapshot", lambda: self.snapshot(name)),
            ("Record/Stop", lambda: self.toggle_record(name)),
            ("Mute", lambda: self.toggle_mute(name)),
            ("Reconnect", lambda: self._reconnect(name)),
            ("Remove camera", lambda: self._remove_camera(name)),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _w, cb=callback: cb())
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _remove_camera(self, name: str) -> None:
        from .config import save_cameras

        remaining = [c for c in self.cameras if c.name != name]
        try:
            save_cameras(remaining, self.config_path)
        except OSError as exc:
            self._set_status(f"Could not save cameras: {exc}")
            return
        self.reload()

    def _on_volume(self, scale) -> None:
        player = self.players.get(self.enlarged) if self.enlarged else None
        if player:
            player.set_volume(scale.get_value())

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
            if self.settings.unmute_on_enlarge:
                player.set_muted(False)
            else:
                player.set_muted(True)
        self.enlarged_muted = not self.settings.unmute_on_enlarge
        self._layout()
        self._update_status()

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
        self._update_status()

    def snapshot(self, name: str | None = None) -> None:
        name = name or self.enlarged or (self.cameras[0].name if self.cameras else None)
        if not name:
            self._set_status("No camera to snapshot")
            return
        player = self.players.get(name)
        if not player:
            self._set_status("Camera not ready")
            return
        directory = self.settings.snapshot_dir
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = snapshot_path(directory, name, datetime.now())
            player.snapshot(path)
        except Exception as exc:  # noqa: BLE001 - never raise into the GTK loop
            self._set_status(f"Snapshot failed: {exc}")
            self._show_snapshot_result("Snapshot failed", str(exc), None)
            return
        self._set_status(f"Saved {path}")
        self._show_snapshot_result("Snapshot saved", str(path), directory)

    def _record_path(self, name: str) -> Path:
        directory = getattr(self.settings, "record_dir", None) or self.settings.snapshot_dir
        directory.mkdir(parents=True, exist_ok=True)
        from .util import sanitize_name

        now = datetime.now()
        template = getattr(self.settings, "record_filename", "{name}-{date}.mkv") or "{name}-{date}.mkv"
        try:
            fname = template.format(
                name=sanitize_name(name),
                date=now.strftime("%Y%m%d"),
                time=now.strftime("%H%M%S"),
                datetime=now.strftime("%Y%m%d-%H%M%S"),
            )
        except Exception:
            fname = f"{sanitize_name(name)}-{now.strftime('%Y%m%d-%H%M%S')}.mkv"
        if not fname.lower().endswith((".mkv", ".mp4", ".ts")):
            fname += ".mkv"
        return directory / fname

    def _oldest_recording(self, name: str) -> Path | None:
        directory = getattr(self.settings, "record_dir", None) or self.settings.snapshot_dir
        from .util import sanitize_name
        pattern = f"{sanitize_name(name)}-*"
        try:
            files = []
            for p in directory.glob(pattern):
                if p.is_file():
                    files.append((p.stat().st_mtime, p))
            if not files:
                return None
            files.sort()
            return files[0][1]
        except Exception:
            return None

    def _check_record_size(self) -> bool:
        if not self.recording or not self._recording_path:
            return False
        max_mb = int(getattr(self.settings, "record_max_size_mb", 0) or 0)
        if max_mb <= 0:
            return True
        try:
            if self._recording_path.exists():
                size_mb = self._recording_path.stat().st_size / (1024 * 1024)
                if size_mb >= max_mb:
                    target = self.recording
                    oldest = self._oldest_recording(target)
                    if oldest and oldest != self._recording_path:
                        try:
                            oldest.unlink()
                        except Exception:
                            pass
                    self._rotate_recording(target)
                    return True
        except Exception:
            pass
        return True

    def _rotate_recording(self, name: str) -> None:
        player = self.players.get(name)
        if not player:
            self._stop_recording()
            return
        try:
            player.stop_recording()
        except Exception:
            pass
        path = self._record_path(name)
        try:
            player.start_recording(path)
        except Exception as exc:
            self._set_status(f"Record failed: {exc}")
            self._stop_recording()
            return
        self.recording = name
        self._recording_path = path
        self._update_status()
        self._set_status(f"Recording to {path}")

    def toggle_record(self, name: str | None = None) -> None:
        target = name or self.enlarged or (self.cameras[self.selected].name if self.cameras else None)
        if not target:
            self._set_status("No camera to record")
            return
        if self.recording == target:
            self._stop_recording()
            return
        if self.recording:
            self._stop_recording()
        player = self.players.get(target)
        if not player:
            self._set_status("Camera not ready")
            return
        path = self._record_path(target)
        try:
            player.start_recording(path)
        except Exception as exc:
            self._set_status(f"Record failed: {exc}")
            return
        self.recording = target
        self._recording_path = path
        if self._recording_timer is not None:
            try:
                GLib.source_remove(self._recording_timer)
            except Exception:
                pass
        self._recording_timer = GLib.timeout_add(1000, self._check_record_size)
        if hasattr(self, "record_button"):
            self.record_button.set_active(True)
            ctx = self.record_button.get_style_context()
            ctx.add_class("recording")
        self._update_status()
        self._set_status(f"REC ● {target} → {path}")

    def _stop_recording(self) -> None:
        if not self.recording:
            return
        player = self.players.get(self.recording)
        if player:
            try:
                player.stop_recording()
            except Exception:
                pass
        if self._recording_timer is not None:
            try:
                GLib.source_remove(self._recording_timer)
            except Exception:
                pass
            self._recording_timer = None
        path = self._recording_path
        self.recording = None
        self._recording_path = None
        if hasattr(self, "record_button"):
            self.record_button.set_active(False)
            ctx = self.record_button.get_style_context()
            ctx.remove_class("recording")
        self._update_status()
        if path:
            self._set_status(f"Saved {path}")
            GLib.idle_add(self._show_record_result, str(path), self.settings.record_dir)

    def _on_record_toggled(self, button) -> None:
        if button.get_active():
            self.toggle_record()
        else:
            self._stop_recording()

    def _show_snapshot_result(self, title: str, detail: str, folder) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE, text=title,
        )
        dialog.format_secondary_text(detail)
        if folder is not None:
            open_button = dialog.add_button("Open folder", Gtk.ResponseType.ACCEPT)
            open_button.connect(
                "clicked",
                lambda _b: Gtk.show_uri_on_window(
                    self, folder.as_uri(), Gdk.CURRENT_TIME
                ),
            )
        dialog.run()
        dialog.destroy()

    def _show_record_result(self, detail: str, folder) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE, text="Recording saved",
        )
        dialog.format_secondary_text(detail)
        if folder is not None:
            open_button = dialog.add_button("Open folder", Gtk.ResponseType.ACCEPT)
            open_button.connect(
                "clicked",
                lambda _b: Gtk.show_uri_on_window(
                    self, folder.as_uri(), Gdk.CURRENT_TIME
                ),
            )
        dialog.run()
        dialog.destroy()
        return False

    def toggle_mute(self, name: str | None = None) -> None:
        name = name or self.enlarged
        if not name:
            self._set_status("Enlarge a camera to control audio")
            return
        player = self.players.get(name)
        if not player:
            return
        if name == self.enlarged:
            self.enlarged_muted = not self.enlarged_muted
            player.set_muted(self.enlarged_muted)
            self._update_status()
        else:
            player.set_muted(True)
            self._set_status(f"{name}: muted")

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
        if key in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            if not self.cameras:
                return True
            rows, cols = self._grid_shape()
            col = self.selected % cols
            row = self.selected // cols
            if key == Gdk.KEY_Left:
                col = max(col - 1, 0)
            elif key == Gdk.KEY_Right:
                col = min(col + 1, cols - 1)
            elif key == Gdk.KEY_Up:
                row = max(row - 1, 0)
            else:
                row = min(row + 1, rows - 1)
            self.selected = min(row * cols + col, len(self.cameras) - 1)
            self._update_status()
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.cameras:
                name = self.cameras[self.selected].name
                self._enlarge(name)
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
        if key in (Gdk.KEY_t, Gdk.KEY_T):
            self.toggle_record()
            return True
        if key == Gdk.KEY_F11:
            self.toggle_fullscreen()
            return True
        return False
