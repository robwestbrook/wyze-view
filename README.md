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
- **ffmpeg** (optional) — provides `ffprobe` for the `--check` command and the
  camera connection test in the camera manager.
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
| `F11` | Fullscreen |
| `Ctrl+N` | Manage cameras |
| `Ctrl+P` | Preferences |
| `Ctrl+Q` | Quit |
| `Arrows` / `Enter` | Select and enlarge a tile |
| `Esc` | Return to the grid / leave fullscreen |

## License

MIT — see [LICENSE](LICENSE).
