from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def sanitize_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return cleaned or "camera"


def snapshot_path(directory: Path, name: str, when: datetime) -> Path:
    return Path(directory) / f"{sanitize_name(name)}-{when:%Y%m%d-%H%M%S}.jpg"


def redact_url(url: str) -> str:
    return re.sub(r"//[^/]*@", "//***@", url)
