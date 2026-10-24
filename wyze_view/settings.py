from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SETTINGS = Path.home() / ".config" / "wyze-view" / "settings.toml"


@dataclass
class Settings:
    snapshot_dir: Path = Path.home() / "Pictures" / "cameras"
    record_dir: Path = Path.home() / "Videos" / "wyze-view"
    record_filename: str = "{name}-{date}.mkv"
    record_max_size_mb: int = 0
    start_fullscreen: bool = False
    unmute_on_enlarge: bool = True
    grid_columns: int = 0
    autostart: bool = False
    window_width: int = 1600
    window_height: int = 900


def load_settings(path: Path = DEFAULT_SETTINGS) -> Settings:
    settings = Settings()
    if not path.exists():
        return settings

    try:
        raw = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return settings
    if isinstance(raw.get("snapshot_dir"), str):
        settings.snapshot_dir = Path(raw["snapshot_dir"]).expanduser()
    if isinstance(raw.get("record_dir"), str):
        settings.record_dir = Path(raw["record_dir"]).expanduser()
    else:
        settings.record_dir = settings.snapshot_dir
    if isinstance(raw.get("record_filename"), str):
        settings.record_filename = raw["record_filename"]
    if isinstance(raw.get("record_max_size_mb"), int):
        settings.record_max_size_mb = raw["record_max_size_mb"]
    for field in ("start_fullscreen", "unmute_on_enlarge", "autostart"):
        if isinstance(raw.get(field), bool):
            setattr(settings, field, raw[field])
    if isinstance(raw.get("grid_columns"), int) and 0 <= raw["grid_columns"] <= 4:
        settings.grid_columns = raw["grid_columns"]
    for field in ("window_width", "window_height"):
        value = raw.get(field)
        if isinstance(value, int) and value > 0:
            setattr(settings, field, value)
    return settings


def save_settings(settings: Settings, path: Path = DEFAULT_SETTINGS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    lines = [
        "# Wyze View settings.",
        f"snapshot_dir = {json.dumps(str(settings.snapshot_dir))}",
        f"record_dir = {json.dumps(str(settings.record_dir))}",
        f"record_filename = {json.dumps(settings.record_filename)}",
        f"record_max_size_mb = {settings.record_max_size_mb}",
        f"start_fullscreen = {str(settings.start_fullscreen).lower()}",
        f"unmute_on_enlarge = {str(settings.unmute_on_enlarge).lower()}",
        f"grid_columns = {settings.grid_columns}",
        f"autostart = {str(settings.autostart).lower()}",
        f"window_width = {settings.window_width}",
        f"window_height = {settings.window_height}",
        "",
    ]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
