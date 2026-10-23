import stat

from wyze_view.settings import Settings, load_settings, save_settings


def test_missing_file_returns_defaults(tmp_path):
    settings = load_settings(tmp_path / "nope.toml")
    assert settings == Settings()


def test_partial_file_fills_defaults(tmp_path):
    cfg = tmp_path / "settings.toml"
    cfg.write_text("grid_columns = 3\nstart_fullscreen = true\n")
    settings = load_settings(cfg)
    assert settings.grid_columns == 3
    assert settings.start_fullscreen is True
    assert settings.window_width == 1280
    assert settings.unmute_on_enlarge is True


def test_save_round_trip_with_restricted_permissions(tmp_path):
    cfg = tmp_path / "nested" / "settings.toml"
    original = Settings(
        snapshot_dir=tmp_path / "pics",
        start_fullscreen=True,
        unmute_on_enlarge=False,
        grid_columns=2,
        autostart=True,
        window_width=800,
        window_height=600,
    )
    save_settings(original, cfg)
    assert load_settings(cfg) == original
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.parent.stat().st_mode) == 0o700


def test_malformed_file_returns_defaults(tmp_path):
    cfg = tmp_path / "settings.toml"
    cfg.write_text("this is = = not toml\n")
    assert load_settings(cfg) == Settings()


def test_invalid_values_are_ignored(tmp_path):
    cfg = tmp_path / "settings.toml"
    cfg.write_text('grid_columns = 9\nwindow_width = "wide"\n')
    settings = load_settings(cfg)
    assert settings.grid_columns == 0
    assert settings.window_width == 1280
