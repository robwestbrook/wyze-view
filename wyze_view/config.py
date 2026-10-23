from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config" / "wyze-view" / "cameras.toml"

DEFAULT_TOML = """\
# Wyze View cameras. Add one [[camera]] block per feed.
# url is required; sub_url is optional and used for the CPU-cheap grid view.
#
# [[camera]]
# name = "Corner"
# url = "rtsp://user:pass@192.168.1.165:554/stream0/"
# sub_url = "rtsp://user:pass@192.168.1.165:554/stream1/"
"""


@dataclass(frozen=True)
class Camera:
    name: str
    url: str
    sub_url: str | None = None


def _warn(message: str) -> None:
    print(f"wyze-view: {message}", file=sys.stderr)


def _create_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as fh:
        fh.write(DEFAULT_TOML)
    path.chmod(0o600)


def load_cameras(path: Path = DEFAULT_CONFIG) -> list[Camera]:
    if not path.exists():
        _create_default(path)
        return []

    raw = tomllib.loads(path.read_text())
    entries = raw.get("camera", [])
    if not isinstance(entries, list):
        _warn(f"{path}: 'camera' must be an array of tables")
        return []

    cameras: list[Camera] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            _warn(f"camera #{index}: not a table, skipped")
            continue

        name = entry.get("name")
        url = entry.get("url")
        sub_url = entry.get("sub_url")

        if not isinstance(name, str) or not name.strip():
            _warn(f"camera #{index}: missing name, skipped")
            continue
        if not isinstance(url, str) or not url.strip():
            _warn(f"camera '{name}': missing url, skipped")
            continue
        if not isinstance(sub_url, str) or not sub_url.strip():
            sub_url = None
        if name in seen:
            _warn(f"camera '{name}': duplicate name, skipped")
            continue

        seen.add(name)
        cameras.append(
            Camera(
                name=name.strip(),
                url=url.strip(),
                sub_url=sub_url.strip() if sub_url else None,
            )
        )

    return cameras
