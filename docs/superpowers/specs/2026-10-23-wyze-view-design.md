# Wyze View — Live Camera Multi-View Design

Date: 2026-10-23
Status: Approved design, pending spec review

## Goal

A small desktop app on Debian 13 XFCE (X11) that shows live RTSP feeds from
Wyze cameras in one window, with a click-to-enlarge grid, per-camera audio,
snapshots, and fullscreen. Cameras are added by editing a config file.

## Scope

- Direct RTSP from cameras on the LAN; no cloud, no bridge, no go2rtc.
- One window with an auto-fitting grid of all configured cameras.
- Click a tile to enlarge it; Esc or click returns to the grid.
- Audio muted by default; the enlarged camera is unmuted.
- Snapshot the selected/enlarged camera to a JPEG file.
- Fullscreen toggle.
- Add cameras by editing a TOML config, then Reload.

## Non-goals

- Recording, retention, timelines.
- Motion detection or alerts.
- PTZ or any camera control.
- Remote access, authentication, multi-user.
- Roku or TV output (separate go2rtc plan).
- Auto-discovery of cameras.

## Target environment

- Debian 13 (trixie), XFCE, X11 session.
- Intel Celeron N3160, Braswell integrated GPU, 1920x1080 panel.
- Python 3.13 with PyGObject and GTK3 already installed.
- `mpv` installed via apt (the only new dependency).
- Cameras: Wyze Cam Pan v3 and Wyze Cam v3 units exposing built-in RTSP on
  port 554, paths `/stream0/` (main) and `/stream1/` (sub).

## Architecture

```
┌───────────────────────────── GTK main window ─────────────────────────────┐
│ toolbar: Reload  Snapshot  Mute  Fullscreen        status: N cameras      │
│ ┌────────────┐  ┌────────────┐  ┌────────────┐                            │
│ │ DrawingArea│  │ DrawingArea│  │ DrawingArea│   each hosts an mpv child   │
│ │  (mpv #1)  │  │  (mpv #2)  │  │  (mpv #3)  │   embedded via --wid       │
│ └────────────┘  └────────────┘  └────────────┘                            │
└───────────────────────────────────────────────────────────────────────────┘
        ▲ JSON IPC per camera (mute, snapshot, loadfile, quit)
```

Single Python process. For each camera, spawn one `mpv` subprocess with
`--wid=<XID>` pointing at a realized `Gtk.DrawingArea`. mpv handles RTSP,
transport, decode, and audio; the app orchestrates and draws the UI.

Why mpv: robust RTSP reconnect, VA-API hardware decode on Braswell, and
snapshot/mute built in, for very little code. X11 is required for `--wid`
embedding and is the confirmed session type.

## Components

### `config.py`

- Loads `~/.config/wyze-view/cameras.toml`.
- Schema:
  ```toml
  [[camera]]
  name = "Corner"                 # required, unique, non-empty
  url = "rtsp://u:p@host:554/stream0/"   # required
  sub_url = "rtsp://u:p@host:554/stream1/"  # optional, grid stream
  ```
- Validation: skip entries missing `name` or `url`; reject duplicate names;
  warn to stderr/status. Return a list of `Camera` dataclasses.
- On first run, if the config is missing, create it with a commented example
  and a directory mode of 0700 / file mode 0600.
- `--check` support: validate config and probe each URL with ffprobe.

### `player.py`

- `Player` wraps one mpv process.
- Spawn flags: `--wid=<xid>`, `--no-border`, `--no-osc`, `--osd-level=0`,
  `--input-ipc-server=<socket>`, `--rtsp-transport=tcp`, `--hwdec=auto-safe`,
  `--mute=yes`, `--screenshot-format=jpg`, `--really-quiet`.
- IPC commands used: `set_property mute`, `loadfile <url>`, `screenshot-to-file
  <path> video`, `quit`.
- Tracks state: starting, playing, offline, stopped.
- Watchdog: if the process exits unexpectedly or reports no playback after a
  timeout, restart with exponential backoff (2s, 4s, 8s, 16s, 30s cap).
- `stop()` terminates the process and removes its socket.

### `grid.py`

- Pure layout function: `grid_dims(n) -> (rows, cols)`.
  - 1 → 1x1, 2 → 1x2, 3–4 → 2x2, 5–9 → 3x3, 10–16 → 4x4.
  - For n > 16 use `ceil(sqrt(n))` per axis with the last row left-aligned.
- Builds `Gtk.Grid` of `Gtk.DrawingArea` tiles with equal expand/weight.
- Click selects/enlarges: hides other tiles, reloads the selected player with
  the main `url`; second click or Esc restores the grid and reloads `sub_url`
  (or `url` when no sub is configured).
- Recomputes on window resize and camera-count change.

### `app.py`

- `Gtk.Application` / main window with a header bar:
  Reload, Snapshot, Mute, Fullscreen, status label.
- Keyboard: `R` reload config, `S` snapshot, `M` mute toggle, `F11`/`Esc`
  fullscreen/back. Tiles are selected by clicking.
- Reload diff: new cameras spawn players; removed cameras stop and free tiles;
  URL changes restart the affected player.
- Snapshot path: `~/Pictures/cameras/<sanitized-name>-YYYYmmdd-HHMMSS.jpg`;
  create the directory if needed; show the path in the status label.
- If `mpv` is missing, show an error dialog naming `sudo apt install mpv` and
  exit cleanly.

### `__main__.py`

- Arg parsing: `--config PATH`, `--check`.
- Runs the GTK app, or the config/URL check and exits.

## Behavior details

- **Startup:** load config, build grid, spawn players using `sub_url` (fallback
  `url`). All muted.
- **Enlarge:** selected player switches to `url`, is unmuted; others are muted.
- **Return:** selected player returns to `sub_url` and is muted.
- **Audio:** exactly one unmuted player at a time; in grid mode none.
- **Fullscreen:** window fullscreen; grid layout recomputes; no separate mode.
- **Status:** camera count and last error/snapshot path; clears on next action.

## Error handling

- Missing config → create default, show a one-line hint.
- Invalid camera entry → skip, log, show warning in status.
- Wrong credentials/unreachable camera → tile shows "Offline — retrying";
  watchdog retries with backoff.
- mpv crash or IPC failure → respawn that player; others unaffected.
- Snapshot failure → status message; never raises into the GTK loop.
- ffmpeg/ffprobe absence only affects `--check`, reported clearly.

## Testing

Automated (pytest):

- `config.py`: valid parse, optional sub_url, missing name/url, duplicate names,
  default-file creation.
- `grid.py`: `grid_dims` for n = 1..17 and a larger case; last-row alignment.
- Snapshot filename sanitization and timestamp format.

Manual smoke:

- Launch with 3 cameras; grid shows substreams.
- Click a tile: enlarges, plays main stream, unmutes; Esc returns and mutes.
- Snapshot writes a valid JPEG under `~/Pictures/cameras/`.
- Add a fourth camera to the TOML and press Reload; grid becomes 2x2.
- Disconnect a camera's network; tile shows offline and recovers on reconnect.
- F11 fullscreen and back.

## Packaging and run

- Run: `python3 -m wyze_view` from the project, or an executable `wyze-view`
  wrapper on `PATH`.
- Desktop entry: `~/.local/share/applications/wyze-view.desktop`, launched
  manually from the XFCE menu (no autostart).
- Install dependency: `sudo apt install mpv`.
- No systemd service, no root, no network listeners.

## Security

- Config stores RTSP credentials; directory 0700, file 0600.
- URL-encode special characters in credentials (`@` `%40`, `+` `%2B`, etc.).
- App binds nothing; camera access is LAN-only.
- Never log passwords; redact userinfo from errors and `--check` output.

## Risks

- `--wid` embedding is X11-specific; Wayland would need the libmpv render API.
- Three 1080p software decodes would strain the N3160; mitigated by substreams
  in the grid, VA-API (`--hwdec=auto-safe`), and main stream only when enlarged.
- mpv process-per-camera costs ~20–40 MB each; acceptable for a handful of
  cameras, not for dozens.
