# Wyze View — Multi-Camera Recording Design

Date: 2026-10-31
Status: Implemented and shipped in 0.2.0 (2026-11-02)
Builds on: `2026-10-23-wyze-view-design.md` and
`2026-10-23-wyze-view-distributable-design.md`

## Goal

Turn the existing single-camera, manual-only recording into a small recording
system: record one camera or all cameras, start recording automatically for
chosen cameras, cap each recording file's size with rolling overwrite, show
clearly which cameras are recording, and browse/play recordings in mpv.

## Why

Today recording is tied to the display player and one `self.recording` slot.
That cannot record all cameras at once, cannot survive enlarging a camera
(which switches its stream), and has no auto-start or playback. The user wants
hands-off capture that is independent of what the grid is showing.

## Scope

- A dedicated recorder per camera using **ffmpeg stream copy** on the camera's
  sub stream, independent of the display mpv process.
- Per-camera manual record toggle, plus **Record All** and **Stop All**.
- **Auto-start** per camera, chosen with a checkbox in Manage Cameras and
  persisted in `cameras.toml`.
- Configurable maximum recording size in MB. When reached, the same file is
  truncated and recording continues into it (rolling last-N-MB buffer).
- Fixed filename format:
  `<camera-name>-<start-date>-<start-time>_<stop-date>-<stop-time>.mkv`
- Per-tile **REC** indicator via mpv OSD, plus status-bar summary.
- **Recordings** dialog listing files in the recordings folder, with Play
  (opens mpv) and Open folder.
- ffmpeg becomes a required runtime dependency.

## Non-goals

- In-app playback, seeking UI, or an embedded player.
- Scheduled windows, motion-triggered clips, or retention by age/total disk.
- Transcoding, re-encoding, audio extraction, or thumbnail generation.
- Deleting or editing recordings from the app.
- Keeping a history of files per recording session; overwrite means one
  rolling file per camera.
- Recording the main stream when a sub stream exists; cameras without
  `sub_url` record their main `url` as a fallback.

## Anti-goals

- Recording must never depend on which camera is enlarged or on the grid
  layout.
- A failed or offline camera must not stop other cameras' recordings.
- No credentials in logs, status text, or filenames.

## Constraints

- X11/XWayland, GTK3, Python 3.11+, mpv required, ffmpeg now required.
- Target hardware is a Celeron N3160: stream copy only, no decoding.
- Output is Matroska (`.mkv`).
- mpv and ffmpeg cannot truncate a file they are writing, so overwrite is
  implemented as stop → truncate → restart the same working file.
- The camera's sub stream is lower resolution than the main stream.

## Approaches considered

### Recording engine

- **A — record with the display player.** Reuses mpv IPC and adds no process.
  Rejected: enlarging switches the player from sub to main stream, which
  interrupts or corrupts an active recording, and grid recordings depend on
  playback state.
- **B — dedicated recorder per camera (chosen).** One ffmpeg process per
  recorded camera copies the sub stream to disk. Display, enlarge, collapse,
  pause, and reload of the UI do not touch recordings. Cost: one extra
  process and RTSP connection per recorded camera; ffmpeg becomes required.
  Stream copy is cheap on the target hardware.

### Auto-start selection

- Per-camera checkbox stored in `cameras.toml` (chosen): survives renames and
  lives with the camera definition.
- A list of camera names in `settings.toml` (rejected): breaks silently when a
  camera is renamed.

### Playback

- Recordings dialog with a file list and Play in mpv (chosen).
- "Open recordings folder" only (rejected): less code but clunkier, and the
  user asked for playback.

## Design

### Camera model and config

`Camera` gains `auto_record: bool = False`. `cameras.toml` accepts an optional
`record = true` key per `[[camera]]`; `save_cameras` writes it only when true.
Unknown/invalid values are ignored like the existing fields. The camera edit
form in Manage Cameras gets a **Record automatically** checkbox; the list row
may show a small "auto" marker.

### Settings

Keep `record_dir` and `record_max_size_mb`. Remove `record_filename`: the
format is fixed by this design, so the template setting and its Preferences
field are deleted. `record_max_size_mb = 0` means no limit. The Preferences
dialog keeps the recordings-folder chooser and the size spin button.

### Filename and working file

Final name: `<sanitized-name>-<YYYY-MM-DD>-<HHMMSS>_<YYYY-MM-DD>-<HHMMSS>.mkv`
for example `Corner-2026-10-31-211500_2026-10-31-214500.mkv`. The start time is
the session start; it does not change when the size cap overwrites the buffer.

While recording, write to a hidden working file in the recordings folder:
`.<sanitized-name>-<pid>-<start>.part.mkv`. On stop, finalize ffmpeg and rename
to the final name. If the app crashes, the hidden working file remains on disk
and is not listed by the Recordings dialog.

### `wyze_view/recorder.py`

A `Recorder` owns one camera's ffmpeg process and a daemon watcher thread.

- `start()` spawns:
  `ffmpeg -nostdin -loglevel error -rtsp_transport tcp -i <sub_url or url>
   -c copy -f matroska -y <working>`. Cameras without a sub stream fall back to
  the main URL, matching the display player.
- The watcher polls the process and the working file's size about once per
  second:
  - unexpected exit while active → finalize and rename the current working
    file (if non-empty) with the stop time of the drop, then retry with
    backoff (5 s, 10 s, 20 s, 40 s, 60 s cap) into a **new** working file with
    a new start time. No footage is discarded; one recording session can
    become several files if the camera drops repeatedly.
  - `max_size_mb > 0` and size ≥ limit → SIGINT, wait for exit, truncate the
    working file, restart into the same path with the same start time.
    Earlier footage in that file is overwritten by design (this is the only
    intentional data loss in the system).
- `stop()` signals SIGINT, waits up to 2 s, kills if needed, then renames the
  working file to the final name and returns that path. If the working file is
  missing or zero bytes, it is discarded instead of renamed, and the recorder
  returns `None`.
- State callbacks (`recording`, `retrying`, `stopped`, `error`) are marshalled
  to GTK with `GLib.idle_add`.
- ffmpeg stderr is captured, redacted with `redact_url`, and surfaced only as a
  short status message.

A module-level `record_filename(name, start, stop)` builds the final name, and
`list_recordings(directory)` returns non-hidden `.mkv`/`.mp4`/`.ts` files
sorted newest first for the dialog.

### App wiring (`app.py`)

- Replace `self.recording`/`self._recording_path` with
  `self.recorders: dict[str, Recorder]`.
- `toggle_record(name)` starts or stops that camera's recorder. The header Rec
  button and `T` act on the selected/enlarged camera and reflect that camera's
  state.
- `record_all()` starts recorders for every configured camera; `stop_all()`
  stops all. Both are Camera-menu actions; right-click tile keeps
  Record/Stop.
- After `reload()` and on startup, start recorders for cameras with
  `auto_record` set. Reload stops all recorders first (finalizing files), then
  re-arms auto-start cameras; this is documented in the README.
- On quit/destroy, stop all recorders so files are finalized and renamed.
  Stops run concurrently (one thread per recorder, joined with a short total
  timeout) so reload and quit are not blocked per camera.
- Status bar lists recording cameras: `REC: Corner, Cam2`. If ffmpeg is
  missing, starting a recording reports that clearly.

### Per-tile REC indicator

GTK widgets do not reliably draw over mpv's embedded child X window, so the
indicator is drawn by mpv itself: `Player` gains `show_osd(text)` /
`clear_osd()`, sending `show-text` with `duration=0` (persistent) or `""` to
clear. `app.py` tracks `self.recording_names: set[str]` and calls
`show_osd("● REC")` / `clear_osd()` on that camera's **display** player
whenever: a recorder starts or stops, the display player's state callback
fires `playing` (covers reload, enlarge/collapse reload, and reconnect after
offline), so the OSD is re-applied any time the display mpv process is
replaced. If the camera is offline, the tile shows its Offline overlay and the
status bar still lists the recording; the OSD is reapplied once the player
reports `playing` again.

### Recordings dialog (`wyze_view/dialogs/recordings.py`)

`File ▸ Recordings…` opens a modal dialog listing `list_recordings(record_dir)`
with name, size, and modified time, newest first. Buttons: **Play** (also
double-click), **Open folder**, **Refresh**, **Close**. Play spawns a detached
`mpv --force-window=yes <path>`; failures are reported in the dialog. No
delete or rename actions.

### Failure handling

- ffmpeg missing → status message, no recording started.
- Camera unreachable → recorder retries with backoff; other cameras unaffected.
- Disk full or write error → ffmpeg exits; retry/backoff; error in status.
- Rename failure at stop → keep the working file, report its path.
- Invalid or zero limit → no size cap.
- Recorder stop during reload/quit → file finalized and renamed before exit.

### Security

Recordings live in the user-chosen directory; the default stays under the
user's home. The recordings folder is created with the existing 0700 directory
helper. URLs and credentials never appear in filenames, status text, or OSD;
ffmpeg error text is redacted before display.

## Testing

Automated:

- `record_filename`: sanitization, date/time format, `.mkv` extension.
- `list_recordings`: filters extensions and hidden files, newest first.
- Config: `record = true` round-trips; absent/false stays false; camera manager
  preserves the flag on edit.
- Settings: removed `record_filename` still loads old files without error.
- Recorder: command construction, truncate-and-restart at the size cap, final
  rename on stop, finalize-and-start-new-file on an unexpected exit (with a
  fake process/runner), zero-byte stop is discarded not renamed.

Manual smoke:

- Record one camera; verify file name, playback in mpv, and tile REC marker.
- Record All; verify all tiles show REC and all files grow.
- Enlarge a recording camera; verify the recording continues.
- Set a small limit; verify the file stops growing and restarts (size stays
  near the cap) and playback shows the recent portion.
- Enable auto-record on two cameras, relaunch, verify only those start.
- Open Recordings, play a file, open the folder.

## Packaging and docs

- `debian/control`: move `ffmpeg` from Recommends to Depends.
- README: recording behavior, auto-start, size cap, file naming, playback, and
  that ffmpeg is required.
- Rebuild and reinstall the `.deb`; the installed 0.1.0 package predates all
  recording code.

## Left to the builder

- Exact ffmpeg flags for low-latency start and clean finalization.
- Backoff timings and watcher cadence.
- OSD styling and placement details.
- Whether the Recordings dialog shows duration (requires probing files).
