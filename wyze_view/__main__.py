from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

from .config import DEFAULT_CONFIG, load_cameras
from .util import redact_url


def check(config_path: Path) -> int:
    try:
        cameras = load_cameras(config_path)
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        print(f"Config error: {exc}")
        return 1
    if not cameras:
        print(f"No cameras in {config_path}")
        return 1

    failed = 0
    for camera in cameras:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-rtsp_transport",
                    "tcp",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,width,height",
                    "-of",
                    "csv=p=0",
                    camera.url,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            ok = result.returncode == 0 and bool(result.stdout.strip())
            detail = result.stdout.strip() or result.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok = False
            detail = str(exc)

        label = "OK  " if ok else "FAIL"
        print(f"{label} {camera.name} {redact_url(camera.url)}")
        if not ok and detail:
            print(f"     {redact_url(detail.splitlines()[-1])}")
        failed += 0 if ok else 1

    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wyze-view")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--check", action="store_true", help="validate config and probe each camera"
    )
    args = parser.parse_args(argv)

    if args.check:
        return check(args.config)

    import os

    from .application import preferred_backend

    os.environ.setdefault("GDK_BACKEND", preferred_backend(os.environ.get("XDG_SESSION_TYPE")))

    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    result = Gtk.init_check(None)
    ok = result[0] if isinstance(result, tuple) else result
    if not ok:
        print(
            "Wyze View needs X11 or XWayland; pure-Wayland embedding is not "
            "supported yet.",
            file=sys.stderr,
        )
        return 1

    from .application import WyzeViewApp

    app = WyzeViewApp(args.config)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
