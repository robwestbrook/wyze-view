# Multi-Camera Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-camera, display-player-tied recording with a
dedicated-ffmpeg recorder per camera, supporting one-camera, all-camera, and
per-camera auto-start recording, a fixed start/stop filename, rolling
size-capped overwrite, a per-tile REC indicator, and a Recordings dialog that
opens files in mpv.

**Architecture:** A new `wyze_view/recorder.py` module owns one ffmpeg
subprocess per recorded camera, independent of the display `Player`. `app.py`
holds `self.recorders: dict[str, Recorder]` instead of the old single
`self.recording` slot, drives per-camera/all-camera/auto-start controls, and
asks the display `Player` to show a persistent OSD badge on recording tiles.
`Camera` gains an `auto_record` flag read from `cameras.toml`; `Settings`
drops the now-unused filename template.

**Tech Stack:** Python 3.11+, GTK3/PyGObject, mpv (display), ffmpeg (record,
stream copy), pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-recording-design.md`

## Global Constraints

- Python ≥ 3.11, GTK3/PyGObject, X11/XWayland only (unchanged).
- `mpv` stays required for display; `ffmpeg` becomes a required runtime
  dependency (was `Recommends`), used for recording via stream copy only — no
  transcoding.
- Output container is Matroska (`.mkv`); final filenames follow exactly
  `<sanitized-name>-<YYYY-MM-DD>-<HHMMSS>_<YYYY-MM-DD>-<HHMMSS>.mkv`.
- The recordings folder is user-chosen (`settings.record_dir`), created the
  same way the existing snapshot/record code does today (plain
  `mkdir(parents=True, exist_ok=True)`, no special permission bits — unlike
  `cameras.toml`/`settings.toml`, recordings are not credentials).
- `record_max_size_mb <= 0` always means "no limit"; this must hold for 0,
  negative, and missing values.
- Never put credentials in filenames, OSD text, or status messages; ffmpeg
  stderr shown to the user goes through `redact_url` first.
- No automated GTK tests exist anywhere in this codebase today (`app.py` and
  every file in `wyze_view/dialogs/` have zero unit tests). Keep that
  convention: engine code (`recorder.py`, `player.py`, `config.py`) gets
  pytest coverage; GTK wiring in `app.py` and the dialogs gets a manual smoke
  step, matching how `toggle_record`/`_stop_recording` are tested today (not
  at all).

## Review Focus

- A camera with no `sub_url` has recording enabled (manually or via
  auto-record): must fall back to the main `url`, not fail or crash.
- `ffmpeg` is not installed: starting a recording must show a clear status
  message and must not spawn a process or loop forever.
- The user starts the same camera's recording twice in a row (double-click
  the header button, or hits Record All while one camera is already
  recording): must not spawn a second ffmpeg process for that camera.
- `reload()` happens while cameras are recording (config edited via Manage
  Cameras, or the user presses Reload): active recordings must be stopped and
  finalized (renamed) before the old tiles are torn down, and auto-record
  cameras must come back recording exactly once, not duplicated.
- `record_max_size_mb` is `0`, negative, or missing (old config file):
  recording must run unbounded, not stop immediately or crash.

---

## Task 1: Recorder filename and listing helpers

**Files:**
- Create: `wyze_view/recorder.py`
- Test: `tests/test_recorder.py`

**Interfaces:**
- Consumes: `sanitize_name` from `wyze_view/util.py`.
- Produces: `RECORDING_EXTENSIONS: tuple[str, ...]`,
  `record_filename(name: str, start: datetime, stop: datetime) -> str`,
  `list_recordings(directory: Path) -> list[Path]`.

- [ ] **Step 1: Write the failing tests**

```python
import os
from datetime import datetime
from pathlib import Path

from wyze_view.recorder import list_recordings, record_filename


def test_record_filename_uses_sanitized_name_and_start_stop_times():
    start = datetime(2026, 10, 31, 21, 15, 0)
    stop = datetime(2026, 10, 31, 21, 45, 0)
    assert record_filename("Front Door", start, stop) == (
        "Front_Door-2026-10-31-211500_2026-10-31-214500.mkv"
    )


def test_list_recordings_sorts_newest_first_and_skips_hidden_and_other_files(tmp_path):
    old = tmp_path / "A-2026-01-01-000000_2026-01-01-000100.mkv"
    old.write_bytes(b"x")
    os.utime(old, (1000, 1000))
    new = tmp_path / "B-2026-01-02-000000_2026-01-02-000100.mkv"
    new.write_bytes(b"x")
    os.utime(new, (2000, 2000))
    (tmp_path / ".B-working.part.mkv").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    assert list_recordings(tmp_path) == [new, old]


def test_list_recordings_on_missing_directory_returns_empty(tmp_path):
    assert list_recordings(tmp_path / "nope") == []
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_recorder.py -v`
Expected: FAIL — `wyze_view.recorder` does not exist.

- [ ] **Step 3: Implement `wyze_view/recorder.py`**

`record_filename` formats as `f"{sanitize_name(name)}-{start:%Y-%m-%d}-{start:%H%M%S}_{stop:%Y-%m-%d}-{stop:%H%M%S}.mkv"`.
`list_recordings` returns files directly in `directory` whose name does not
start with `.` and whose suffix (case-insensitive) is in
`RECORDING_EXTENSIONS = (".mkv", ".mp4", ".ts")`, sorted by modification time
descending. Return `[]` if `directory` does not exist.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_recorder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wyze_view/recorder.py tests/test_recorder.py
git commit -m "feat: add recording filename and listing helpers"
```

---

## Task 2: Recorder process lifecycle

**Files:**
- Modify: `wyze_view/recorder.py`
- Test: `tests/test_recorder.py`

**Interfaces:**
- Consumes: `Camera` (`name`, `url`, `sub_url`) from `wyze_view/config.py`;
  `record_filename` from Task 1.
- Produces:
  `build_ffmpeg_args(url: str, path: Path) -> list[str]`,
  `backoff_delay(attempt: int) -> float`,
  `class Recorder.__init__(self, camera: Camera, directory: Path, max_size_mb: int = 0, on_state: Callable[[str], None] | None = None) -> None`,
  `Recorder.state` property returning one of
  `"stopped" | "recording" | "retrying" | "error"`,
  `Recorder.start() -> None`,
  `Recorder.stop() -> Path | None` (returns the finalized file path, or
  `None` if nothing non-empty was recorded).

Model `Recorder` on the existing `Player` in `wyze_view/player.py`: a
`_generation` counter invalidates stale watcher threads on `stop()`/`start()`,
mirroring `Player._watch`. Use `subprocess.Popen` (patch point for tests, as
`player_module.subprocess.Popen` is patched in `tests/test_player.py`) and
send `signal.SIGINT` to stop ffmpeg cleanly (not `terminate()`/SIGTERM).
Resolve the recording URL as `camera.sub_url or camera.url`. Each recording
segment writes to a hidden working file in `directory`,
e.g. `.{sanitize_name(camera.name)}-{uuid4().hex[:8]}.part.mkv`, distinct from
the final name built by `record_filename`.

Watcher behavior (poll roughly once per second — use a module-level
`_POLL_INTERVAL_S = 1.0` so tests can monkeypatch it):
- Unexpected process exit while recording → finalize the current segment
  (rename the working file via `record_filename(camera.name, segment_start,
  datetime.now())` if its size is > 0 bytes, otherwise delete it), set state
  `"retrying"`, sleep `backoff_delay(attempt)`, increment attempt, then begin
  a **new** segment (new working file, new `segment_start`).
- `max_size_mb > 0` and the working file's size in MB reaches `max_size_mb` →
  send SIGINT, wait for exit, then restart ffmpeg into the **same** working
  file path with the **same** `segment_start` (ffmpeg's `-y` flag truncates
  it); do not rename and do not touch the retry `attempt` counter.
- `start()` is a no-op if already recording (same reentrancy guard as
  `Player.start()`).
- If `shutil.which("ffmpeg")` is `None`, `start()` sets state `"error"` and
  does not spawn or retry.
- `stop()` increments `_generation` (stopping the watcher), signals and waits
  for the process if running, then finalizes the current segment exactly like
  the unexpected-exit path (rename if non-empty, delete and return `None` if
  empty or never created), and sets state `"stopped"`.

- [ ] **Step 1: Write the failing tests**

```python
import signal
import threading
import time
import types
from pathlib import Path

import pytest

from wyze_view.config import Camera
from wyze_view.recorder import Recorder, backoff_delay, build_ffmpeg_args


def test_build_ffmpeg_args_copies_stream_to_path(tmp_path):
    path = tmp_path / "out.mkv"
    args = build_ffmpeg_args("rtsp://u:p@1.2.3.4/stream1/", path)
    assert args[0] == "ffmpeg"
    assert "-rtsp_transport" in args and "tcp" in args
    assert "-c" in args and "copy" in args
    assert args[-1] == str(path)


def test_backoff_delay_grows_and_caps_at_60():
    assert [backoff_delay(i) for i in range(6)] == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]


def test_recorder_starts_stopped(tmp_path):
    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/x")
    recorder = Recorder(camera, tmp_path)
    assert recorder.state == "stopped"


def test_recorder_uses_sub_url_when_present(tmp_path, monkeypatch):
    import wyze_view.recorder as recorder_module

    seen = {}

    class FakeProc:
        returncode = None
        def poll(self): return None
        def wait(self, timeout=None): return None
        def send_signal(self, sig): self.returncode = 0

    def fake_popen(args, **kwargs):
        seen["args"] = args
        return FakeProc()

    monkeypatch.setattr(recorder_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(recorder_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/main", "rtsp://u:p@1.2.3.4/sub")
    recorder = Recorder(camera, tmp_path)
    recorder.start()
    assert seen["args"][-2] == "rtsp://u:p@1.2.3.4/sub" or "rtsp://u:p@1.2.3.4/sub" in seen["args"]
    recorder.stop()


def test_recorder_falls_back_to_main_url_without_sub_url(tmp_path, monkeypatch):
    import wyze_view.recorder as recorder_module

    seen = {}

    class FakeProc:
        returncode = None
        def poll(self): return None
        def wait(self, timeout=None): return None
        def send_signal(self, sig): self.returncode = 0

    def fake_popen(args, **kwargs):
        seen["args"] = args
        return FakeProc()

    monkeypatch.setattr(recorder_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(recorder_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/main")
    recorder = Recorder(camera, tmp_path)
    recorder.start()
    assert "rtsp://u:p@1.2.3.4/main" in seen["args"]
    recorder.stop()


def test_start_twice_while_recording_spawns_one_process(tmp_path, monkeypatch):
    import wyze_view.recorder as recorder_module

    spawned = []

    class FakeProc:
        returncode = None
        def poll(self): return None
        def wait(self, timeout=None): return None
        def send_signal(self, sig): self.returncode = 0

    def fake_popen(args, **kwargs):
        spawned.append(FakeProc())
        return spawned[-1]

    monkeypatch.setattr(recorder_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(recorder_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/x")
    recorder = Recorder(camera, tmp_path)
    recorder.start()
    recorder.start()
    assert len(spawned) == 1
    assert recorder.state == "recording"
    recorder.stop()


def test_start_without_ffmpeg_sets_error_state_and_does_not_spawn(tmp_path, monkeypatch):
    import wyze_view.recorder as recorder_module

    monkeypatch.setattr(recorder_module.shutil, "which", lambda _: None)
    spawned = []
    monkeypatch.setattr(
        recorder_module.subprocess, "Popen",
        lambda *a, **k: spawned.append(1) or pytest.fail("should not spawn"),
    )

    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/x")
    recorder = Recorder(camera, tmp_path)
    recorder.start()
    assert recorder.state == "error"
    assert spawned == []


def test_stop_renames_nonempty_working_file_to_final_name(tmp_path, monkeypatch):
    import wyze_view.recorder as recorder_module

    class FakeProc:
        returncode = None
        def __init__(self, path):
            Path(path).write_bytes(b"data")
        def poll(self): return None
        def wait(self, timeout=None):
            self.returncode = 0
            return 0
        def send_signal(self, sig): pass

    monkeypatch.setattr(
        recorder_module.subprocess, "Popen",
        lambda args, **kwargs: FakeProc(args[-1]),
    )
    monkeypatch.setattr(recorder_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/x")
    recorder = Recorder(camera, tmp_path)
    recorder.start()
    final = recorder.stop()
    assert final is not None
    assert final.exists()
    assert final.parent == tmp_path
    assert not final.name.startswith(".")
    assert recorder.state == "stopped"


def test_stop_discards_empty_working_file(tmp_path, monkeypatch):
    import wyze_view.recorder as recorder_module

    class FakeProc:
        returncode = None
        def poll(self): return None
        def wait(self, timeout=None):
            self.returncode = 0
            return 0
        def send_signal(self, sig): pass

    monkeypatch.setattr(recorder_module.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(recorder_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/x")
    recorder = Recorder(camera, tmp_path)
    recorder.start()
    final = recorder.stop()
    assert final is None
    assert list(tmp_path.iterdir()) == []


def test_zero_or_negative_max_size_means_no_cap(tmp_path, monkeypatch):
    import wyze_view.recorder as recorder_module

    class FakeProc:
        returncode = None
        def __init__(self, path):
            Path(path).write_bytes(b"0" * (5 * 1024 * 1024))
        def poll(self): return None
        def wait(self, timeout=None): return None
        def send_signal(self, sig): self.returncode = 0

    spawned = []

    def fake_popen(args, **kwargs):
        proc = FakeProc(args[-1])
        spawned.append(proc)
        return proc

    monkeypatch.setattr(recorder_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(recorder_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(recorder_module, "_POLL_INTERVAL_S", 0.01)

    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/x")
    for limit in (0, -5):
        recorder = Recorder(camera, tmp_path, max_size_mb=limit)
        recorder.start()
        time.sleep(0.05)
        assert len(spawned) == 1  # never restarted for size
        recorder.stop()
        spawned.clear()
```

Note: the size-cap truncate-and-restart and unexpected-exit-retry behaviors
run on a background watcher thread; follow the synchronization style already
used in `tests/test_player.py` (`test_stop_then_start_does_not_orphan_previous_watcher`)
— `threading.Event`s and a monkeypatched `time.sleep`/`backoff_delay` — to
write deterministic tests for:

```python
def test_size_cap_restarts_into_same_working_file_without_renaming(tmp_path, monkeypatch):
    ...  # assert exactly one working file exists, its name unchanged across
         # the restart, and recorder.state == "recording" after the restart


def test_unexpected_exit_finalizes_segment_and_starts_a_new_one(tmp_path, monkeypatch):
    ...  # assert a finalized (non-hidden) file from the first segment exists
         # alongside a new hidden working file, and no footage/bytes written
         # before the crash were discarded
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_recorder.py -v`
Expected: FAIL — `Recorder`/`build_ffmpeg_args`/`backoff_delay` not defined.

- [ ] **Step 3: Implement `build_ffmpeg_args`, `backoff_delay`, and `Recorder` in `wyze_view/recorder.py`**

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_recorder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wyze_view/recorder.py tests/test_recorder.py
git commit -m "feat: add per-camera ffmpeg recorder with retry and size cap"
```

---

## Task 3: Camera `auto_record` flag

**Files:**
- Modify: `wyze_view/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Camera.auto_record: bool = False` (new field, keeps the dataclass
  frozen and positional compatibility — add it last). `load_cameras` reads an
  optional `record` boolean per `[[camera]]`; `save_cameras` writes
  `record = true` only when `auto_record` is true.

- [ ] **Step 1: Write the failing tests**

```python
def test_loads_auto_record_flag_when_true(tmp_path):
    cfg = tmp_path / "cameras.toml"
    cfg.write_text(
        '[[camera]]\nname = "Corner"\nurl = "rtsp://u:p@1.2.3.4/x"\nrecord = true\n'
    )
    assert load_cameras(cfg)[0].auto_record is True


def test_auto_record_defaults_to_false_when_absent_or_invalid(tmp_path):
    cfg = tmp_path / "cameras.toml"
    cfg.write_text(
        '[[camera]]\nname = "A"\nurl = "rtsp://u:p@1.2.3.4/x"\n'
        '[[camera]]\nname = "B"\nurl = "rtsp://u:p@1.2.3.5/x"\nrecord = "yes"\n'
    )
    cams = load_cameras(cfg)
    assert [c.auto_record for c in cams] == [False, False]


def test_save_cameras_round_trips_auto_record_flag(tmp_path):
    cfg = tmp_path / "cameras.toml"
    cams = [
        Camera("A", "rtsp://u:p@1.2.3.4/x", auto_record=True),
        Camera("B", "rtsp://u:p@1.2.3.5/x", auto_record=False),
    ]
    save_cameras(cams, cfg)
    assert load_cameras(cfg) == cams
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `auto_record` is an unexpected keyword argument.

- [ ] **Step 3: Implement the `auto_record` field in `wyze_view/config.py`**

Add the field to `Camera`. In `load_cameras`, set
`auto_record=entry.get("record") is True`. In `save_cameras`, after the
`sub_url` line, append `f"record = true"` when `camera.auto_record` is true.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS, and the full suite still passes: `python -m pytest -q`

- [ ] **Step 5: Commit**

```bash
git add wyze_view/config.py tests/test_config.py
git commit -m "feat: add per-camera auto-record flag to cameras.toml"
```

---

## Task 4: Player OSD badge

**Files:**
- Modify: `wyze_view/player.py`
- Test: `tests/test_player.py`

**Interfaces:**
- Produces: `OSD_DURATION_MS: int` (module constant), `Player.show_osd(text: str) -> None`, `Player.clear_osd() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_show_osd_sends_show_text_with_persistent_duration(tmp_path, monkeypatch):
    import wyze_view.player as player_module

    cam = Camera("A", "rtsp://u:p@1.2.3.4/x")
    player = Player(cam, 1, tmp_path / "a.sock")
    player._proc = types.SimpleNamespace(poll=lambda: None)
    calls = []
    monkeypatch.setattr("wyze_view.player.ipc_command",
                        lambda sock, cmd, timeout=3.0: calls.append(cmd) or {"error": "success"})
    player.show_osd("● REC")
    assert calls == [["show-text", "● REC", player_module.OSD_DURATION_MS]]


def test_clear_osd_sends_empty_text(tmp_path, monkeypatch):
    cam = Camera("A", "rtsp://u:p@1.2.3.4/x")
    player = Player(cam, 1, tmp_path / "a.sock")
    player._proc = types.SimpleNamespace(poll=lambda: None)
    calls = []
    monkeypatch.setattr("wyze_view.player.ipc_command",
                        lambda sock, cmd, timeout=3.0: calls.append(cmd) or {"error": "success"})
    player.clear_osd()
    assert calls[0][:2] == ["show-text", ""]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_player.py -v`
Expected: FAIL — `show_osd`/`clear_osd` not defined.

- [ ] **Step 3: Implement `show_osd`/`clear_osd` in `wyze_view/player.py`**

Both send `["show-text", text, OSD_DURATION_MS]` through `ipc_command`,
swallowing `(OSError, TimeoutError)` like the other `set_*` methods.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_player.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wyze_view/player.py tests/test_player.py
git commit -m "feat: add persistent OSD badge to Player"
```

---

## Task 5: Settings cleanup — drop the filename template

**Files:**
- Modify: `wyze_view/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Settings` without `record_filename`; `load_settings` ignores a
  leftover `record_filename` key in an old file rather than erroring.

- [ ] **Step 1: Write the failing test**

```python
def test_old_file_with_record_filename_key_loads_without_error(tmp_path):
    cfg = tmp_path / "settings.toml"
    cfg.write_text('record_filename = "{name}-{date}.mkv"\ngrid_columns = 1\n')
    settings = load_settings(cfg)
    assert settings.grid_columns == 1
    assert not hasattr(settings, "record_filename")
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL — `hasattr(settings, "record_filename")` is currently `True`.

- [ ] **Step 3: Remove `record_filename` from `wyze_view/settings.py`**

Delete the field from `Settings`, its line in `load_settings`, and its line
in `save_settings`. Unknown keys in the TOML (including a stale
`record_filename`) are already ignored by `load_settings`'s explicit
field-by-field reads, so no extra handling is needed beyond removing the
field.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wyze_view/settings.py tests/test_settings.py
git commit -m "refactor: drop the recording filename template setting"
```

---

## Task 6: Camera Manager — per-camera auto-record checkbox

**Files:**
- Modify: `wyze_view/dialogs/camera_manager.py`

**Interfaces:**
- Consumes: `Camera.auto_record` from Task 3.
- Produces: no new module-level functions; `_edit_form` persists
  `auto_record` through a `Gtk.CheckButton`, and `_refresh` marks
  auto-recording cameras in the list.

- [ ] **Step 1: Add a "Record automatically" `Gtk.CheckButton` to `_edit_form`**

Place it below the Name field (visible in both compose and full-URL modes,
unlike `compose_widgets`/`url_widgets`). Initialize from
`self.cameras[index].auto_record` when editing, `False` when adding.

- [ ] **Step 2: Include the flag when building the saved `Camera`**

In both the full-URL branch and the `_camera_from` branch of `_edit_form`,
pass `auto_record=record_check.get_active()` into the `Camera(...)` call.
`_camera_from`'s signature changes to accept the checkbox's value as a
parameter (it does not own the checkbox).

- [ ] **Step 3: Show auto-record status in `_refresh`**

Append `"  [auto]"` to a row's label when `camera.auto_record` is true, so
`Corner  —  rtsp://***@host/stream0/  [auto]`.

- [ ] **Step 4: Manual smoke**

Run `wyze-view`, open Manage Cameras, add a camera with "Record
automatically" checked, verify the list shows `[auto]`, edit it to uncheck,
verify the marker disappears, and verify `cameras.toml` gains/loses
`record = true` accordingly.

- [ ] **Step 5: Commit**

```bash
git add wyze_view/dialogs/camera_manager.py
git commit -m "feat: add record-automatically checkbox to camera manager"
```

---

## Task 7: Preferences — remove the filename field

**Files:**
- Modify: `wyze_view/dialogs/preferences.py`

**Interfaces:**
- Consumes: `Settings` without `record_filename` from Task 5.
- Produces: `PreferencesDialog` with the "Recording filename" row and
  `self.record_name` removed; `result_settings` no longer sets
  `settings.record_filename`.

- [ ] **Step 1: Remove the "Recording filename" label, entry, and grid row**

- [ ] **Step 2: Remove the `self.settings.record_filename = ...` line from `result_settings`**

- [ ] **Step 3: Manual smoke**

Run `wyze-view`, open Preferences, confirm the recordings folder and max size
fields are present and the filename field is gone, save, confirm
`settings.toml` has no `record_filename` line.

- [ ] **Step 4: Commit**

```bash
git add wyze_view/dialogs/preferences.py
git commit -m "ui: remove the recording filename field from preferences"
```

---

## Task 8: Wire recording into `MainWindow`

**Files:**
- Modify: `wyze_view/app.py`

**Interfaces:**
- Consumes: `Recorder` (Task 2), `Camera.auto_record` (Task 3),
  `Player.show_osd`/`clear_osd` (Task 4), `settings.record_dir` /
  `settings.record_max_size_mb` (unchanged fields).
- Produces: `self.recorders: dict[str, Recorder]` (replaces `self.recording`,
  `self._recording_path`, `self._recording_timer`); `toggle_record(name:
  str | None = None) -> None` (unchanged signature, new behavior);
  `record_all() -> None`; `stop_all() -> None`. These three are the
  `win.record`, `win.record-all`, and `win.stop-all` action handlers.

`Recorder.stop()` blocks for up to a few seconds (it waits on the ffmpeg
process and renames a file), so stopping several cameras must not do it
serially on the GTK thread. Non-blocking call sites (`toggle_record`,
`stop_all`, `reload`) may fire a stop per camera on its own daemon thread and
move on. Quit is different: daemon threads can be killed mid-rename when the
process exits, silently losing the last segment, so `_on_destroy` needs a
**blocking, bounded** stop that waits for every recorder to finish finalizing
before the window finishes tearing down.

Replace the single-recording fields in `__init__` with
`self.recorders: dict[str, Recorder] = {}`.

Add two private helpers used by `toggle_record`, `record_all`, `stop_all`,
`reload`, and `_on_destroy`:

- `_start_recording(name: str) -> None`: no-op if `name` already in
  `self.recorders` or `name` not in `self.cameras`; else if
  `shutil.which("ffmpeg")` is `None`, call `self._set_status("ffmpeg is
  required to record — install it with: sudo apt install ffmpeg")` and
  return; else build `Recorder(camera, self.settings.record_dir,
  self.settings.record_max_size_mb, on_state=lambda state, name=name:
  GLib.idle_add(self._on_recorder_state, name, state))`, call `.start()`,
  store it in `self.recorders`, and if `self.players.get(name)` exists and is
  playing, call `player.show_osd("● REC")`.
- `_stop_recording(name: str) -> None`: pop `name` from `self.recorders`; if
  absent, return. Clear the OSD on `self.players.get(name)` if present. Run
  `recorder.stop()` on a daemon `threading.Thread` (it blocks up to a few
  seconds) and report the result with
  `GLib.idle_add(self._show_record_result, str(path), self.settings.record_dir)`
  when a path is returned (reuse the existing `_show_record_result` dialog).

`toggle_record(name=None)` resolves `target` exactly as today (`name or
self.enlarged or self.cameras[self.selected].name`), then calls
`self._stop_recording(target)` if `target in self.recorders` else
`self._start_recording(target)`.

`record_all()` calls `self._start_recording(c.name)` for every camera in
`self.cameras`. `stop_all()` calls `self._stop_recording(name)` for
`list(self.recorders)` (snapshot before mutating).

`_on_recorder_state(name: str, state: str) -> None`: updates the status bar
(`self._update_status()`); on `"error"` also call
`self._set_status(f"{name}: recording error")`.

`_update_status` replaces its `if self.recording:` block with
`if self.recorders: parts.append("REC: " + ", ".join(sorted(self.recorders)))`.

`_on_state` (display-player state callback) gains, in the `state ==
"playing"` branch: `if name in self.recorders: player.show_osd("● REC")`.

`reload()` replaces its old single-recording teardown block (the one
guarded by `if self.recording and self._recording_path:`) with
`for name in list(self.recorders): self._stop_recording(name)`, called
before `for player in self.players.values(): player.stop()`. After
`self.cameras = cameras` is assigned and tiles are built (same point where
`GLib.idle_add(self._start_players)` is called), add
`for camera in self.cameras: if camera.auto_record:
self._start_recording(camera.name)`.

`_on_destroy` must not use the fire-and-forget `_stop_recording`, since its
daemon thread can be killed mid-rename when the process exits. Add
`_stop_all_recorders_blocking(timeout: float = 5.0) -> None`: snapshot
`recorders = list(self.recorders.items())`, clear `self.recorders`, spawn one
non-daemon `threading.Thread(target=recorder.stop)` per entry (clearing that
camera's OSD first if its player exists), `start()` all of them, then
`join(timeout)` each in turn so the total wait is bounded by `timeout`
regardless of camera count. Call this from `_on_destroy` before `for player
in self.players.values(): player.stop()`.

Header `record_button`: replace `_on_record_toggled`'s two branches
(`self.toggle_record()` / `self._stop_recording()`) with a single call to
`self.toggle_record()` — `toggle_record` already decides start vs. stop from
`self.recorders` membership, so the old assumption that only the button
toggles recording (`self.recording`/`self._stop_recording()` pair) no longer
applies.

The button can now fall out of sync with reality when recording is started
or stopped from the `T` key, a menu item, Record All/Stop All, or auto-start
— none of those touch the button. Add `_sync_record_button(self) -> None`:
computes `target = self.enlarged or (self.cameras[self.selected].name if
self.cameras else None)` and sets
`self.record_button.set_active(target in self.recorders)` wrapped in
`self.record_button.handler_block_by_func(self._on_record_toggled)` /
`handler_unblock_by_func(...)` so setting it does not re-trigger
`_on_record_toggled`. Call `self._sync_record_button()` at the end of
`_on_recorder_state`, `_update_status` (selection/enlarge changes already
call `_update_status`), and after `record_all()`/`stop_all()`.

Camera menu and actions: in `_build_menu_bar`, add `camera_menu.append(self._menu_item("Record All", "record-all"))` and
`camera_menu.append(self._menu_item("Stop All", "stop-all"))` after the
existing `"Record"` item. In `_add_actions`, register `record-all` and
`stop-all` as `Gio.SimpleAction`s calling `self.record_all()` /
`self.stop_all()` (no new accelerators).

- [ ] **Step 1: Make the changes described above in `wyze_view/app.py`**

- [ ] **Step 2: Run the full automated suite**

Run: `python -m pytest -q`
Expected: PASS (these changes have no new automated tests of their own —
`app.py` has none today — but must not break `tests/test_backend.py` or
anything else).

- [ ] **Step 3: Manual smoke**

With `ffmpeg` installed and at least two configured cameras:
- Start a single camera's recording via the header Rec button; verify its
  tile shows the `● REC` OSD and the status bar shows `REC: <name>`.
- Click Rec again; verify the file in the recordings folder is named
  `<name>-<start>_<stop>.mkv` and the OSD clears.
- Use Camera ▸ Record All; verify every tile shows the OSD and the status
  bar lists every camera.
- Use Camera ▸ Stop All; verify all files are finalized and OSDs clear.
- Enlarge a camera that is recording; verify the recording continues
  uninterrupted (the recorder is independent of the display player).
- Set a small "Max recording size" in Preferences, record until the cap
  hits; verify the file stops growing near the cap and a new segment is not
  created (same file, same start time).
- Enable "Record automatically" on one camera (Task 6), relaunch the app;
  verify only that camera starts recording on startup.
- Edit `cameras.toml` and press Reload while a camera is recording; verify
  the in-progress file is finalized (not left as a hidden `.part` file) and,
  if that camera is still configured with `record = true`, that it resumes
  recording into a new file.
- Uninstall/rename `ffmpeg` temporarily (or `PATH` trick) and try to record;
  verify a clear status message appears and no crash occurs.
- Start recording on two cameras and quit the app (Ctrl+Q) immediately;
  verify both files on disk are finalized (no leftover hidden `.part` files)
  rather than cut off mid-rename.

- [ ] **Step 4: Commit**

```bash
git add wyze_view/app.py
git commit -m "feat: wire per-camera recorders into the main window"
```

---

## Task 9: Recordings dialog

**Files:**
- Create: `wyze_view/dialogs/recordings.py`
- Modify: `wyze_view/app.py`

**Interfaces:**
- Consumes: `list_recordings` (Task 1).
- Produces: `RecordingsDialog(parent: Gtk.Window, directory: Path) -> Gtk.Dialog`
  with a `run_and_destroy() -> None` method (mirrors `CameraManagerDialog`'s
  lifecycle fix already in place: caller gets a live dialog object and must
  destroy it after `run()`, so this dialog owns that itself to avoid
  repeating the earlier bug).

- [ ] **Step 1: Implement `wyze_view/dialogs/recordings.py`**

`RecordingsDialog` is a `Gtk.Dialog` with a `Gtk.ListBox` (or
`Gtk.TreeView`) populated from `list_recordings(directory)`, each row showing
the filename, size in MB, and modified time. Buttons: **Play** (also
double-click a row), **Open folder**, **Refresh**, **Close**. **Play** spawns
`subprocess.Popen(["mpv", "--force-window=yes", str(path)])` detached
(`stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL`) and reports a status
label on `FileNotFoundError`/`OSError`. **Open folder** reuses the pattern
from `app.py`'s `_show_snapshot_result`
(`Gtk.show_uri_on_window(self, directory.as_uri(), Gdk.CURRENT_TIME)`).
**Refresh** re-runs `list_recordings` and repopulates. `run_and_destroy`
calls `self.run()` then `self.destroy()`.

- [ ] **Step 2: Wire `File ▸ Recordings…` in `wyze_view/app.py`**

In `_build_menu_bar`, add
`file_menu.append(self._menu_item("Recordings…", "recordings"))` (placed
after `"Reload"`, before the separator). In `_add_actions`, register a
`recordings` `Gio.SimpleAction` whose handler imports `RecordingsDialog` and
calls `RecordingsDialog(self, self.settings.record_dir).run_and_destroy()`.

- [ ] **Step 3: Run the full automated suite**

Run: `python -m pytest -q`
Expected: PASS (no new automated tests; `list_recordings` is already covered
by Task 1).

- [ ] **Step 4: Manual smoke**

Record a short clip, open File ▸ Recordings…, verify it is listed with a
plausible size/time, double-click to play it in mpv, use Open folder, use
Refresh after recording another clip.

- [ ] **Step 5: Commit**

```bash
git add wyze_view/dialogs/recordings.py wyze_view/app.py
git commit -m "feat: add Recordings dialog with mpv playback"
```

---

## Task 10: Packaging and docs

**Files:**
- Modify: `debian/control`
- Modify: `README.md`

- [ ] **Step 1: Move `ffmpeg` from Recommends to Depends in `debian/control`**

```
Depends: ${python3:Depends}, ${misc:Depends}, python3-gi, gir1.2-gtk-3.0, mpv, ffmpeg
```

Remove the `Recommends: ffmpeg` line.

- [ ] **Step 2: Document recording in `README.md`**

Add a "Recording" section covering: the header Rec button and Camera ▸
Record/Record All/Stop All menu items, the right-click tile Record/Stop,
auto-record via the camera manager checkbox, the fixed filename format
(`<name>-<start>_<stop>.mkv`), the size cap and rolling-overwrite behavior
from Preferences, and File ▸ Recordings… for playback. Update the
Requirements section: `ffmpeg` is now required, not optional.

- [ ] **Step 3: Rebuild and reinstall the package for manual testing**

```bash
dpkg-buildpackage -b
sudo dpkg -i ../wyze-view_0.1.0_all.deb
sudo apt-get install -f
```

Confirm `wyze-view` launches and the installed version includes recording
(the previously installed `.deb` predates it entirely).

- [ ] **Step 4: Commit**

```bash
git add debian/control README.md
git commit -m "docs: document recording and require ffmpeg"
```
