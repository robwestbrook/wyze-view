# Wyze View — Distributable Desktop App Design

Date: 2026-10-23
Status: Approved design, pending spec review
Builds on: `2026-10-23-wyze-view-design.md` (the working single-window app)

## Goal

Turn the working Wyze View prototype into a proper, user-friendly desktop app
that an ordinary Debian/Ubuntu user can install and use without editing files:
a real application shell (menus, actions, Quit, single instance), an in-app
camera manager, preferences, clearer per-camera feedback, and a `.deb` package
plus the repository hygiene a public project needs.

## Audience and target

- Public Linux desktop users, distributed as a **Debian `.deb`**.
- Debian 13 (trixie) and Ubuntu 24.04+ (Python ≥ 3.11, GTK3).
- X11 session required for `--wid` embedding; Wayland users run through
  XWayland (see Wayland handling).

## Scope

- Convert the entry point to `Gtk.Application` with `Gio.SimpleAction`s,
  accelerators, a menu bar, and an explicit Quit.
- In-app camera manager: add, edit, remove, test, reorder, rename cameras;
  the app writes the config.
- Preferences dialog with a persisted `settings.toml`.
- Usability: tile name labels, on-tile offline overlay + Reconnect, empty
  state, tile right-click menu, keyboard navigation, status summary, remembered
  window geometry, password show/hide, volume slider, snapshot confirmation
  with Open folder, tile tooltips (codec/resolution/fps).
- `.deb` packaging, desktop entry, icon, AppStream metainfo, man page.
- Repo hygiene: `README.md`, `LICENSE` (MIT), versioning.

## Non-goals

- True Wayland embedding (libmpv render API) — future work.
- Tray/indicator icon and desktop notifications.
- Keyring credential storage (v1 keeps the 0600 config file).
- Cloud, recording, motion detection, PTZ, remote access, auto-discovery.
- GitHub Actions CI (build locally for v1).
- i18n, per-camera fit/crop scaling.

## Architecture

The player layer is unchanged: one `mpv` subprocess per camera embedded via
`--wid`, with the click-through fix already shipped (mpv's child window's X
input shape is cleared so the tile receives clicks). What changes is the shell
around it.

```
WyzeViewApp(Gtk.Application, id="io.github.<owner>.WyzeView")
└── MainWindow(Gtk.ApplicationWindow)
    ├── Gtk.HeaderBar            title + window controls
    ├── Gtk.MenuBar              File / View / Camera / Help
    ├── _Fixed (Gtk.Fixed)       tiles (EventBox + embedded mpv), never reparented
    └── status bar               "N cameras · M offline · audio: <name>"
```

New/changed modules:

- `wyze_view/application.py` — `WyzeViewApp`, actions, accelerators, menus,
  single-instance handling, quit lifecycle.
- `wyze_view/config.py` — keeps `Camera`, `load_cameras`; adds
  `save_cameras(path, cameras)` (atomic, `0700`/`0600`) and
  `test_camera(camera) -> (ok, detail)`.
- `wyze_view/settings.py` — `Settings` dataclass, `load_settings()`,
  `save_settings()`, defaults.
- `wyze_view/dialogs/` — `camera_manager.py`, `preferences.py`, `about.py`,
  `shortcuts.py`.
- `wyze_view/app.py` — `MainWindow` gains menus/actions wiring, tile overlays,
  labels, right-click menu, keyboard nav, volume, tooltips, geometry.

## Application shell

- `application_id = "io.github.<owner>.WyzeView"` (exact owner set once the
  public repo URL is provided). The app id provides single instance: a second
  launch raises the existing window.
- `__main__.py` keeps `--config` and `--check`; otherwise builds and runs the
  application. `GDK_BACKEND=x11` is set here before GTK initializes.

Actions (all available from menus and, where noted, accelerators):

| Action | Accelerator | Behaviour |
|---|---|---|
| `reload` | `R` | re-read config, rebuild grid |
| `snapshot` | `S` | enlarged camera, else first; confirmation dialog |
| `mute` | `M` | toggle enlarged camera |
| `fullscreen` | `F11` | toggle window fullscreen |
| `manage-cameras` | `Ctrl+N` | open camera manager (Add Camera… starts in add mode) |
| `preferences` | `Ctrl+P` | open preferences |
| `shortcuts` | `F1` | shortcut cheat-sheet |
| `about` | — | About dialog (version, MIT, project URL) |
| `quit` | `Ctrl+Q` | teardown, then exit |

Menu bar:

- **File**: Add Camera… · Manage Cameras… · Reload · — · Quit
- **View**: Fullscreen
- **Camera**: Snapshot · Mute
- **Help**: Keyboard Shortcuts · About

## Camera manager

Opened from File ▸ Add Camera… / Manage Cameras… (same dialog, different start).

- List of cameras: name, redacted URL, last test result.
- Buttons: Add, Edit, Remove, Test, Move Up, Move Down, Close.
- Add/Edit form composes the RTSP URL from friendly fields: Name, Host/IP,
  Port (default 554), Username, Password (masked, with a show/hide toggle),
  Main path (`/stream0/`), Sub path (`/stream1/`, optional). A "Paste a full
  URL instead" toggle supports non-Wyze cameras.
- **Test** runs `test_camera` and shows `OK` + codec/resolution, or a redacted
  `FAIL` reason; if `ffprobe` is absent, says "Install ffmpeg to test cameras".
- **Save** validates (non-empty, unique name; non-empty URL), writes via
  `save_cameras`, then `reload()` (full rebuild; no diffing in v1).
- Passwords are masked and never logged; only redacted URLs appear in
  status/errors.

## Preferences and settings

`~/.config/wyze-view/settings.toml` (atomic, `0600`), loaded with defaults so a
missing or partial file is never an error. Not GSettings (avoids a compiled
schema in the package).

| Setting | Default | Effect |
|---|---|---|
| `snapshot_dir` | `~/Pictures/cameras` | where snapshots are written |
| `start_fullscreen` | false | open fullscreen |
| `unmute_on_enlarge` | true | if false, enlarge keeps the camera muted |
| `grid_columns` | 0 (auto) | 0 = `grid_dims`; 1–4 = fixed columns |
| `autostart` | false | toggles `~/.config/autostart/wyze-view.desktop` |
| `window_width` / `window_height` | 1280 / 720 | restored on launch, saved on close |

Autostart writes/removes a user `.desktop` copy; no new mechanism.

## Usability details

- **Tile labels**: camera name in a corner overlay.
- **Offline overlay**: "Offline — retrying (Ns)" on the tile, with Reconnect.
- **Empty state**: with no cameras, a centered "Add your first camera" button.
- **Tile right-click menu**: Enlarge, Snapshot, Mute, Reconnect, Remove camera.
- **Keyboard navigation**: arrow keys move a highlighted tile, `Enter` enlarges,
  `Esc` collapses/returns.
- **Status bar**: "N cameras · M offline · audio: <name>".
- **Volume slider** for the enlarged camera (mpv `volume` property).
- **Snapshot**: success dialog shows the path with an Open folder button;
  failure shows the reason.
- **Tooltips**: codec/resolution/fps from the probe.

## Wayland handling

- `GDK_BACKEND=x11` is set before GTK initializes (also in the `.desktop`
  `Exec`), so the app runs through XWayland on Wayland desktops.
- If the display cannot be opened (no XWayland), show a dialog: "Wyze View
  needs X11 or XWayland; pure-Wayland embedding isn't supported yet." and exit
  cleanly.

## Error handling

- mpv missing → dialog naming `sudo apt install mpv`, with a Copy button.
- ffprobe missing → Test/`--check` report "Install ffmpeg to test cameras".
- Config parse error → dialog offering Open camera manager.
- Snapshot failure → dialog with the reason; never raises into the GTK loop.
- Offline camera → on-tile overlay + Reconnect; no dialog spam.

## Lifecycle

- Single instance via the app id.
- `Ctrl+Q` and window close both run one teardown: stop every player, remove
  the temp dir, close the X display, quit — no orphaned mpv.
- Window geometry saved on close, restored on launch.

## Packaging and repo hygiene

- `pyproject.toml` gains `[build-system]` (setuptools) and package config so it
  is both pip-installable and buildable by `pybuild`.
- `debian/`: `control`, `rules` (`dh $@ --with python3 --buildsystem=pybuild`),
  `changelog` (0.1.0), `copyright`, `install`, `wyze-view.1`, the `.desktop`,
  the icon, and the metainfo file.
- Installed: `wyze_view` → `/usr/lib/python3/dist-packages/`;
  `/usr/bin/wyze-view` → `exec python3 -m wyze_view "$@"`;
  `/usr/share/applications/wyze-view.desktop`;
  `/usr/share/icons/hicolor/256x256/apps/wyze-view.png`;
  `/usr/share/metainfo/io.github.<owner>.WyzeView.metainfo.xml`;
  `/usr/share/man/man1/wyze-view.1`.
- `Depends`: `python3 (>=3.11)`, `python3-gi`, `gir1.2-gtk-3.0`, `mpv`.
  `Recommends`: `ffmpeg`.
- Repo: `README.md` (install, first run, screenshot), `LICENSE` (MIT),
  version `0.1.0`, git tag convention.
- Build: `dpkg-buildpackage -b` locally; `lintian` clean; install into a clean
  Debian 13 environment and launch from the menu.

## Testing

Automated (pytest):

- `config_manager`: save/load round-trip, atomic write + `0700`/`0600`, reorder
  preserved, validation (empty/duplicate name, empty URL).
- `settings`: missing file → defaults, partial file → defaults filled, save
  round-trip, grid-column override.
- `test_camera`: monkeypatched `subprocess` — OK, FAIL, credential redaction.
- Existing 35 tests stay green.

Manual smoke: first-run empty state → add camera + Test → edit/rename/reorder →
offline overlay + Reconnect → click-enlarge + `Esc` → volume → snapshot + Open
folder → `F11` → preferences (grid columns, autostart) → `Ctrl+Q` leaves zero
mpv → second launch raises the window → Wayland/XWayland run.

Packaging: `dpkg-buildpackage`, `lintian`, clean-environment install.

Dev aid: `tools/xclick.py` (the libXtst click injector) committed for driving
GUI smoke steps without `xdotool`.

## Risks

- **Wayland**: only works via XWayland in v1; a pure-Wayland session fails with
  a clear message. Real support needs the libmpv render API (future).
- **Sandbox/portability**: a `.deb` only reaches Debian/Ubuntu users; other
  distros need a future Flatpak/AppImage.
- **Probing on Test**: ffprobe can take up to ~15 s per URL; run it without
  blocking the GTK loop.
- **Config writes**: the manager is now the primary editor; atomic writes and
  validation must not lose a hand-edited file.
- **Scope**: this is a large change set; it will be split into several
  implementation tasks in the plan.

## Open items

- Public GitHub URL (owner/repo) to finalize the application id, metainfo, and
  README links. Placeholder `io.github.<owner>.WyzeView` until provided.
