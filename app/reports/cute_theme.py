"""Pink pastel override for generated Excel reports."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

HEADER_FILL = PatternFill(start_color="FFFF8FAB", end_color="FFFF8FAB", fill_type="solid")
TITLE_FILL = PatternFill(start_color="FFFFC2D4", end_color="FFFFC2D4", fill_type="solid")
WASH_FILL = PatternFill(start_color="FFFFF0F6", end_color="FFFFF0F6", fill_type="solid")
CUTE_BORDER = Border(
    left=Side(style="thin", color="FFF4ACB7"),
    right=Side(style="thin", color="FFF4ACB7"),
    top=Side(style="thin", color="FFF4ACB7"),
    bottom=Side(style="thin", color="FFF4ACB7"),
)
ROW_COLORS = (
    "FFFDE2E4",
    "FFFAD2E1",
    "FFE2ECE9",
    "FFBEE1E6",
    "FFFFF1E6",
    "FFE5D4EF",
    "FFFFE5EC",
    "FFD8F3DC",
)
CONFETTI = (
    "FFFF8FAB",
    "FFCDB4DB",
    "FFA2D2FF",
    "FFFFC8DD",
    "FFBDE0FE",
    "FFFFB5A7",
    "FFC3F0CA",
    "FFF9C6D0",
)
TAB_COLORS = (
    "FF8FAB",
    "CDB4DB",
    "A2D2FF",
    "FFC8DD",
    "BDE0FE",
    "FFB5A7",
    "C3F0CA",
    "F9C6D0",
)
CHART_COLORS = TAB_COLORS
SPRINKLES = ("🌸", "✨", "💖", "🎀", "🌷", "💗", "⭐", "🍓", "🩷", "🫧")
SCATTER = (
    (2, 2),
    (3, 8),
    (4, 14),
    (6, 5),
    (7, 11),
    (9, 2),
    (12, 15),
    (14, 4),
    (16, 12),
    (18, 7),
    (21, 3),
    (23, 14),
    (26, 6),
    (28, 10),
    (31, 2),
    (33, 13),
)


def apply_huseyin_special(path: str | Path) -> None:
    """Restyle a generated workbook, or every workbook inside a zip."""
    target = Path(path)
    if target.suffix.lower() == ".zip":
        _paint_zip(target)
        return
    if target.suffix.lower() == ".xlsx":
        _paint_xlsx(target)


def _paint_xlsx(path: Path) -> None:
    workbook = load_workbook(path)
    _paint_workbook(workbook)
    workbook.save(path)


def _paint_bytes(data: bytes) -> bytes:
    workbook = load_workbook(io.BytesIO(data))
    _paint_workbook(workbook)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _paint_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as source:
        members = [(info.filename, source.read(info.filename)) for info in source.infolist()]
    painted: list[tuple[str, bytes]] = []
    for name, data in members:
        if name.lower().endswith(".xlsx"):
            data = _paint_bytes(data)
            sibling = path.parent / Path(name).name
            if sibling.is_file():
                sibling.write_bytes(data)
        painted.append((name, data))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in painted:
            archive.writestr(name, data)


def _paint_workbook(workbook: Workbook) -> None:
    for index, worksheet in enumerate(workbook.worksheets):
        _paint_worksheet(worksheet, index)


def _paint_worksheet(worksheet: Worksheet, index: int) -> None:
    worksheet.sheet_properties.tabColor = TAB_COLORS[index % len(TAB_COLORS)]
    worksheet.sheet_view.showGridLines = False
    worksheet.oddHeader.left.text = "🌸"
    worksheet.oddHeader.center.text = "Hüseyin Becerikli Special"
    worksheet.oddHeader.center.font = "Comic Sans MS"
    worksheet.oddHeader.center.size = 14
    worksheet.oddHeader.center.color = "FF69B4"
    worksheet.oddHeader.right.text = "✨"
    worksheet.oddFooter.center.text = "💖 pastel sprinkles 🎀"
    worksheet.oddFooter.center.font = "Comic Sans MS"
    worksheet.oddFooter.center.size = 10
    worksheet.oddFooter.center.color = "CDB4DB"

    if _is_sparse(worksheet):
        _decorate_sparse_sheet(worksheet, index)
        return

    width = _visible_width(worksheet)
    blocked = _merged_non_origin(worksheet)
    for row_idx, row in enumerate(
        worksheet.iter_rows(min_row=1, max_row=worksheet.max_row, max_col=width),
        1,
    ):
        header = _is_header_row(worksheet, row_idx, width)
        fill = HEADER_FILL if header else _row_fill(row_idx)
        for col_idx, cell in enumerate(row, 1):
            if (row_idx, col_idx) in blocked:
                continue
            if cell.value in (None, "") and not _has_fill(cell) and not header:
                continue
            cell.fill = fill
            cell.font = _cute_font(cell, header=header)
            cell.border = CUTE_BORDER
    if worksheet.row_dimensions[1].height is None or worksheet.row_dimensions[1].height < 22:
        worksheet.row_dimensions[1].height = 22
    _add_sprinkle_column(worksheet, width, index)
    _recolor_charts(worksheet)


def _merged_non_origin(worksheet: Worksheet) -> set[tuple[int, int]]:
    blocked: set[tuple[int, int]] = set()
    for merged in worksheet.merged_cells.ranges:
        for row in range(merged.min_row, merged.max_row + 1):
            for col in range(merged.min_col, merged.max_col + 1):
                if (row, col) != (merged.min_row, merged.min_col):
                    blocked.add((row, col))
    return blocked


def _decorate_sparse_sheet(worksheet: Worksheet, index: int) -> None:
    blocked = _merged_non_origin(worksheet)
    for row in range(1, 35):
        for col in range(1, 17):
            if (row, col) in blocked:
                continue
            cell = worksheet.cell(row, col)
            if cell.value not in (None, ""):
                size = cell.font.size or 28
                cell.font = Font(name="Comic Sans MS", size=size, bold=True, color="FFD63384")
                cell.fill = TITLE_FILL
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                continue
            cell.fill = WASH_FILL
    for offset, (row, col) in enumerate(SCATTER):
        if (row, col) in blocked or worksheet.cell(row, col).value not in (None, ""):
            continue
        cell = worksheet.cell(row, col, SPRINKLES[(offset + index) % len(SPRINKLES)])
        cell.font = Font(name="Comic Sans MS", size=16)
        cell.fill = _confetti_fill((offset + index) % len(CONFETTI))
        cell.alignment = Alignment(horizontal="center", vertical="center")
    _recolor_charts(worksheet)


def _add_sprinkle_column(worksheet: Worksheet, width: int, index: int) -> None:
    column = width + 1
    letter = get_column_letter(column)
    if worksheet.column_dimensions[letter].hidden:
        return
    worksheet.column_dimensions[letter].width = 4.5
    last_row = max(worksheet.max_row or 1, 1)
    for row in range(1, last_row + 1):
        if row != 1 and row % 3 != 0:
            continue
        glyph = "🌸" if row == 1 else SPRINKLES[(row + index) % len(SPRINKLES)]
        cell = worksheet.cell(row, column, glyph)
        cell.font = Font(name="Comic Sans MS", size=12 if row == 1 else 11)
        cell.fill = HEADER_FILL if row == 1 else _confetti_fill((row + index) % len(CONFETTI))
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = CUTE_BORDER


def _visible_width(worksheet: Worksheet) -> int:
    last = 1
    max_col = worksheet.max_column or 1
    scan_rows = range(1, min(worksheet.max_row or 1, 40) + 1)
    for col in range(1, max_col + 1):
        if worksheet.column_dimensions[get_column_letter(col)].hidden:
            continue
        for row in scan_rows:
            if worksheet.cell(row, col).value not in (None, ""):
                last = col
                break
    return last


def _is_sparse(worksheet: Worksheet) -> bool:
    """Cover sheets are a title plus empty space. Tables have a grid of values."""
    rows: set[int] = set()
    cols: set[int] = set()
    valued = 0
    max_row = min(worksheet.max_row or 1, 40)
    max_col = min(worksheet.max_column or 1, 20)
    for row in worksheet.iter_rows(min_row=1, max_row=max_row, max_col=max_col):
        for cell in row:
            if cell.value in (None, ""):
                continue
            valued += 1
            rows.add(cell.row)
            cols.add(cell.column)
    if len(rows) >= 2 and len(cols) >= 2:
        return False
    return valued < 8


def _is_header_row(worksheet: Worksheet, row_idx: int, width: int) -> bool:
    if row_idx == 1:
        return True
    valued = 0
    bold = 0
    for col in range(1, width + 1):
        cell = worksheet.cell(row_idx, col)
        if cell.value in (None, ""):
            continue
        valued += 1
        font = cell.font
        if font is not None and font.bold and (font.size or 11) >= 11:
            bold += 1
    return valued >= 2 and bold == valued


def _has_fill(cell) -> bool:
    fill = cell.fill
    return fill is not None and fill.fill_type not in (None, "none")


def _row_fill(row_idx: int) -> PatternFill:
    color = ROW_COLORS[(row_idx - 2) % len(ROW_COLORS)]
    return PatternFill(start_color=color, end_color=color, fill_type="solid")


def _confetti_fill(index: int) -> PatternFill:
    color = CONFETTI[index % len(CONFETTI)]
    return PatternFill(start_color=color, end_color=color, fill_type="solid")


def _cute_font(cell, *, header: bool) -> Font:
    size = cell.font.size if cell.font is not None and cell.font.size else None
    if header:
        size = max(size or 12, 12)
    else:
        size = size or 11
    return Font(
        name="Comic Sans MS",
        size=size,
        bold=header or bool(cell.font is not None and cell.font.bold),
        italic=bool(cell.font is not None and cell.font.italic),
        color="FFFFFFFF" if header else "FF5C4B51",
    )


def _recolor_charts(worksheet: Worksheet) -> None:
    for chart_index, chart in enumerate(getattr(worksheet, "_charts", ())):
        for series_index, series in enumerate(getattr(chart, "series", ())):
            color = CHART_COLORS[(chart_index + series_index) % len(CHART_COLORS)]
            _paint_series(series, color)


def _paint_series(series, color: str) -> None:
    props = getattr(series, "graphicalProperties", None)
    if props is None:
        return
    line = getattr(props, "line", None)
    if line is not None and not getattr(line, "noFill", False):
        line.solidFill = color
    else:
        props.solidFill = color
    marker = getattr(series, "marker", None)
    if marker is not None and getattr(marker, "graphicalProperties", None) is not None:
        marker.graphicalProperties.solidFill = color
        marker_line = getattr(marker.graphicalProperties, "line", None)
        if marker_line is not None:
            marker_line.solidFill = color
    for point in getattr(series, "data_points", None) or []:
        point_props = getattr(point, "graphicalProperties", None)
        if point_props is not None:
            point_props.solidFill = color
