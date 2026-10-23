from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .config import Camera


def build_mpv_args(xid: int, socket_path: Path, url: str) -> list[str]:
    return [
        "mpv",
        f"--wid={xid}",
        "--no-config",
        "--no-border",
        "--no-osc",
        "--osd-level=0",
        f"--input-ipc-server={socket_path}",
        "--rtsp-transport=tcp",
        "--hwdec=auto-safe",
        "--mute=yes",
        "--screenshot-format=jpg",
        "--really-quiet",
        "--idle=yes",
        "--force-window=yes",
        url,
    ]


def backoff_delay(attempt: int) -> float:
    return float(min(2 ** (attempt + 1), 30))


def ipc_command(socket_path: Path, command: list, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while not socket_path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"mpv IPC socket did not appear: {socket_path}")
        time.sleep(0.05)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        payload = json.dumps({"command": command, "request_id": 1}) + "\n"
        sock.sendall(payload.encode())
        buffer = b""
        while b"\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
        line = buffer.split(b"\n", 1)[0]
        return json.loads(line or b"{}")


class Player:
    def __init__(
        self,
        camera: Camera,
        xid: int,
        socket_path: Path,
        on_state: Callable[[str], None] | None = None,
    ) -> None:
        self.camera = camera
        self.xid = xid
        self.socket_path = Path(socket_path)
        self.on_state = on_state or (lambda _state: None)
        self._url = camera.sub_url or camera.url
        self._proc: subprocess.Popen | None = None
        self._stopping = False
        self._attempt = 0
        self._state = "stopped"

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        self._state = state
        self.on_state(state)

    def start(self) -> None:
        self._stopping = False
        self.socket_path.unlink(missing_ok=True)
        self._spawn()
        threading.Thread(target=self._watch, daemon=True).start()

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(
            build_mpv_args(self.xid, self.socket_path, self._url),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._set_state("playing")

    def _watch(self) -> None:
        started = time.monotonic()
        while not self._stopping:
            if self._proc is None:
                time.sleep(0.1)
                continue
            self._proc.wait()
            if self._stopping:
                break
            if time.monotonic() - started > 30:
                self._attempt = 0
            self._set_state("offline")
            time.sleep(backoff_delay(self._attempt))
            if self._stopping:
                break
            self._attempt += 1
            try:
                self.socket_path.unlink(missing_ok=True)
                self._spawn()
                started = time.monotonic()
            except OSError:
                self._set_state("offline")

    def load(self, url: str) -> None:
        self._url = url
        if self._proc and self._proc.poll() is None:
            try:
                ipc_command(self.socket_path, ["loadfile", url, "replace"])
            except (OSError, TimeoutError):
                pass

    def set_muted(self, muted: bool) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                ipc_command(self.socket_path, ["set_property", "mute", muted])
            except (OSError, TimeoutError):
                pass

    def set_paused(self, paused: bool) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                ipc_command(self.socket_path, ["set_property", "pause", paused])
            except (OSError, TimeoutError):
                pass

    def snapshot(self, path: Path) -> None:
        ipc_command(self.socket_path, ["screenshot-to-file", str(path), "video"])

    def stop(self) -> None:
        self._stopping = True
        if self._proc and self._proc.poll() is None:
            try:
                ipc_command(self.socket_path, ["quit"], timeout=1)
            except (OSError, TimeoutError):
                pass
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
        self.socket_path.unlink(missing_ok=True)
        self._set_state("stopped")
