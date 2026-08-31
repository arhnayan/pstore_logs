"""Entry point for PyInstaller standalone builds."""

from __future__ import annotations

import sys
import threading
import time
import webbrowser


def main() -> None:
    import uvicorn

    from app.config import settings

    host = settings.host
    port = settings.port
    url = f"http://{host}:{port}"

    if "--no-browser" not in sys.argv:
        def open_browser() -> None:
            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=open_browser, daemon=True).start()

    print(f"PowerStore Monitor running at {url}")
    print("Press Ctrl+C to stop.")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
