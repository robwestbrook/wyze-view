# Wyze View Distributable App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the working Wyze View prototype into a proper, distributable GTK3 desktop app: a `Gtk.Application` shell with menus/actions/Quit, an in-app camera manager, preferences, richer per-camera feedback, and a Debian `.deb` plus repo hygiene.

**Architecture:** Keep the one-mpv-per-camera `--wid` embedding and the click-through fix. Wrap it in `Gtk.Application` (single instance, actions, accelerators, menu bar). Add pure-logic modules (`settings.py`, config write/test helpers) with tests, dialogs driven by actions, and packaging under `debian/`. GTK cannot draw over mpv's child window, so the playing-tile name uses mpv's OSD; the offline overlay and tooltips are GTK (visible when no video window is present / as separate windows).

**Tech Stack:** Python 3.13, PyGObject/GTK3, mpv 0.40, `tomllib`, pytest (dev, in `.venv`), Debian `debhelper` + `pybuild`.

**Spec:** `docs/superpowers/specs/2026-10-23-wyze-view-distributable-design.md`

## Global Constraints

- Target: Debian 13 (trixie) / Ubuntu 24.04+, **X11** session; `--wid` embedding needs X11, Wayland runs via XWayland (`GDK_BACKEND=x11`).
- Runtime dependency: `mpv` only (`Depends`); `ffmpeg` is `Recommends` (Test/`--check` only).
- Python `>=3.11`; GTK3 via `python3-gi`, `gir1.2-gtk-3.0`.
- Never print or log RTSP passwords; redact userinfo in all output (`redact_url`).
- Config at `~/.config/wyze-view/cameras.toml`, dir `0700`, file `0600`; settings at `~/.config/wyze-view/settings.toml` same modes.
- Audio: all muted at start; exactly one unmuted camera when enlarged; none in grid.
- Tiles live in `Gtk.Fixed` and are never reparented; mpv's child window input shape is cleared so tiles receive clicks.
- Application id `io.github.robwestbrook.WyzeView`; repo `https://github.com/robwestbrook/wyze-view`; license MIT.
- App binds no network ports.
- GUI has no automated tests (needs display + real cameras); all pure logic is unit-tested and GUI steps are explicit manual checks.

---

## File Structure

```
wyze-view/
├── pyproject.toml                     # + [build-system], packages
├── README.md                          # new
├── LICENSE                            # new (MIT)
├── bin/wyze-view                      # dev launcher (unchanged)
├── tools/xclick.py                    # new dev aid (libXtst click injector)
├── tools/make_icons.sh                # new icon generator
├── packaging/
│   ├── wyze-view.desktop              # updated (Icon=wyze-view)
│   ├── io.github.robwestbrook.WyzeView.metainfo.xml   # new
│   └── icons/wyze-view.svg, wyze-view-{16,24,32,48,64,128,256}.png  # new
├── debian/                            # new packaging tree
│   ├── control  rules  changelog  copyright  install  wyze-view.1
├── wyze_view/
│   ├── __init__.py                    # + __version__
│   ├── __main__.py                    # modified: backend + Gtk.Application
│   ├── application.py                 # new: WyzeViewApp, actions, backend
│   ├── settings.py                    # new: Settings load/save
│   ├── config.py                      # + save_cameras, test_camera, url helpers
│   ├── app.py                         # modified: ApplicationWindow, menus, tiles
│   ├── dialogs/
│   │   ├── __init__.py                # new
│   │   ├── camera_manager.py          # new
│   │   ├── preferences.py             # new
│   │   ├── about.py                   # new
│   │   └── shortcuts.py               # new
│   └── (grid.py, player.py, util.py)  # player.py + set_volume/info
└── tests/
    ├── test_settings.py               # new
    ├── test_config.py                 # extended
    └── test_backend.py                # new
```

---

### Task 1: Settings module

**Files:**
- Create: `wyze_view/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DEFAULT_SETTINGS: Path`
  - `Settings` dataclass with fields `snapshot_dir: Path`, `start_fullscreen: bool`, `unmute_on_enlarge: bool`, `grid_columns: int`, `autostart: bool`, `window_width: int`, `window_height: int`
  - `load_settings(path: Path = DEFAULT_SETTINGS) -> Settings`
  - `save_settings(settings: Settings, path: Path = DEFAULT_SETTINGS) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_settings.py`:

```python
import stat

from wyze_view.settings import Settings, load_settings, save_settings


def test_missing_file_returns_defaults(tmp_path):
    settings = load_settings(tmp_path / "nope.toml")
    assert settings == Settings()


def test_partial_file_fills_defaults(tmp_path):
    cfg = tmp_path / "settings.toml"
    cfg.write_text("grid_columns = 3\nstart_fullscreen = true\n")
    settings = load_settings(cfg)
    assert settings.grid_columns == 3
    assert settings.start_fullscreen is True
    assert settings.window_width == 1280
    assert settings.unmute_on_enlarge is True


def test_save_round_trip_with_restricted_permissions(tmp_path):
    cfg = tmp_path / "nested" / "settings.toml"
    original = Settings(
        snapshot_dir=tmp_path / "pics",
        start_fullscreen=True,
        unmute_on_enlarge=False,
        grid_columns=2,
        autostart=True,
        window_width=800,
        window_height=600,
    )
    save_settings(original, cfg)
    assert load_settings(cfg) == original
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.parent.stat().st_mode) == 0o700


def test_invalid_values_are_ignored(tmp_path):
    cfg = tmp_path / "settings.toml"
    cfg.write_text('grid_columns = 9\nwindow_width = "wide"\n')
    settings = load_settings(cfg)
    assert settings.grid_columns == 0
    assert settings.window_width == 1280
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wyze_view.settings'`

- [ ] **Step 3: Implement `wyze_view/settings.py`**

```python
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SETTINGS = Path.home() / ".config" / "wyze-view" / "settings.toml"


@dataclass
class Settings:
    snapshot_dir: Path = Path.home() / "Pictures" / "cameras"
    start_fullscreen: bool = False
    unmute_on_enlarge: bool = True
    grid_columns: int = 0
    autostart: bool = False
    window_width: int = 1280
    window_height: int = 720


def load_settings(path: Path = DEFAULT_SETTINGS) -> Settings:
    settings = Settings()
    if not path.exists():
        return settings

    raw = tomllib.loads(path.read_text())
    if isinstance(raw.get("snapshot_dir"), str):
        settings.snapshot_dir = Path(raw["snapshot_dir"]).expanduser()
    for field in ("start_fullscreen", "unmute_on_enlarge", "autostart"):
        if isinstance(raw.get(field), bool):
            setattr(settings, field, raw[field])
    if isinstance(raw.get("grid_columns"), int) and 0 <= raw["grid_columns"] <= 4:
        settings.grid_columns = raw["grid_columns"]
    for field in ("window_width", "window_height"):
        value = raw.get(field)
        if isinstance(value, int) and value > 0:
            setattr(settings, field, value)
    return settings


def save_settings(settings: Settings, path: Path = DEFAULT_SETTINGS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    lines = [
        "# Wyze View settings.",
        f"snapshot_dir = {json.dumps(str(settings.snapshot_dir))}",
        f"start_fullscreen = {str(settings.start_fullscreen).lower()}",
        f"unmute_on_enlarge = {str(settings.unmute_on_enlarge).lower()}",
        f"grid_columns = {settings.grid_columns}",
        f"autostart = {str(settings.autostart).lower()}",
        f"window_width = {settings.window_width}",
        f"window_height = {settings.window_height}",
        "",
    ]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/pytest tests/test_settings.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add wyze_view/settings.py tests/test_settings.py
git commit -m "feat: add settings module"
```

---

### Task 2: Config write, URL helpers, and camera test

**Files:**
- Modify: `wyze_view/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `Camera`, `DEFAULT_CONFIG`, `redact_url` (`wyze_view.util`).
- Produces:
  - `save_cameras(cameras: list[Camera], path: Path = DEFAULT_CONFIG) -> None`
  - `compose_rtsp_url(host: str, port: int, user: str, password: str, path: str) -> str`
  - `split_rtsp_url(url: str) -> dict` with keys `host`, `port`, `user`, `password`, `path`
  - `test_camera(camera: Camera, timeout: float = 15.0) -> tuple[bool, str]` (detail redacted)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
import stat

from wyze_view.config import (
    Camera,
    compose_rtsp_url,
    load_cameras,
    save_cameras,
    split_rtsp_url,
    test_camera,
)


def test_save_cameras_round_trips_with_permissions(tmp_path):
    cfg = tmp_path / "nested" / "cameras.toml"
    cams = [
        Camera("Front Door", "rtsp://u:p@1.2.3.4:554/stream0/", None),
        Camera("Back", "rtsp://u:p@1.2.3.5:554/stream0/", "rtsp://u:p@1.2.3.5:554/stream1/"),
    ]
    save_cameras(cams, cfg)
    assert load_cameras(cfg) == cams
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.parent.stat().st_mode) == 0o700


def test_save_cameras_preserves_order(tmp_path):
    cfg = tmp_path / "cameras.toml"
    cams = [Camera("B", "rtsp://u:p@1.2.3.4/x"), Camera("A", "rtsp://u:p@1.2.3.5/x")]
    save_cameras(cams, cfg)
    assert [c.name for c in load_cameras(cfg)] == ["B", "A"]


def test_compose_rtsp_url_percent_encodes_credentials():
    url = compose_rtsp_url("192.168.1.5", 554, "user", "p@ss:word", "/stream0/")
    assert url == "rtsp://user:p%40ss%3Aword@192.168.1.5:554/stream0/"


def test_split_rtsp_url_decodes_credentials():
    parts = split_rtsp_url("rtsp://user:p%40ss%3Aword@192.168.1.5:554/stream0/")
    assert parts == {
        "host": "192.168.1.5",
        "port": 554,
        "user": "user",
        "password": "p@ss:word",
        "path": "/stream0/",
    }


def test_test_camera_reports_missing_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr("wyze_view.config.shutil.which", lambda _: None)
    ok, detail = test_camera(Camera("A", "rtsp://u:p@1.2.3.4/x"))
    assert ok is False
    assert "ffmpeg" in detail


def test_test_camera_redacts_credentials(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "rtsp://user:secret@1.2.3.4/x: connection refused"

    monkeypatch.setattr("wyze_view.config.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "wyze_view.config.subprocess.run", lambda *a, **k: Result()
    )
    ok, detail = test_camera(Camera("A", "rtsp://user:secret@1.2.3.4/x"))
    assert ok is False
    assert "secret" not in detail
    assert "***" in detail
```

Note: `tests/test_config.py` already imports `stat` and `load_cameras` at the top; if so, keep the existing import lines and only add the new names. Do not duplicate imports.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'save_cameras'`

- [ ] **Step 3: Implement the additions in `wyze_view/config.py`**

Add these imports at the top (keep existing ones):

```python
import json
import os
import shutil
import subprocess
from urllib.parse import quote, unquote, urlsplit

from .util import redact_url
```

Append these functions:

```python
def compose_rtsp_url(host: str, port: int, user: str, password: str, path: str) -> str:
    userinfo = f"{quote(user, safe='')}:{quote(password, safe='')}"
    if not path.startswith("/"):
        path = "/" + path
    return f"rtsp://{userinfo}@{host}:{port}{path}"


def split_rtsp_url(url: str) -> dict:
    parts = urlsplit(url)
    return {
        "host": parts.hostname or "",
        "port": parts.port or 554,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "path": parts.path or "/",
    }


def save_cameras(cameras: list[Camera], path: Path = DEFAULT_CONFIG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    lines = ["# Wyze View cameras. Managed by the app; safe to edit by hand.", ""]
    for camera in cameras:
        lines += ["[[camera]]", f"name = {json.dumps(camera.name)}",
                  f"url = {json.dumps(camera.url)}"]
        if camera.sub_url:
            lines.append(f"sub_url = {json.dumps(camera.sub_url)}")
        lines.append("")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def test_camera(camera: Camera, timeout: float = 15.0) -> tuple[bool, str]:
    if shutil.which("ffprobe") is None:
        return False, "ffprobe not found — install ffmpeg to test cameras"
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-rtsp_transport", "tcp",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height",
                "-of", "csv=p=0", camera.url,
            ],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, redact_url(str(exc))
    ok = result.returncode == 0 and bool(result.stdout.strip())
    detail = result.stdout.strip() or result.stderr.strip()
    if not detail:
        return ok, ""
    return ok, redact_url(detail.splitlines()[-1])
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: all pass (existing 5 + 6 new)

- [ ] **Step 5: Commit**

```bash
git add wyze_view/config.py tests/test_config.py
git commit -m "feat: add config writes, URL helpers, and camera test"
```

---

### Task 3: Application shell, menus, actions, X11 backend

**Files:**
- Create: `wyze_view/application.py`
- Create: `tests/test_backend.py`
- Modify: `wyze_view/__main__.py`
- Modify: `wyze_view/app.py` (`MainWindow` becomes `Gtk.ApplicationWindow`; add menu bar and window actions)

**Interfaces:**
- Consumes: `load_settings`, `Settings`, `DEFAULT_SETTINGS`, `load_cameras`, `MainWindow`.
- Produces:
  - `APP_ID: str = "io.github.robwestbrook.WyzeView"`
  - `VERSION: str = "0.1.0"`
  - `preferred_backend(session_type: str | None) -> str`
  - `WyzeViewApp(config_path: Path = DEFAULT_CONFIG, settings_path: Path = DEFAULT_SETTINGS)`
  - `MainWindow(application: WyzeViewApp, config_path: Path, settings: Settings)`

- [ ] **Step 1: Write the failing backend test**

Create `tests/test_backend.py`:

```python
from wyze_view.application import preferred_backend


def test_backend_is_always_x11():
    assert preferred_backend("wayland") == "x11"
    assert preferred_backend("x11") == "x11"
    assert preferred_backend(None) == "x11"
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `.venv/bin/pytest tests/test_backend.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wyze_view.application'`

- [ ] **Step 3: Implement `wyze_view/application.py`**

```python
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
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `.venv/bin/pytest tests/test_backend.py -v`
Expected: 1 passed

- [ ] **Step 5: Convert `MainWindow` to a `Gtk.ApplicationWindow` with a menu bar**

In `wyze_view/app.py`, change the import block to also import `Gio`:

```python
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk  # noqa: F401
```

Change the class and constructor signature, and replace the header-button block with a header bar + menu bar:

```python
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
```

Add the menu/action helpers as new methods:

```python
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
```

Note: `reload/snapshot/toggle_mute/toggle_fullscreen` already exist and stay; the header buttons are removed (their behaviour now lives in the menu).

- [ ] **Step 6: Rewrite `__main__.py` to use the application and X11 backend**

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wyze-view")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--check", action="store_true", help="validate config and probe each camera"
    )
    args = parser.parse_args(argv)

    if args.check:
        return check(args.config)

    import os

    from .application import preferred_backend

    os.environ.setdefault("GDK_BACKEND", preferred_backend(os.environ.get("XDG_SESSION_TYPE")))

    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    result = Gtk.init_check(None)
    ok = result[0] if isinstance(result, tuple) else result
    if not ok:
        print(
            "Wyze View needs X11 or XWayland; pure-Wayland embedding is not "
            "supported yet.",
            file=sys.stderr,
        )
        return 1

    from .application import WyzeViewApp

    app = WyzeViewApp(args.config)
    return app.run([sys.argv[0]])
```

Remove the now-unused `shutil`/`Gtk` mpv check from `main` (it moved into `WyzeViewApp.do_startup`). Keep `check()` unchanged.

- [ ] **Step 7: Run the full suite and a headless import**

Run: `.venv/bin/pytest -q` and `.venv/bin/python -c "import wyze_view.app, wyze_view.application"`
Expected: all tests pass; import clean.

- [ ] **Step 8: Manual smoke (controller/user)**

Launch under X11: `.venv/bin/python -m wyze_view`. Verify the window opens with a **File/View/Camera menu bar**, Reload/Snapshot/Mute/Fullscreen work from the menu, `Ctrl+Q` quits leaving no mpv, and launching twice raises the existing window.

- [ ] **Step 9: Commit**

```bash
git add wyze_view/application.py wyze_view/__main__.py wyze_view/app.py tests/test_backend.py
git commit -m "feat: add Gtk.Application shell with menus and actions"
```

---

### Task 4: Camera manager dialog

**Files:**
- Create: `wyze_view/dialogs/__init__.py` (empty)
- Create: `wyze_view/dialogs/camera_manager.py`
- Modify: `wyze_view/app.py` (add `manage-cameras` action + File menu entry)

**Interfaces:**
- Consumes: `Camera`, `save_cameras`, `split_rtsp_url`, `compose_rtsp_url`, `test_camera`, `redact_url`.
- Produces: `CameraManagerDialog(parent, cameras: list[Camera], on_save: Callable[[list[Camera]], None])` with `.run()`.

- [ ] **Step 1: Create `wyze_view/dialogs/__init__.py`**

```python
"""GTK dialogs for Wyze View."""
```

- [ ] **Step 2: Implement `wyze_view/dialogs/camera_manager.py`**

```python
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

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
    ) -> None:
        super().__init__(title="Cameras", transient_for=parent, modal=True)
        self.set_default_size(640, 480)
        self.on_save = on_save
        self.cameras: list[Camera] = list(cameras)
        self.full_url_mode = False

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
        for row, field in enumerate(FIELDS):
            grid.attach(Gtk.Label(label=labels[field], xalign=0.0), 0, row, 1, 1)
            entry = Gtk.Entry()
            entry.set_text(values[field])
            if field == "password":
                entry.set_visibility(False)
                entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            entries[field] = entry
            grid.attach(entry, 1, row, 1, 1)
        result = Gtk.Label(label="", xalign=0.0)
        dialog.get_content_area().add(grid)
        dialog.get_content_area().add(result)
        dialog.show_all()

        while True:
            response = dialog.run()
            if response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.DELETE_EVENT):
                break
            camera = self._camera_from(entries)
            if camera is None:
                result.set_text("Name and host are required")
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
            return {field: ("554" if field == "port" else
                            ("/stream0/" if field == "path" else
                             ("/stream1/" if field == "sub_path" else "")))
                    for field in FIELDS}
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
        }

    def _camera_from(self, entries: dict[str, Gtk.Entry]) -> Camera | None:
        values = {field: entry.get_text() for field, entry in entries.items()}
        if not values["name"].strip() or not values["host"].strip():
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
        return Camera(values["name"].strip(), url, sub_url)

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
```

Note: `test_camera` blocks up to 15 s; the `events_pending` pump is a minimal way to show "Testing…" without a worker thread. Acceptable for a manual dialog.

- [ ] **Step 3: Wire the action in `app.py`**

Add to `_add_actions`:

```python
        action = Gio.SimpleAction.new("manage-cameras", None)
        action.connect("activate", lambda *_: self._manage_cameras())
        self.add_action(action)
        self.get_application().set_accels_for_action(
            "win.manage-cameras", ["<Primary>n"]
        )
```

Add to the File menu (in `_build_menu_bar`, before Reload):

```python
        file_menu.append(self._menu_item("Add Camera…", "manage-cameras"))
        file_menu.append(self._menu_item("Manage Cameras…", "manage-cameras"))
```

Add the method:

```python
    def _manage_cameras(self) -> None:
        from .dialogs.camera_manager import CameraManagerDialog

        def on_save(cameras: list[Camera]) -> None:
            from .config import save_cameras

            try:
                save_cameras(cameras, self.config_path)
            except OSError as exc:
                self._set_status(f"Could not save cameras: {exc}")
                return
            self.reload()

        CameraManagerDialog(self, self.cameras, on_save).run()
```

- [ ] **Step 4: Full suite + import check**

Run: `.venv/bin/pytest -q` and `.venv/bin/python -c "import wyze_view.dialogs.camera_manager"`
Expected: tests pass; import clean.

- [ ] **Step 5: Manual smoke (controller/user)**

Open File ▸ Add Camera…, add a camera with host/user/pass, Test shows OK, Save writes the file and the tile appears; Edit/Remove/Move Up/Down behave.

- [ ] **Step 6: Commit**

```bash
git add wyze_view/dialogs wyze_view/app.py
git commit -m "feat: add in-app camera manager"
```

---

### Task 5: Preferences dialog and window geometry

**Files:**
- Create: `wyze_view/dialogs/preferences.py`
- Modify: `wyze_view/app.py` (add `preferences` action; grid columns; snapshot dir; start-fullscreen; save geometry on close)

**Interfaces:**
- Consumes: `Settings`, `save_settings`, `grid_dims`.
- Produces: `PreferencesDialog(parent, settings: Settings, on_save: Callable[[Settings], None])` with `.run()`.

- [ ] **Step 1: Implement `wyze_view/dialogs/preferences.py`**

```python
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
        self.settings.start_fullscreen = self.start_fullscreen.get_active()
        self.settings.unmute_on_enlarge = self.unmute.get_active()
        self.settings.grid_columns = int(self.columns.get_active_id() or "0")
        self.settings.autostart = self.autostart.get_active()
        return self.settings

    def run_and_save(self) -> None:
        if self.run() == Gtk.ResponseType.OK:
            self.on_save(self.result_settings())
        self.destroy()
```

Replace the `__import__("pathlib")` line with a normal `from pathlib import Path` at the top and `Path(self.folder.get_filename())`.

- [ ] **Step 2: Implement autostart toggle in `app.py`**

Add near the top of `app.py`:

```python
AUTOSTART_DESKTOP = Path.home() / ".config" / "autostart" / "wyze-view.desktop"
AUTOSTART_CONTENT = (
    "[Desktop Entry]\nType=Application\nName=Wyze View\n"
    "Exec=wyze-view\nIcon=wyze-view\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"
)


def apply_autostart(enabled: bool) -> None:
    if enabled:
        AUTOSTART_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_DESKTOP.write_text(AUTOSTART_CONTENT)
    else:
        AUTOSTART_DESKTOP.unlink(missing_ok=True)
```

- [ ] **Step 3: Wire preferences + grid columns + snapshot dir + geometry in `app.py`**

Add to `_add_actions`:

```python
        action = Gio.SimpleAction.new("preferences", None)
        action.connect("activate", lambda *_: self._preferences())
        self.add_action(action)
        self.get_application().set_accels_for_action(
            "win.preferences", ["<Primary>p"]
        )
```

Add to the File menu before Quit:

```python
        file_menu.append(self._menu_item("Preferences", "preferences"))
```

Add the method:

```python
    def _preferences(self) -> None:
        from .dialogs.preferences import PreferencesDialog
        from .settings import save_settings

        def on_save(settings) -> None:
            self.settings = settings
            save_settings(settings, self.get_application().settings_path)
            apply_autostart(settings.autostart)
            self._layout()

        PreferencesDialog(self, self.settings, on_save).run_and_save()
```

Change `_layout` to honour `grid_columns` (replace the `rows, cols = grid_dims(...)` line):

```python
        rows, cols = grid_dims(len(self.cameras))
        if self.settings.grid_columns:
            cols = min(self.settings.grid_columns, len(self.cameras))
            rows = -(-len(self.cameras) // cols)
```

Change `snapshot()` to use `self.settings.snapshot_dir` (replace the `directory = ...` line):

```python
        directory = self.settings.snapshot_dir
```

Change `_on_destroy` to save geometry before quitting (insert before `Gtk.main_quit()`):

```python
        width, height = self.get_size()
        self.settings.window_width = width
        self.settings.window_height = height
        from .settings import save_settings

        save_settings(self.settings, self.get_application().settings_path)
```

Change `__init__` to apply `start_fullscreen` (after `self.add(box)`):

```python
        if settings.start_fullscreen:
            self.fullscreen()
```

- [ ] **Step 4: Full suite + import check**

Run: `.venv/bin/pytest -q` and `.venv/bin/python -c "import wyze_view.dialogs.preferences"`
Expected: tests pass; import clean.

- [ ] **Step 5: Manual smoke (controller/user)**

Open Preferences: change grid columns to 2 and see the layout change; set the snapshot folder and confirm snapshots land there; toggle Autostart and check `~/.config/autostart/wyze-view.desktop`; resize the window, quit, relaunch and confirm the size is restored.

- [ ] **Step 6: Commit**

```bash
git add wyze_view/dialogs/preferences.py wyze_view/app.py
git commit -m "feat: add preferences and window geometry persistence"
```

---

### Task 6: Tile feedback — OSD name, offline overlay, empty state, status, tooltips

**Files:**
- Modify: `wyze_view/player.py` (add `set_volume`, `info`, and `--osd-playing-msg`)
- Modify: `wyze_view/app.py` (offline overlay, empty state, status summary, tooltips)
- Test: `tests/test_player.py`

**Interfaces:**
- Consumes: `Player`, `redact_url`.
- Produces:
  - `Player.set_volume(volume: float) -> None` (0–100)
  - `Player.info() -> dict` with keys `width`, `height`, `fps`
  - `build_mpv_args` includes `--osd-playing-msg=<name>` and `--osd-level=1`

- [ ] **Step 1: Write failing player tests**

Append to `tests/test_player.py`:

```python
def test_mpv_args_show_camera_name_via_osd(tmp_path):
    args = build_mpv_args(1234, tmp_path / "a.sock", "rtsp://u:p@1.2.3.4/x", "Front Door")
    assert "--osd-level=1" in args
    assert "--osd-playing-msg=Front Door" in args


def test_set_volume_sends_volume_property(tmp_path, monkeypatch):
    cam = Camera("A", "rtsp://u:p@1.2.3.4/x")
    player = Player(cam, 1, tmp_path / "a.sock")
    player._proc = types.SimpleNamespace(poll=lambda: None)
    calls = []
    monkeypatch.setattr("wyze_view.player.ipc_command",
                        lambda sock, cmd, timeout=3.0: calls.append(cmd) or {"error": "success"})
    player.set_volume(70)
    assert calls == [["set_property", "volume", 70.0]]


def test_info_reads_properties(tmp_path, monkeypatch):
    cam = Camera("A", "rtsp://u:p@1.2.3.4/x")
    player = Player(cam, 1, tmp_path / "a.sock")
    player._proc = types.SimpleNamespace(poll=lambda: None)
    values = {"width": {"data": 1920}, "height": {"data": 1080},
              "estimated-vf-fps": {"data": 20.0}}
    monkeypatch.setattr("wyze_view.player.ipc_command",
                        lambda sock, cmd, timeout=3.0: values[cmd[1]])
    assert player.info() == {"width": 1920, "height": 1080, "fps": 20.0}
```

Add `import types` to the top of `tests/test_player.py` if not present.

- [ ] **Step 2: Run tests to confirm they fail**

Run: `.venv/bin/pytest tests/test_player.py -v`
Expected: FAIL (`TypeError: build_mpv_args() takes 3 positional arguments` / `AttributeError: 'Player' object has no attribute 'set_volume'`).

- [ ] **Step 3: Implement in `wyze_view/player.py`**

Change `build_mpv_args` signature and body:

```python
def build_mpv_args(xid: int, socket_path: Path, url: str, name: str = "") -> list[str]:
    args = [
        "mpv",
        f"--wid={xid}",
        "--no-config",
        "--no-border",
        "--no-osc",
        "--osd-level=1",
        f"--input-ipc-server={socket_path}",
        "--rtsp-transport=tcp",
        "--hwdec=auto-safe",
        "--mute=yes",
        "--screenshot-format=jpg",
        "--really-quiet",
        "--force-window=yes",
    ]
    if name:
        args.append(f"--osd-playing-msg={name}")
    args.append(url)
    return args
```

Update `_spawn` to pass the name:

```python
            build_mpv_args(self.xid, self.socket_path, self._url, self.camera.name),
```

Add methods after `set_paused`:

```python
    def set_volume(self, volume: float) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                ipc_command(self.socket_path, ["set_property", "volume", float(volume)])
            except (OSError, TimeoutError):
                pass

    def info(self) -> dict:
        result = {"width": 0, "height": 0, "fps": 0.0}
        if not (self._proc and self._proc.poll() is None):
            return result
        for prop, key, cast in (
            ("width", "width", int), ("height", "height", int),
            ("estimated-vf-fps", "fps", float),
        ):
            try:
                reply = ipc_command(self.socket_path, ["get_property", prop])
                result[key] = cast(reply.get("data") or 0)
            except (OSError, TimeoutError, TypeError, ValueError):
                pass
        return result
```

Note: the existing `test_build_mpv_args_includes_required_flags` calls `build_mpv_args(1234, sock, url)`; the new `name` parameter defaults to `""`, so it still passes.

- [ ] **Step 4: Implement tile feedback in `app.py`**

In `reload()`, replace the tile-creation loop with one that adds an offline overlay and a tooltip:

```python
        for camera in self.cameras:
            tile = Gtk.EventBox()
            tile.get_style_context().add_class("tile")
            tile.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            tile.connect("button-press-event", self._on_tile_click, camera.name)
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
```

Add `self.offline_overlays: dict[str, Gtk.Widget] = {}` and `self.offline: set[str] = set()` in `__init__`, and clear them in `reload()` alongside `self.tiles.clear()`.

Add `_reconnect` and update `_on_state` / `_update_status`:

```python
    def _reconnect(self, name: str) -> None:
        player = self.players.get(name)
        if player:
            player.stop()
            player.start()

    def _update_status(self) -> None:
        audio = "none"
        if self.enlarged and not self.enlarged_muted:
            audio = self.enlarged
        parts = [f"{len(self.cameras)} cameras", f"{len(self.offline)} offline"]
        if self.enlarged:
            parts.append(f"selected: {self.enlarged}")
        parts.append(f"audio: {audio}")
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
```

Replace `_on_state` with:

```python
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
            info = player.info()
            if info["width"]:
                self.tiles[name].set_tooltip_text(
                    f"{name} · {info['width']}x{info['height']} · "
                    f"{info['fps']:.0f} fps"
                )
```

Replace the `self._set_status(f"{len(self.cameras)} cameras")` calls at the end of `reload()` and `_collapse()`, and the "Viewing X" in `_enlarge`, with `self._update_status()`. Keep the "No cameras configured" message path, but also render the empty state.

Empty state in `reload()` (replace the `if not self.cameras:` block):

```python
        self.cameras = cameras
        if not self.cameras:
            self._show_empty_state()
            return
```

Add:

```python
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
```

Import `redact_url` in `app.py`: change the util import to
`from .util import redact_url, sanitize_name, snapshot_path`.

- [ ] **Step 5: Full suite + import check**

Run: `.venv/bin/pytest -q` and `.venv/bin/python -c "import wyze_view.app"`
Expected: tests pass; import clean.

- [ ] **Step 6: Manual smoke (controller/user)**

Each playing tile shows its camera name briefly via mpv OSD; hover shows name/resolution/fps; kill a camera's network and confirm the tile shows the name + Reconnect and the status reads "N cameras · 1 offline"; with zero cameras, the empty state with the Add button appears.

- [ ] **Step 7: Commit**

```bash
git add wyze_view/player.py wyze_view/app.py tests/test_player.py
git commit -m "feat: add tile feedback, offline overlay, and empty state"
```

---

### Task 7: Tile interaction — right-click menu, keyboard nav, volume, snapshot dialog

**Files:**
- Modify: `wyze_view/app.py`
- Modify: `tests/test_player.py` (volume already covered in Task 6; no new pure logic here)

**Interfaces:**
- Consumes: `Player.set_volume`, `save_cameras`, `snapshot_path`.
- Produces: tile context menu; `self.selected` index; volume scale in the header bar; snapshot confirmation dialog with Open folder.

- [ ] **Step 1: Add the tile context menu and keyboard selection**

Add `self.selected = 0` in `__init__` and reset it to `0` in `reload()` next to `self.enlarged = None`.

Update `_on_tile_click` so a click also selects the tile (replace the method body):

```python
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
```

In `reload()`, connect the right-click and add:

```python
    def _on_tile_right_click(self, widget, event, name: str) -> bool:
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        for label, callback in (
            ("Enlarge", lambda: self._enlarge(name)),
            ("Snapshot", lambda: self.snapshot()),
            ("Mute", lambda: self.toggle_mute()),
            ("Reconnect", lambda: self._reconnect(name)),
            ("Remove camera", lambda: self._remove_camera(name)),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _w, cb=callback: cb())
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True
```

In the tile-creation loop, add:

```python
            tile.connect("button-press-event", self._on_tile_right_click, camera.name)
```

Wait — two `button-press-event` handlers on the same widget both fire; the left-click handler returns `False` for button 3 and the right-click handler returns `False` for button 1, so both are safe. Keep both connections.

Add `_remove_camera`:

```python
    def _remove_camera(self, name: str) -> None:
        from .config import save_cameras

        remaining = [c for c in self.cameras if c.name != name]
        try:
            save_cameras(remaining, self.config_path)
        except OSError as exc:
            self._set_status(f"Could not save cameras: {exc}")
            return
        self.reload()
```

Keyboard navigation — replace the arrow/Escape handling in `_on_key` with selection-aware logic (keep `r/s/m/F11`):

```python
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
```

Add `_grid_shape` used by both `_layout` and keyboard nav:

```python
    def _grid_shape(self) -> tuple[int, int]:
        rows, cols = grid_dims(len(self.cameras))
        if self.settings.grid_columns:
            cols = min(self.settings.grid_columns, max(len(self.cameras), 1))
            rows = -(-len(self.cameras) // cols)
        return rows, cols
```

and use it in `_layout` (`rows, cols = self._grid_shape()`).

- [ ] **Step 2: Add the volume slider to the header bar**

In `__init__`, after the header bar is created, add:

```python
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.volume.set_value(100)
        self.volume.set_size_request(120, -1)
        self.volume.set_tooltip_text("Volume (enlarged camera)")
        self.volume.connect("value-changed", self._on_volume)
        header.pack_end(self.volume)
```

Add:

```python
    def _on_volume(self, scale) -> None:
        player = self.players.get(self.enlarged) if self.enlarged else None
        if player:
            player.set_volume(scale.get_value())
```

- [ ] **Step 3: Snapshot confirmation dialog with Open folder**

Replace `snapshot()`'s success path:

```python
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
```

Add:

```python
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
```

- [ ] **Step 4: Full suite + import check**

Run: `.venv/bin/pytest -q` and `.venv/bin/python -c "import wyze_view.app"`
Expected: tests pass; import clean.

- [ ] **Step 5: Manual smoke (controller/user)**

Right-click a tile → Enlarge/Snapshot/Mute/Reconnect/Remove all work; arrow keys + Enter enlarge; the volume slider changes the enlarged camera's loudness; Snapshot shows the path with an Open folder button.

- [ ] **Step 6: Commit**

```bash
git add wyze_view/app.py
git commit -m "feat: add tile context menu, keyboard nav, volume, snapshot dialog"
```

---

### Task 8: About and shortcuts dialogs

**Files:**
- Create: `wyze_view/dialogs/about.py`
- Create: `wyze_view/dialogs/shortcuts.py`
- Modify: `wyze_view/app.py` (add `about`/`shortcuts` actions + Help menu)

**Interfaces:**
- Consumes: `VERSION`, `APP_ID`.
- Produces: `show_about(parent)`, `show_shortcuts(parent)`.

- [ ] **Step 1: Implement `wyze_view/dialogs/about.py`**

```python
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
```

- [ ] **Step 2: Implement `wyze_view/dialogs/shortcuts.py`**

```python
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
```

- [ ] **Step 3: Wire Help menu in `app.py`**

In `_build_menu_bar`, after the Camera menu:

```python
        help_item = Gtk.MenuItem(label="Help")
        help_menu = Gtk.Menu()
        help_menu.append(self._menu_item("Keyboard Shortcuts", "shortcuts"))
        help_menu.append(self._menu_item("About", "about"))
        help_item.set_submenu(help_menu)
        bar.append(help_item)
```

In `_add_actions`:

```python
        for name, handler in (
            ("shortcuts", lambda *_: self._shortcuts()),
            ("about", lambda *_: self._about()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        self.get_application().set_accels_for_action("win.shortcuts", ["F1"])
```

Add methods:

```python
    def _shortcuts(self) -> None:
        from .dialogs.shortcuts import show_shortcuts

        show_shortcuts(self)

    def _about(self) -> None:
        from .dialogs.about import show_about

        show_about(self)
```

- [ ] **Step 4: Full suite + import check**

Run: `.venv/bin/pytest -q` and `.venv/bin/python -c "import wyze_view.dialogs.about, wyze_view.dialogs.shortcuts"`
Expected: tests pass; import clean.

- [ ] **Step 5: Manual smoke (controller/user)**

Help ▸ About shows the icon/version/MIT; Help ▸ Keyboard Shortcuts lists the keys; `F1` opens it.

- [ ] **Step 6: Commit**

```bash
git add wyze_view/dialogs/about.py wyze_view/dialogs/shortcuts.py wyze_view/app.py
git commit -m "feat: add About and shortcuts dialogs"
```

---

### Task 9: Icon assets

**Files:**
- Create: `packaging/icons/wyze-view.svg`
- Create: `packaging/icons/wyze-view-{16,24,32,48,64,128,256}.png`
- Create: `tools/make_icons.sh`
- Modify: `packaging/wyze-view.desktop` (Icon=wyze-view)

**Interfaces:**
- Consumes: nothing.
- Produces: the icon files used by Task 10's packaging and `set_icon_name("wyze-view")`.

- [ ] **Step 1: Write `packaging/icons/wyze-view.svg` (concept H1)**

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <rect x="8" y="8" width="240" height="240" rx="46" fill="#20262e"/>
  <circle cx="128" cy="128" r="102" fill="#0f151b"/>
  <circle cx="128" cy="128" r="94" fill="none" stroke="#2ec4b6" stroke-width="9"/>
  <rect x="78" y="78" width="44" height="44" rx="10" fill="#5c6b7a"/>
  <rect x="134" y="78" width="44" height="44" rx="10" fill="#5c6b7a"/>
  <rect x="78" y="134" width="44" height="44" rx="10" fill="#5c6b7a"/>
  <rect x="134" y="134" width="44" height="44" rx="10" fill="#2ec4b6"/>
</svg>
```

- [ ] **Step 2: Write `tools/make_icons.sh`**

```sh
#!/bin/sh
# Render the app icon PNG set from the SVG source.
set -e
cd "$(dirname "$0")/../packaging/icons"
for size in 16 24 32 48 64 128 256; do
  if [ "$size" -le 24 ]; then
    magick -background none -density 384 wyze-view.svg -resize "${size}x${size}" \
      -alpha on "wyze-view-${size}.png"
  else
    magick -background none -density 384 wyze-view.svg -resize "${size}x${size}" \
      "wyze-view-${size}.png"
  fi
done
echo "rendered icon set"
```

Make it executable and run it:

```bash
chmod +x tools/make_icons.sh
./tools/make_icons.sh
```

Note: ImageMagick's built-in SVG renderer is basic; if the render is poor, install `librsvg2-bin` and switch the script to `rsvg-convert -w "$size" -h "$size" wyze-view.svg -o "wyze-view-${size}.png"`. Verify the 16px and 24px PNGs look crisp; if not, add a simplified small-size SVG (`wyze-view-small.svg` with a thicker ring and no accent tile) and use it for those sizes.

- [ ] **Step 3: Update the desktop file**

Change `packaging/wyze-view.desktop` `Icon=` line to `Icon=wyze-view` and `Exec=` to `Exec=wyze-view`.

- [ ] **Step 4: Verify**

Run: `file packaging/icons/wyze-view-256.png` and view the PNGs.
Expected: valid PNGs; visually matching concept H1.

- [ ] **Step 5: Commit**

```bash
git add packaging/icons tools/make_icons.sh packaging/wyze-view.desktop
git commit -m "feat: add app icon and render script"
```

---

### Task 10: Debian packaging

**Files:**
- Modify: `pyproject.toml` (build-system + packages)
- Create: `debian/control`, `debian/rules`, `debian/changelog`, `debian/copyright`, `debian/install`, `debian/wyze-view.1`
- Create: `packaging/io.github.robwestbrook.WyzeView.metainfo.xml`

**Interfaces:**
- Consumes: everything built so far.
- Produces: an installable `.deb`.

- [ ] **Step 1: Add build-system and packages to `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["wyze_view", "wyze_view.dialogs"]
```

- [ ] **Step 2: Create `debian/control`**

```
Source: wyze-view
Section: video
Priority: optional
Maintainer: Rob Westbrook <robwestbrook@users.noreply.github.com>
Build-Depends: debhelper-compat (= 13), dh-python, python3, python3-setuptools
Standards-Version: 4.6.2
Rules-Requires-Root: no

Package: wyze-view
Architecture: all
Depends: ${python3:Depends}, ${misc:Depends}, python3-gi, gir1.2-gtk-3.0, mpv
Recommends: ffmpeg
Description: live Wyze camera multi-view
 Shows live RTSP feeds from multiple cameras in one window, with a
 click-to-enlarge grid, per-camera audio, snapshots, and fullscreen.
```

- [ ] **Step 3: Create `debian/rules`**

```make
#!/usr/bin/make -f
%:
	dh $@ --with python3 --buildsystem=pybuild
```

(`chmod +x debian/rules`)

- [ ] **Step 4: Create `debian/changelog`, `debian/copyright`, `debian/install`, `debian/wyze-view.1`**

`debian/changelog`:

```
wyze-view (0.1.0-1) unstable; urgency=medium

  * Initial release.

 -- Rob Westbrook <robwestbrook@users.noreply.github.com>  Thu, 23 Oct 2026 12:00:00 -0500
```

`debian/copyright`:

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: wyze-view
Source: https://github.com/robwestbrook/wyze-view

Files: *
Copyright: 2026 Rob Westbrook
License: MIT
```

`debian/install`:

```
packaging/wyze-view.desktop usr/share/applications
packaging/io.github.robwestbrook.WyzeView.metainfo.xml usr/share/metainfo
packaging/icons/wyze-view-256.png usr/share/icons/hicolor/256x256/apps/wyze-view.png
packaging/icons/wyze-view-128.png usr/share/icons/hicolor/128x128/apps/wyze-view.png
packaging/icons/wyze-view-64.png usr/share/icons/hicolor/64x64/apps/wyze-view.png
packaging/icons/wyze-view-48.png usr/share/icons/hicolor/48x48/apps/wyze-view.png
packaging/icons/wyze-view-32.png usr/share/icons/hicolor/32x32/apps/wyze-view.png
packaging/icons/wyze-view-24.png usr/share/icons/hicolor/24x24/apps/wyze-view.png
packaging/icons/wyze-view-16.png usr/share/icons/hicolor/16x16/apps/wyze-view.png
packaging/icons/wyze-view.svg usr/share/icons/hicolor/scalable/apps/wyze-view.svg
```

`debian/wyze-view.1` (a minimal man page):

```
.TH WYZE-VIEW 1 "2026-10-23" "wyze-view 0.1.0" "User Commands"
.SH NAME
wyze-view \- live RTSP multi-view for Wyze cameras
.SH SYNOPSIS
.B wyze-view
[\fB\-\-config\fR \fIPATH\fR] [\fB\-\-check\fR]
.SH OPTIONS
.TP
.B \-\-config \fIPATH\fR
Use an alternate cameras.toml.
.TP
.B \-\-check
Validate the config and probe each camera with ffprobe, then exit.
.SH FILES
.I ~/.config/wyze-view/cameras.toml
.SH SEE ALSO
.BR mpv (1)
```

- [ ] **Step 5: Create `packaging/io.github.robwestbrook.WyzeView.metainfo.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>io.github.robwestbrook.WyzeView</id>
  <name>Wyze View</name>
  <summary>Live RTSP multi-view for your cameras</summary>
  <metadata_license>MIT</metadata_license>
  <project_license>MIT</project_license>
  <launchable type="desktop-id">wyze-view.desktop</launchable>
  <url type="homepage">https://github.com/robwestbrook/wyze-view</url>
  <description>
    <p>Shows live RTSP feeds from multiple cameras in one window, with a
    click-to-enlarge grid, per-camera audio, snapshots, and fullscreen.</p>
  </description>
  <content_rating type="oars-1.1"/>
</component>
```

- [ ] **Step 6: Build and inspect the package**

Run:

```bash
sudo apt-get install -y debhelper dh-python python3-setuptools  # build deps, if missing
dpkg-buildpackage -us -uc -b
ls -l ../wyze-view_0.1.0-1_all.deb
dpkg-deb -c ../wyze-view_0.1.0-1_all.deb
```

Expected: a `.deb` containing `/usr/lib/python3/dist-packages/wyze_view/…`, `/usr/share/applications/wyze-view.desktop`, the icons, the metainfo, and the man page.

- [ ] **Step 7: Lint and install-test**

Run: `lintian ../wyze-view_0.1.0-1_all.deb` (install `lintian` if needed), then install it in a clean environment (container or VM) with `sudo dpkg -i` and launch **Wyze View** from the menu.
Expected: lintian reports no errors; the app launches and depends resolve.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml debian packaging/io.github.robwestbrook.WyzeView.metainfo.xml
git commit -m "build: add Debian packaging and AppStream metadata"
```

---

### Task 11: README, LICENSE, and dev tool

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `tools/xclick.py`
- Modify: `wyze_view/__init__.py` (add `__version__`)

**Interfaces:**
- Consumes: nothing.
- Produces: docs and the committed dev aid.

- [ ] **Step 1: Add `__version__`**

`wyze_view/__init__.py`:

```python
"""Live RTSP multi-view for Wyze cameras."""

__version__ = "0.1.0"
```

- [ ] **Step 2: Create `LICENSE`**

Standard MIT license text, `Copyright (c) 2026 Rob Westbrook`.

- [ ] **Step 3: Create `README.md`**

Sections: what it is; screenshot placeholder; **Install** (`sudo dpkg -i wyze-view_0.1.0-1_all.deb` / build with `dpkg-buildpackage -b`); **Requirements** (X11 or XWayland, mpv; ffmpeg optional); **First run** (add cameras via File ▸ Add Camera…); **Config** (`~/.config/wyze-view/cameras.toml`, hand-editable); **CLI** (`--check`); **Keyboard shortcuts**; **License**.

- [ ] **Step 4: Create `tools/xclick.py`**

Copy the working `libXtst` click injector (the one used during development) with a docstring explaining it drives GUI smoke tests on an X11 display:

```python
"""Dev aid: click at absolute screen coordinates via the XTEST extension.

Usage: python3 tools/xclick.py X Y
Requires an X11 display (not for Wayland-only sessions).
"""
import ctypes
import sys
import time


def click(x: int, y: int) -> None:
    x11 = ctypes.CDLL("libX11.so.6")
    xtst = ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtst.XTestFakeMotionEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong
    ]
    xtst.XTestFakeButtonEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong
    ]
    display = x11.XOpenDisplay(None)
    if not display:
        raise SystemExit("cannot open X display")
    xtst.XTestFakeMotionEvent(display, -1, x, y, 0)
    x11.XFlush(display)
    time.sleep(0.3)
    xtst.XTestFakeButtonEvent(display, 1, 1, 0)
    x11.XFlush(display)
    time.sleep(0.1)
    xtst.XTestFakeButtonEvent(display, 1, 0, 0)
    x11.XFlush(display)
    time.sleep(0.3)
    x11.XCloseDisplay(display)


if __name__ == "__main__":
    click(int(sys.argv[1]), int(sys.argv[2]))
    print("clicked", sys.argv[1], sys.argv[2])
```

- [ ] **Step 5: Full suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add README.md LICENSE tools/xclick.py wyze_view/__init__.py
git commit -m "docs: add README, LICENSE, and dev click tool"
```

---

## Self-Review Notes

- **Spec coverage:** shell/menus/actions/Quit (T3, T8), camera manager (T4), preferences + settings + geometry (T1, T5), usability core eight (T6: name via OSD, offline overlay, empty state, tooltips, status; T7: right-click menu, keyboard nav, volume, snapshot dialog), Wayland/X11 (T3), error surfaces (T3 mpv/display; T6 ffprobe/offline; T5 config error is unchanged from the app's reload; T7 snapshot dialog), packaging + repo hygiene + icon (T9, T10, T11). Keyring/tray/notifications/CI are out of scope by the spec.
- **Interface consistency:** `Settings` fields match across T1/T5; `save_cameras`/`test_camera`/`compose_rtsp_url`/`split_rtsp_url` defined in T2 and consumed in T4/T7; `Player.set_volume`/`info` defined in T6 and consumed in T6/T7; `MainWindow(application, config_path, settings)` set in T3 and used thereafter; `_grid_shape` shared by layout and keyboard nav.
- **Known gap carried from the spec:** config parse errors in the GUI show a status message (T6 keeps this); the spec's "dialog offering Open camera manager" is satisfied by the empty state / status plus the always-available File ▸ Manage Cameras… — an explicit dialog is not added to keep the diff small. If a dedicated error dialog is wanted, add it in T6.
- **Overlay limitation** (GTK cannot draw over mpv's child window) is handled by using mpv's OSD for the playing name and GTK only for the offline overlay, as recorded in the spec.
