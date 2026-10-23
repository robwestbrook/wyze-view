import json
import socket
import threading
from pathlib import Path

from wyze_view.config import Camera
from wyze_view.player import Player, backoff_delay, build_mpv_args, ipc_command


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
                conn.sendall(b'{"error":"success","data":null}\n')

    thread = threading.Thread(target=serve)
    thread.start()
    while not sock_path.exists():
        pass
    reply = ipc_command(sock_path, ["set_property", "mute", True])
    thread.join(timeout=2)
    assert received["command"] == ["set_property", "mute", True]
    assert reply["error"] == "success"


def test_player_state_starts_stopped(tmp_path):
    cam = Camera("A", "rtsp://u:p@1.2.3.4/x")
    player = Player(cam, 1, tmp_path / "a.sock")
    assert player.state == "stopped"
