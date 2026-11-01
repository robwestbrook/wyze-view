from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from wyze_view.config import Camera
from wyze_view.util import redact_url, sanitize_name

RECORDING_EXTENSIONS = (".mkv", ".mp4", ".ts")
_POLL_INTERVAL_S = 1.0
_STOP_WAIT_S = 5.0


def record_filename(name: str, start: datetime, stop: datetime) -> str:
    return (
        f"{sanitize_name(name)}-{start:%Y-%m-%d}-{start:%H%M%S}"
        f"_{stop:%Y-%m-%d}-{stop:%H%M%S}.mkv"
    )


def list_recordings(directory: Path) -> list[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    files = [
        path
        for path in directory.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in RECORDING_EXTENSIONS
    ]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def build_ffmpeg_args(url: str, path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-rtsp_transport", "tcp",
        "-i", url,
        "-c", "copy",
        "-y",
        str(path),
    ]


def backoff_delay(attempt: int) -> float:
    return float(min(5 * 2**attempt, 60))


class Recorder:
    """One ffmpeg stream-copy recorder per camera, with crash retry and a
    size-capped rolling working file."""

    def __init__(
        self,
        camera: Camera,
        directory: Path,
        max_size_mb: int = 0,
        on_state: Callable[[str], None] | None = None,
    ) -> None:
        self.camera = camera
        self.directory = Path(directory)
        self.max_size_mb = max_size_mb
        self.on_state = on_state or (lambda _state: None)
        self.last_error = ""
        self._url = camera.sub_url or camera.url
        self._proc: subprocess.Popen | None = None
        self._work_path: Path | None = None
        self._segment_start: datetime | None = None
        self._generation = 0
        self._attempt = 0
        self._state = "stopped"
        self._lock = threading.Lock()
        self._stderr_tail = b""

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        self._state = state
        self.on_state(state)

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        if shutil.which("ffmpeg") is None:
            self.last_error = "ffmpeg executable not found"
            self._set_state("error")
            return
        self._generation += 1
        generation = self._generation
        self._attempt = 0
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self._begin_segment()
        except OSError as exc:
            self.last_error = redact_url(str(exc))
            self._set_state("error")
            return
        threading.Thread(target=self._watch, args=(generation,), daemon=True).start()

    def stop(self) -> Path | None:
        # Bump and take over the process atomically: a watcher holding the lock
        # re-checks the generation there, so it either breaks before spawning
        # or stop() sees and signals the watcher's new process.
        with self._lock:
            self._generation += 1
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            self._signal_stop(proc)
            self._drain_stderr(proc)
        with self._lock:
            final = self._finalize_locked()
        self._set_state("stopped")
        return final

    def _begin_segment(self) -> None:
        self._segment_start = datetime.now()
        self._work_path = self.directory / (
            f".{sanitize_name(self.camera.name)}-{uuid.uuid4().hex[:8]}.part.mkv"
        )
        self._spawn(self._work_path)
        self._set_state("recording")

    def _spawn(self, path: Path) -> None:
        self._proc = subprocess.Popen(
            build_ffmpeg_args(self._url, path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        stream = getattr(self._proc, "stderr", None)
        if stream is not None:
            try:
                os.set_blocking(stream.fileno(), False)
            except (AttributeError, OSError, ValueError):
                pass

    def _signal_stop(self, proc: subprocess.Popen) -> None:
        try:
            proc.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=_STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            pass

    def _watch(self, generation: int) -> None:
        while generation == self._generation:
            proc = self._proc
            if proc is None:
                break
            self._pump_stderr(proc)
            if proc.poll() is not None:
                self._drain_stderr(proc)
                if generation != self._generation:
                    break  # intentional stop; stop() finalizes the segment
                if not self._handle_exit(generation):
                    break
            elif self._size_exceeded():
                self._restart_for_size_cap(generation)
            time.sleep(_POLL_INTERVAL_S)

    def _handle_exit(self, generation: int) -> bool:
        with self._lock:
            if generation != self._generation:
                return False  # stop() owns finalization and the stopped state
            self._finalize_locked()
            self._set_state("retrying")
        time.sleep(backoff_delay(self._attempt))
        with self._lock:
            if generation != self._generation:
                return False
            self._attempt += 1
            try:
                self._begin_segment()
            except OSError as exc:
                self.last_error = redact_url(str(exc))
                self._set_state("error")
                return False
        return True

    def _size_exceeded(self) -> bool:
        work = self._work_path
        if self.max_size_mb <= 0 or work is None:
            return False
        try:
            return work.stat().st_size >= self.max_size_mb * 1024 * 1024
        except OSError:
            return False

    def _restart_for_size_cap(self, generation: int) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._signal_stop(proc)  # outside the lock: waits up to _STOP_WAIT_S
            self._drain_stderr(proc)
        # Truncate + respawn + _proc assignment are atomic against stop()'s
        # bump + takeover, so stop() either sees the old file untouched or
        # signals the respawned process — never truncates a finalized segment.
        with self._lock:
            if generation != self._generation:
                return
            work = self._work_path
            if work is None:
                return
            try:
                work.write_bytes(b"")  # truncate, not ffmpeg -y
            except OSError:
                pass
            try:
                self._spawn(work)
            except OSError as exc:
                self.last_error = redact_url(str(exc))
                self._set_state("error")

    def _finalize_locked(self) -> Path | None:
        path, start = self._work_path, self._segment_start
        self._work_path = None
        self._segment_start = None
        if path is None or start is None:
            return None
        try:
            nonempty = path.stat().st_size > 0
        except OSError:
            nonempty = False
        if not nonempty:
            path.unlink(missing_ok=True)
            return None
        name = record_filename(self.camera.name, start, datetime.now())
        final = self.directory / name
        counter = 2
        while final.exists():
            final = self.directory / f"{name[:-4]}-{counter}.mkv"
            counter += 1
        try:
            path.replace(final)
        except OSError:
            return None
        return final

    def _pump_stderr(self, proc: subprocess.Popen) -> None:
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        try:
            chunk = os.read(stream.fileno(), 65536)
        except (OSError, ValueError):
            return
        if chunk:
            self._note_stderr(chunk)

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        while True:
            try:
                chunk = os.read(stream.fileno(), 65536)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            self._note_stderr(chunk)

    def _note_stderr(self, data: bytes) -> None:
        parts = (self._stderr_tail + data).split(b"\n")
        self._stderr_tail = parts[-1][:512]
        for line in reversed(parts[:-1]):
            text = line.decode(errors="replace").strip()
            if text:
                self.last_error = redact_url(text)
                return
