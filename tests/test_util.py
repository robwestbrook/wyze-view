from datetime import datetime
from pathlib import Path

from wyze_view.util import redact_url, sanitize_name, snapshot_path


def test_sanitize_name_replaces_unsafe_characters():
    assert sanitize_name("Front Door") == "Front_Door"
    assert sanitize_name("cam/1: north") == "cam_1_north"


def test_sanitize_name_falls_back_for_empty_result():
    assert sanitize_name("///") == "camera"


def test_snapshot_path_uses_timestamp_and_jpg():
    path = snapshot_path(
        Path("/tmp/pics"), "Front Door", datetime(2026, 10, 23, 14, 5, 9)
    )
    assert path == Path("/tmp/pics/Front_Door-20261023-140509.jpg")


def test_redact_url_hides_credentials():
    assert (
        redact_url("rtsp://user:secret@192.168.1.165:554/stream0/")
        == "rtsp://***@192.168.1.165:554/stream0/"
    )
    assert redact_url("rtsp://192.168.1.165:554/stream0/") == (
        "rtsp://192.168.1.165:554/stream0/"
    )
