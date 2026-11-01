# Wyze View

Live RTSP multi-view for Wyze cameras on the Linux desktop. Shows several
camera feeds at once in a grid, with click-to-enlarge, per-camera audio,
snapshots, and fullscreen.

<!-- TODO: add a screenshot at docs/screenshot.png and reference it here -->

## Install

Install the packaged build:

```sh
sudo dpkg -i wyze-view_0.1.0_all.deb
sudo apt-get install -f   # pull in any missing dependencies
```

Or build the package yourself from a checkout:

```sh
sudo apt install debhelper dh-python pybuild-plugin-pyproject python3-setuptools
dpkg-buildpackage -b
sudo dpkg -i ../wyze-view_0.1.0_all.deb
```

## Requirements

- A **X11** session, or **XWayland** on a Wayland session. Pure-Wayland
  embedding is not supported yet.
- **mpv** (required) — `sudo apt install mpv`
- **ffmpeg** (required) — records camera streams, and provides `ffprobe` for
  the `--check` command and the camera connection test in the camera manager.
- GTK 3 and PyGObject (`python3-gi`, `gir1.2-gtk-3.0`), pulled in by the
  package.

## First run

On first launch Wyze View shows an empty grid. Add your cameras through
**File ▸ Add Camera…** (or click **Add your first camera**). Enter a name and
the camera's RTSP URL; an optional sub-stream URL is used for the CPU-cheap
grid view. The app writes them to the config file below.

## Config

Cameras live in `~/.config/wyze-view/cameras.toml`, which is created on first
run and is safe to edit by hand:

```toml
[[camera]]
name = "Corner"
url = "rtsp://user:pass@192.168.1.165:554/stream0/"
sub_url = "rtsp://user:pass@192.168.1.165:554/stream1/"
```

`name` and `url` are required; `sub_url` is optional. Use **File ▸ Reload** to
pick up edits. Preferences are stored alongside it in
`~/.config/wyze-view/settings.toml`.

## Recording

- **Rec** in the header records the selected (enlarged) camera. Toggle it
  again to stop.
- **Camera ▸ Record** does the same. **Record All** starts every camera, and
  **Stop All** stops them.
- Right-click a tile and choose **Record/Stop**.
- Press `Ctrl+T` to start or stop the selected camera.
- To start a camera automatically, tick **Record automatically** for it in
  **Manage Cameras**. This saves `record = true` in `cameras.toml`. The camera
  starts recording on launch and after **File ▸ Reload**. Reload stops all
  recordings first, so manual recordings end there.

Files are named `<name>-<start-date>-<start-time>_<stop-date>-<stop-time>.mkv`,
for example `Corner-2026-11-01-093000_2026-11-01-100512.mkv`.

In **Preferences** you can set:

- **Recordings folder**, default `~/Videos/wyze-view`.
- **Max recording size (MB)**, default `0` (no limit). At the cap, the same
  file is overwritten from the start and recording continues. The file always
  holds the most recent N MB. If the connection drops, the partial file is
  finalized and a new one starts.

To play back recordings, open **File ▸ Recordings…**. It lists the files in
the recordings folder and plays them in mpv.

## CLI

```sh
wyze-view --check            # validate the config and probe every camera
wyze-view --config PATH      # use an alternate cameras.toml
```

`--check` prints an `OK`/`FAIL` line per camera and exits non-zero if any
camera is unreachable.

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `R` | Reload cameras |
| `S` | Snapshot |
| `M` | Mute the enlarged camera |
| `T` | Record / stop the enlarged camera |
| `F11` | Fullscreen |
| `Ctrl+N` | Manage cameras |
| `Ctrl+P` | Preferences |
| `Ctrl+Q` | Quit |
| `Arrows` / `Enter` | Select and enlarge a tile |
| `Esc` | Return to the grid / leave fullscreen |

## License

MIT — see [LICENSE](LICENSE).
