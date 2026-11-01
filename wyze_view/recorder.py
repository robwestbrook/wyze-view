from __future__ import annotations

from datetime import datetime
from pathlib import Path

from wyze_view.util import sanitize_name

RECORDING_EXTENSIONS = (".mkv", ".mp4", ".ts")


def record_filename(name: str, start: datetime, stop: datetime) -> str:
    return (
        f"{sanitize_name(name)}-{start:%Y-%m-%d}-{start:%H%M%S}"
        f"_{stop:%Y-%m-%d}-{stop:%H%M%S}.mkv"
    )


def list_recordings(directory: Path) -> list[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    files = [
        path
        for path in directory.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in RECORDING_EXTENSIONS
    ]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
