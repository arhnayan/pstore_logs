"""Entry point for PyInstaller standalone builds."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import traceback
import webbrowser


def _log(message: str) -> None:
    print(message, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PowerStore Local Monitor")
    parser.add_argument("--host", help="Bind address (default: 127.0.0.1 or PSTORE_HOST)")
    parser.add_argument("--port", type=int, help="Listen port (default: 9090 or PSTORE_PORT)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    return parser.parse_args()


def main() -> None:
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
        if sys.platform == "win32":
            _log("")
            _log("Press Enter to close...")
            try:
                input()
            except EOFError:
                pass
        sys.exit(1)
