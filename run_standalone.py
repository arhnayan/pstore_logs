"""Entry point for PyInstaller standalone builds."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timezone
from pathlib import Path


def _log_file() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "pstore-monitor" / "startup.log"
        return Path.home() / "AppData" / "Local" / "pstore-monitor" / "startup.log"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pstore-monitor" / "startup.log"
    return Path.home() / ".local" / "share" / "pstore-monitor" / "startup.log"


def _log(message: str) -> None:
    line = message
    print(line, flush=True)
    try:
        path = _log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {line}\n")
    except OSError:
        pass


def _ensure_runtime_layout() -> None:
    if not getattr(sys, "frozen", False):
        return

    exe_dir = Path(sys.executable).resolve().parent
    os.chdir(exe_dir)

    internal_dir = exe_dir / "_internal"
    if not internal_dir.is_dir():
        raise RuntimeError(
            "Missing _internal folder next to pstore-monitor.exe. "
            "Copy the entire unzipped pstore-monitor folder, not just the exe."
        )


def _pause_on_error() -> None:
    if sys.platform != "win32":
        return
    _log("")
    _log(f"Log file: {_log_file()}")
    _log("Press any key to close...")
    os.system("pause")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PowerStore Local Monitor")
    parser.add_argument("--host", help="Bind address (default: 127.0.0.1 or PSTORE_HOST)")
    parser.add_argument("--port", type=int, help="Listen port (default: 9090 or PSTORE_PORT)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    return parser.parse_args()


def main() -> None:
    _ensure_runtime_layout()
    _log("Starting PowerStore Monitor...")
    _log("Loading components (this can take 30-60 seconds on slower PCs)...")

    args = parse_args()

    if args.host:
        os.environ["PSTORE_HOST"] = args.host
    if args.port is not None:
        os.environ["PSTORE_PORT"] = str(args.port)

    import uvicorn

    from app.config import settings

    host = settings.host
    port = settings.port
    url = f"http://{host}:{port}"

    if not args.no_browser:
        def open_browser() -> None:
            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=open_browser, daemon=True).start()

    _log(f"PowerStore Monitor running at {url}")
    _log("Press Ctrl+C to stop.")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log("")
        _log("PowerStore Monitor failed to start:")
        traceback.print_exc()
        _pause_on_error()
        sys.exit(1)
