from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG, load_cameras
from .util import redact_url


def check(config_path: Path) -> int:
    cameras = load_cameras(config_path)
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

    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    if shutil.which("mpv") is None:
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text="mpv is required",
        )
        dialog.format_secondary_text("Install it with: sudo apt install mpv")
        dialog.run()
        dialog.destroy()
        return 1

    from .app import MainWindow

    window = MainWindow(args.config)
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
