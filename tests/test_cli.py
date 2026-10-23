from wyze_view import __main__ as cli


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_cfg(tmp_path):
    cfg = tmp_path / "cameras.toml"
    cfg.write_text(
        '[[camera]]\n'
        'name = "Corner"\n'
        'url = "rtsp://user:secret@192.168.1.165:554/stream0/"\n'
    )
    return cfg


def test_check_reports_ok_without_leaking_password(tmp_path, monkeypatch, capsys):
    cfg = _write_cfg(tmp_path)
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: _Result(0, "h264,1920,1080\n")
    )
    assert cli.check(cfg) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "secret" not in out
    assert "***" in out


def test_check_returns_nonzero_when_a_camera_fails(tmp_path, monkeypatch, capsys):
    cfg = _write_cfg(tmp_path)
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: _Result(1, "", "connection refused\n")
    )
    assert cli.check(cfg) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_check_redacts_password_in_error_detail(tmp_path, monkeypatch, capsys):
    cfg = _write_cfg(tmp_path)

    def boom(*a, **k):
        raise cli.subprocess.TimeoutExpired(cmd=a[0], timeout=15)

    monkeypatch.setattr(cli.subprocess, "run", boom)
    assert cli.check(cfg) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "secret" not in out


def test_check_with_no_cameras_returns_nonzero(tmp_path, capsys):
    cfg = tmp_path / "empty.toml"
    assert cli.check(cfg) == 1
    assert "No cameras" in capsys.readouterr().out


def test_check_reports_config_error_without_traceback(tmp_path, capsys):
    cfg = tmp_path / "cameras.toml"
    cfg.write_text("[[camera]\nname = broken\n")
    assert cli.check(cfg) == 1
    out = capsys.readouterr().out
    assert "Config error" in out
