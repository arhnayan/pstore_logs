"""Cross-platform and PyInstaller-aware path helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS) / "app"
    return Path(__file__).resolve().parent


def static_dir() -> Path:
    return app_dir() / "static"


def user_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "pstore-monitor"
        return Path.home() / "AppData" / "Local" / "pstore-monitor"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pstore-monitor"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "pstore-monitor"
    return Path.home() / ".local" / "share" / "pstore-monitor"


def user_downloads_dir() -> Path:
    if sys.platform == "win32":
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            return Path(user_profile) / "Downloads"
    return Path.home() / "Downloads"
