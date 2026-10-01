"""Workbook theme and table styling taken from samples/Book1.xlsx."""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.colors import Color
from openpyxl.workbook import Workbook

_THEME_PATH = Path(__file__).with_name("vodafone_theme.xml")

# Book1 header is theme dk2 (index 3). Banded rows are that color tinted,
# alternating with theme lt2 (index 2).
HEADER_FILL = PatternFill(fill_type="solid")
HEADER_FILL.fgColor = Color(theme=3, tint=0.0)
BAND_FILL = PatternFill(fill_type="solid")
BAND_FILL.fgColor = Color(theme=3, tint=0.5999938962981048)
ALT_FILL = PatternFill(fill_type="solid")
ALT_FILL.fgColor = Color(theme=2, tint=0.0)

HEADER_FONT = Font(name="Arial", size=16, color="FFFFFFFF")
HEADER_FONT_COMPACT = Font(name="Arial", size=11, bold=True, color="FFFFFFFF")
DATA_FONT = Font(name="Arial", size=11, color=Color(theme=1))
DATA_FONT_BOLD = Font(name="Arial", size=11, bold=True, color=Color(theme=1))
HEADER_ALIGN = Alignment(vertical="center")
DATA_ALIGN = Alignment(horizontal="left", vertical="center")
CENTER_ALIGN = Alignment(horizontal="center", vertical="center")
THIN_BORDER = Border(
    left=Side(style="thin", color="FF000000"),
    right=Side(style="thin", color="FF000000"),
    top=Side(style="thin", color="FF000000"),
    bottom=Side(style="thin", color="FF000000"),
)

# Columns that stay empty on production arrays.
MAX_LATENCY_COLUMNS = frozenset(
    {
        "Max Latency",
        "Max Read Latency",
        "Max Write Latency",
    }
)


def _theme_file() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        bundled = Path(bundled_root) / "app" / "reports" / "vodafone_theme.xml"
        if bundled.is_file():
            return bundled
    return _THEME_PATH


def apply_book_theme(workbook: Workbook) -> None:
    """Attach the Book1 Office theme so theme-indexed colors resolve the same way."""
    workbook.loaded_theme = _theme_file().read_bytes()


def omit_max_latency(columns: list[str]) -> list[str]:
    return [column for column in columns if column not in MAX_LATENCY_COLUMNS]
