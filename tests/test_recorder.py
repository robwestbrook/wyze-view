import os
import signal
import threading
import time
import types
from datetime import datetime
from pathlib import Path

import pytest

from wyze_view.config import Camera
from wyze_view.recorder import (
    Recorder,
    backoff_delay,
    build_ffmpeg_args,
    list_recordings,
    record_filename,
)


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
        def poll(self): return self.returncode
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
        def poll(self): return self.returncode
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
        def poll(self): return self.returncode
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
        def poll(self): return self.returncode
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
        def poll(self): return self.returncode
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
        def poll(self): return self.returncode
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


def test_size_cap_restarts_into_same_working_file_without_renaming(tmp_path, monkeypatch):
    import wyze_view.recorder as recorder_module

    spawned = []
    signals = []
    restarted = threading.Event()

    class FakeProc:
        def __init__(self, path):
            self.returncode = None
            self.path = path
            # First proc overshoots the 1 MB cap; the respawn stays under it.
            Path(path).write_bytes(b"x" * (2 * 1024 * 1024) if not spawned else b"x")

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def send_signal(self, sig):
            signals.append(sig)
            self.returncode = 0

    def fake_popen(args, **kwargs):
        proc = FakeProc(args[-1])
        spawned.append(proc)
        if len(spawned) == 2:
            restarted.set()
        return proc

    monkeypatch.setattr(recorder_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(recorder_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(recorder_module, "_POLL_INTERVAL_S", 0.01)

    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/x")
    recorder = Recorder(camera, tmp_path, max_size_mb=1)
    recorder.start()
    assert restarted.wait(2), "ffmpeg was not restarted for the size cap"

    assert signals[0] == signal.SIGINT
    assert len(spawned) == 2
    assert spawned[0].path == spawned[1].path  # same working file, not renamed
    assert Path(spawned[1].path).read_bytes() == b"x"  # truncated before respawn
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name.startswith(".")
    assert recorder.state == "recording"

    recorder.stop()


def test_unexpected_exit_finalizes_segment_and_starts_a_new_one(tmp_path, monkeypatch):
    import wyze_view.recorder as recorder_module

    spawned = []
    in_backoff = threading.Event()
    release_backoff = threading.Event()
    real_sleep = time.sleep

    class FakeProc:
        def __init__(self, path, crash):
            self.returncode = None
            self.path = path
            self._crash = crash
            Path(path).write_bytes(b"footage")

        def poll(self):
            if self._crash:
                self.returncode = 1  # dies on the watcher's first poll
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def send_signal(self, sig):
            self.returncode = 0

    def fake_popen(args, **kwargs):
        proc = FakeProc(args[-1], crash=not spawned)
        spawned.append(proc)
        return proc

    def fake_sleep(delay):
        if delay == 999:
            in_backoff.set()
            release_backoff.wait(5)
            return
        real_sleep(delay)

    monkeypatch.setattr(recorder_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(recorder_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(recorder_module, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(recorder_module, "backoff_delay", lambda attempt: 999)
    monkeypatch.setattr(
        recorder_module,
        "time",
        types.SimpleNamespace(sleep=fake_sleep, monotonic=time.monotonic),
    )

    camera = Camera("Corner", "rtsp://u:p@1.2.3.4/x")
    recorder = Recorder(camera, tmp_path)
    recorder.start()

    assert in_backoff.wait(2), "watcher never entered backoff after the crash"
    assert recorder.state == "retrying"

    finals = [p for p in tmp_path.iterdir() if not p.name.startswith(".")]
    assert len(finals) == 1
    assert finals[0].name.endswith(".mkv")
    assert finals[0].read_bytes() == b"footage"  # no bytes discarded

    release_backoff.set()
    deadline = time.monotonic() + 2
    while len(spawned) < 2 and time.monotonic() < deadline:
        real_sleep(0.01)
    assert len(spawned) == 2
    assert spawned[1].path != spawned[0].path  # new working file for the new segment

    hidden = [p for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert len(hidden) == 1
    assert hidden[0] == Path(spawned[1].path)
    assert finals[0].read_bytes() == b"footage"  # first segment still intact
    assert recorder.state == "recording"

    assert recorder.stop() is not None
    assert recorder.state == "stopped"
