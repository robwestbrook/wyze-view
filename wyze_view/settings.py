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
    start_fullscreen: bool = False
    unmute_on_enlarge: bool = True
    grid_columns: int = 0
    autostart: bool = False
    window_width: int = 1280
    window_height: int = 720


def load_settings(path: Path = DEFAULT_SETTINGS) -> Settings:
    settings = Settings()
    if not path.exists():
        return settings

    raw = tomllib.loads(path.read_text())
    if isinstance(raw.get("snapshot_dir"), str):
        settings.snapshot_dir = Path(raw["snapshot_dir"]).expanduser()
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
