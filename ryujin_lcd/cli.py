from __future__ import annotations

import argparse
import json
import sys
import webbrowser

import uvicorn

from .config import Settings, app_dir
from .conflicts import InstanceLock, diagnostic
from .web import create_app


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ASUS ROG Ryujin II LCD dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--diagnostic", action="store_true", help="only print conflict/device diagnostics")
    p.add_argument("--force", action="store_true", help="allow start despite conservative conflict detection")
    p.add_argument("--mock", action="store_true", help="render and test UI without touching USB")
    p.add_argument("--experimental-linux-usb", action="store_true", help="enable reconstructed Linux HID+bulk LCD writes")
    p.add_argument("--auto-start", action="store_true", help="start the LCD engine when the local service starts")
    p.add_argument("--no-browser", action="store_true")
    return p


def main() -> None:
    # PyInstaller's Windows GUI bootloader intentionally provides no console
    # streams. Uvicorn and optional sensor libraries still probe/write them,
    # so attach both to a per-user log instead of opening a command prompt.
    if sys.stdout is None or sys.stderr is None:
        log_dir = app_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = (log_dir / "ryujin-lcd.log").open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    args = parser().parse_args()
    if args.diagnostic:
        print(json.dumps(diagnostic(), ensure_ascii=False, indent=2))
        return
    settings = Settings.load()
    if args.experimental_linux_usb:
        settings.linux_experimental_usb = True
    lock = InstanceLock()
    lock.acquire()
    try:
        if not args.no_browser:
            webbrowser.open(f"http://{args.host}:{args.port}")
        uvicorn.run(
            create_app(settings, mock=args.mock, force=args.force, auto_start=args.auto_start),
            host=args.host, port=args.port, log_level="info",
        )
    finally:
        lock.release()


if __name__ == "__main__":
    main()
