import stat

from wyze_view.config import Camera, load_cameras


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
