from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .config import Camera


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


def backoff_delay(attempt: int) -> float:
    return float(min(2 ** (attempt + 1), 30))


def ipc_command(socket_path: Path, command: list, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while not socket_path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"mpv IPC socket did not appear: {socket_path}")
        time.sleep(0.05)

    # The socket file appears at bind() time, before listen(), so connect() can
    # be refused; retry until the deadline instead of dropping the command.
    while True:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(socket_path))
            break
        except OSError:
            sock.close()
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"mpv IPC socket not accepting connections: {socket_path}"
                ) from None
            time.sleep(0.05)

    with sock:
        payload = json.dumps({"command": command, "request_id": 1}) + "\n"
        sock.sendall(payload.encode())
        buffer = b""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"mpv IPC reply not received: {socket_path}")
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                raise TimeoutError(
                    f"mpv IPC reply not received: {socket_path}"
                ) from None
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict) and message.get("request_id") == 1:
                    return message
        raise TimeoutError(f"mpv IPC reply not received: {socket_path}")


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
        self._generation = 0
        self._attempt = 0
        self._state = "stopped"

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        self._state = state
        self.on_state(state)

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._generation += 1
        generation = self._generation
        self.socket_path.unlink(missing_ok=True)
        self._spawn()
        threading.Thread(target=self._watch, args=(generation,), daemon=True).start()

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(
            build_mpv_args(self.xid, self.socket_path, self._url, self.camera.name),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._set_state("playing")

    def _watch(self, generation: int) -> None:
        started = time.monotonic()
        while generation == self._generation:
            proc = self._proc
            if proc is None:
                time.sleep(0.1)
                continue
            proc.wait()
            if generation != self._generation:
                break
            if time.monotonic() - started > 30:
                self._attempt = 0
            self._set_state("offline")
            time.sleep(backoff_delay(self._attempt))
            if generation != self._generation:
                break
            self._attempt += 1
            try:
                self.socket_path.unlink(missing_ok=True)
                if generation != self._generation:
                    break
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

    def snapshot(self, path: Path) -> None:
        reply = ipc_command(
            self.socket_path, ["screenshot-to-file", str(path), "video"]
        )
        if reply.get("error") != "success":
            raise RuntimeError(f"mpv snapshot failed: {reply.get('error')}")

    def start_recording(self, path: Path) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                ipc_command(self.socket_path, ["set_property", "stream-record", str(path)])
            except (OSError, TimeoutError) as exc:
                raise RuntimeError(f"mpv recording start failed: {exc}") from exc

    def stop_recording(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                ipc_command(self.socket_path, ["set_property", "stream-record", ""])
            except (OSError, TimeoutError):
                pass

    def stop(self) -> None:
        self._generation += 1
        proc = self._proc
        self._proc = None
        if proc and proc.poll() is None:
            try:
                ipc_command(self.socket_path, ["quit"], timeout=1)
            except (OSError, TimeoutError):
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.terminate()
        self.socket_path.unlink(missing_ok=True)
        self._set_state("stopped")
