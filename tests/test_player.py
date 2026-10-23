import json
import socket
import threading
import time
import types
from pathlib import Path

import pytest

from wyze_view.config import Camera
from wyze_view.player import Player, backoff_delay, build_mpv_args, ipc_command


def _serve_lines(sock_path: Path, lines: list[bytes]) -> threading.Thread:
    def serve():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(sock_path))
            server.listen(1)
            conn, _ = server.accept()
            with conn:
                data = b""
                while b"\n" not in data:
                    data += conn.recv(4096)
                for line in lines:
                    conn.sendall(line)

    thread = threading.Thread(target=serve)
    thread.start()
    deadline = time.monotonic() + 2
    while not sock_path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("fake mpv server did not start")
        time.sleep(0.01)
    return thread


def test_build_mpv_args_includes_required_flags(tmp_path):
    sock = tmp_path / "cam.sock"
    args = build_mpv_args(1234, sock, "rtsp://u:p@1.2.3.4/stream0/")
    assert args[0] == "mpv"
    assert "--wid=1234" in args
    assert f"--input-ipc-server={sock}" in args
    assert "--rtsp-transport=tcp" in args
    assert "--hwdec=auto-safe" in args
    assert "--mute=yes" in args
    assert args[-1] == "rtsp://u:p@1.2.3.4/stream0/"


def test_backoff_delay_grows_and_caps():
    assert [backoff_delay(i) for i in range(6)] == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_ipc_command_sends_json_and_reads_reply(tmp_path):
    sock_path = tmp_path / "mpv.sock"
    received = {}

    def serve():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(sock_path))
            server.listen(1)
            conn, _ = server.accept()
            with conn:
                data = b""
                while b"\n" not in data:
                    data += conn.recv(4096)
                received.update(json.loads(data.split(b"\n", 1)[0]))
                conn.sendall(b'{"error":"success","data":null,"request_id":1}\n')

    thread = threading.Thread(target=serve)
    thread.start()
    while not sock_path.exists():
        pass
    reply = ipc_command(sock_path, ["set_property", "mute", True])
    thread.join(timeout=2)
    assert received["command"] == ["set_property", "mute", True]
    assert reply["error"] == "success"


def test_ipc_command_skips_events_before_reply(tmp_path):
    sock_path = tmp_path / "mpv.sock"
    thread = _serve_lines(
        sock_path,
        [
            b'{"event":"start-file"}\n',
            b'{"event":"file-loaded"}\n',
            b'{"error":"success","data":null,"request_id":1}\n',
        ],
    )
    reply = ipc_command(sock_path, ["get_property", "mute"])
    thread.join(timeout=2)
    assert reply["error"] == "success"


def test_player_state_starts_stopped(tmp_path):
    cam = Camera("A", "rtsp://u:p@1.2.3.4/x")
    player = Player(cam, 1, tmp_path / "a.sock")
    assert player.state == "stopped"


def test_start_twice_while_running_spawns_one_mpv(tmp_path, monkeypatch):
    import wyze_view.player as player_module

    spawned = []
    release = threading.Event()

    class LiveProc:
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            if timeout is not None:
                raise player_module.subprocess.TimeoutExpired("mpv", timeout)
            release.wait(5)
            self.returncode = 0
            return 0

        def terminate(self):
            release.set()
            self.returncode = -15

    def fake_popen(args, **kwargs):
        spawned.append(LiveProc())
        return spawned[-1]

    monkeypatch.setattr(player_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        player_module, "ipc_command", lambda *args, **kwargs: {"error": "success"}
    )

    player = Player(Camera("A", "rtsp://u:p@1.2.3.4/x"), 1, tmp_path / "a.sock")
    player.start()
    player.start()

    assert len(spawned) == 1
    assert player.state == "playing"

    player.stop()
    assert player.state == "stopped"


def test_stop_then_start_does_not_orphan_previous_watcher(tmp_path, monkeypatch):
    import wyze_view.player as player_module

    spawned = []
    sleeping = threading.Event()
    release_sleep = threading.Event()
    second_waiting = threading.Event()
    release_second = threading.Event()
    real_sleep = time.sleep

    class CrashedProc:
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 1
            return 1

        def terminate(self):
            self.returncode = -15

    class LiveProc:
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            if timeout is not None:
                raise player_module.subprocess.TimeoutExpired("mpv", timeout)
            second_waiting.set()
            release_second.wait(5)
            self.returncode = 0
            return 0

        def terminate(self):
            release_second.set()
            self.returncode = -15

    def fake_popen(args, **kwargs):
        proc = CrashedProc() if not spawned else LiveProc()
        spawned.append(proc)
        return proc

    def fake_sleep(delay):
        if delay == 999:
            sleeping.set()
            release_sleep.wait(5)
            return
        real_sleep(delay)

    monkeypatch.setattr(player_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(player_module, "backoff_delay", lambda attempt: 999)
    monkeypatch.setattr(
        player_module,
        "time",
        types.SimpleNamespace(sleep=fake_sleep, monotonic=time.monotonic),
    )
    monkeypatch.setattr(
        player_module, "ipc_command", lambda *args, **kwargs: {"error": "success"}
    )

    player = Player(Camera("A", "rtsp://u:p@1.2.3.4/x"), 1, tmp_path / "a.sock")

    player.start()
    assert sleeping.wait(2)
    player.stop()

    player.start()
    player.socket_path.write_text("new socket")
    assert len(spawned) == 2

    release_sleep.set()
    assert second_waiting.wait(2)
    real_sleep(0.1)

    assert len(spawned) == 2
    assert player.socket_path.exists()

    player.stop()
    assert player.state == "stopped"


def test_snapshot_raises_when_mpv_reports_error(tmp_path):
    sock_path = tmp_path / "mpv.sock"
    thread = _serve_lines(
        sock_path,
        [b'{"error":"failed","data":null,"request_id":1}\n'],
    )
    player = Player(Camera("A", "rtsp://u:p@1.2.3.4/x"), 1, sock_path)
    with pytest.raises(RuntimeError):
        player.snapshot(tmp_path / "shot.jpg")
    thread.join(timeout=2)
