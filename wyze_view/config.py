from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .util import redact_url

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
        name = name.strip()
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
                name=name,
                url=url.strip(),
                sub_url=sub_url.strip() if sub_url else None,
            )
        )

    return cameras


def compose_rtsp_url(host: str, port: int, user: str, password: str, path: str) -> str:
    userinfo = f"{quote(user, safe='')}:{quote(password, safe='')}"
    if not path.startswith("/"):
        path = "/" + path
    return f"rtsp://{userinfo}@{host}:{port}{path}"


def split_rtsp_url(url: str) -> dict:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    return {
        "host": parts.hostname or "",
        "port": port or 554,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "path": parts.path or "/",
    }


def save_cameras(cameras: list[Camera], path: Path = DEFAULT_CONFIG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    lines = ["# Wyze View cameras. Managed by the app; safe to edit by hand.", ""]
    for camera in cameras:
        lines += ["[[camera]]", f"name = {json.dumps(camera.name)}",
                  f"url = {json.dumps(camera.url)}"]
        if camera.sub_url:
            lines.append(f"sub_url = {json.dumps(camera.sub_url)}")
        lines.append("")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def test_camera(camera: Camera, timeout: float = 15.0) -> tuple[bool, str]:
    if shutil.which("ffprobe") is None:
        return False, "ffprobe not found — install ffmpeg to test cameras"
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-rtsp_transport", "tcp",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height",
                "-of", "csv=p=0", camera.url,
            ],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, redact_url(str(exc))
    ok = result.returncode == 0 and bool(result.stdout.strip())
    detail = result.stdout.strip() or result.stderr.strip()
    if not detail:
        return ok, ""
    return ok, redact_url(detail.splitlines()[-1])


test_camera.__test__ = False
