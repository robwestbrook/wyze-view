import stat

from wyze_view.config import (
    Camera,
    compose_rtsp_url,
    load_cameras,
    save_cameras,
    split_rtsp_url,
    test_camera,
)


def test_loads_minimal_camera(tmp_path):
    cfg = tmp_path / "cameras.toml"
    cfg.write_text(
        '[[camera]]\n'
        'name = "Corner"\n'
        'url = "rtsp://u:p@1.2.3.4:554/stream0/"\n'
    )
    assert load_cameras(cfg) == [
        Camera("Corner", "rtsp://u:p@1.2.3.4:554/stream0/", None)
    ]


def test_loads_optional_sub_url(tmp_path):
    cfg = tmp_path / "cameras.toml"
    cfg.write_text(
        '[[camera]]\n'
        'name = "Corner"\n'
        'url = "rtsp://u:p@1.2.3.4:554/stream0/"\n'
        'sub_url = "rtsp://u:p@1.2.3.4:554/stream1/"\n'
    )
    cams = load_cameras(cfg)
    assert cams[0].sub_url == "rtsp://u:p@1.2.3.4:554/stream1/"


def test_skips_entries_missing_name_or_url(tmp_path, capsys):
    cfg = tmp_path / "cameras.toml"
    cfg.write_text(
        '[[camera]]\n'
        'url = "rtsp://u:p@1.2.3.4/x"\n'
        '[[camera]]\n'
        'name = "NoUrl"\n'
        '[[camera]]\n'
        'name = "Good"\n'
        'url = "rtsp://u:p@1.2.3.5/x"\n'
    )
    cams = load_cameras(cfg)
    assert [c.name for c in cams] == ["Good"]
    assert "missing name" in capsys.readouterr().err


def test_skips_duplicate_names(tmp_path, capsys):
    cfg = tmp_path / "cameras.toml"
    cfg.write_text(
        '[[camera]]\nname = "A"\nurl = "rtsp://u:p@1.2.3.4/x"\n'
        '[[camera]]\nname = "A"\nurl = "rtsp://u:p@1.2.3.5/x"\n'
    )
    cams = load_cameras(cfg)
    assert len(cams) == 1
    assert "duplicate" in capsys.readouterr().err


def test_skips_duplicate_names_differing_only_by_whitespace(tmp_path, capsys):
    cfg = tmp_path / "cameras.toml"
    cfg.write_text(
        '[[camera]]\nname = "Corner"\nurl = "rtsp://u:p@1.2.3.4/x"\n'
        '[[camera]]\nname = " Corner "\nurl = "rtsp://u:p@1.2.3.5/x"\n'
    )
    cams = load_cameras(cfg)
    assert len(cams) == 1
    assert cams[0].name == "Corner"
    assert "duplicate" in capsys.readouterr().err


def test_creates_default_config_with_restricted_permissions(tmp_path):
    cfg = tmp_path / "nested" / "cameras.toml"
    assert load_cameras(cfg) == []
    assert cfg.exists()
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.parent.stat().st_mode) == 0o700


def test_save_cameras_round_trips_with_permissions(tmp_path):
    cfg = tmp_path / "nested" / "cameras.toml"
    cams = [
        Camera("Front Door", "rtsp://u:p@1.2.3.4:554/stream0/", None),
        Camera("Back", "rtsp://u:p@1.2.3.5:554/stream0/", "rtsp://u:p@1.2.3.5:554/stream1/"),
    ]
    save_cameras(cams, cfg)
    assert load_cameras(cfg) == cams
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.parent.stat().st_mode) == 0o700


def test_save_cameras_preserves_order(tmp_path):
    cfg = tmp_path / "cameras.toml"
    cams = [Camera("B", "rtsp://u:p@1.2.3.4/x"), Camera("A", "rtsp://u:p@1.2.3.5/x")]
    save_cameras(cams, cfg)
    assert [c.name for c in load_cameras(cfg)] == ["B", "A"]


def test_compose_rtsp_url_percent_encodes_credentials():
    url = compose_rtsp_url("192.168.1.5", 554, "user", "p@ss:word", "/stream0/")
    assert url == "rtsp://user:p%40ss%3Aword@192.168.1.5:554/stream0/"


def test_split_rtsp_url_decodes_credentials():
    parts = split_rtsp_url("rtsp://user:p%40ss%3Aword@192.168.1.5:554/stream0/")
    assert parts == {
        "host": "192.168.1.5",
        "port": 554,
        "user": "user",
        "password": "p@ss:word",
        "path": "/stream0/",
    }


def test_test_camera_reports_missing_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr("wyze_view.config.shutil.which", lambda _: None)
    ok, detail = test_camera(Camera("A", "rtsp://u:p@1.2.3.4/x"))
    assert ok is False
    assert "ffmpeg" in detail


def test_test_camera_redacts_credentials(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "rtsp://user:secret@1.2.3.4/x: connection refused"

    monkeypatch.setattr("wyze_view.config.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "wyze_view.config.subprocess.run", lambda *a, **k: Result()
    )
    ok, detail = test_camera(Camera("A", "rtsp://user:secret@1.2.3.4/x"))
    assert ok is False
    assert "secret" not in detail
    assert "***" in detail
