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
