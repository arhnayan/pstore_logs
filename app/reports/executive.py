"""Per-location executive summary sheet: statistics plus CPU, IOPS, and latency charts."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from openpyxl.chart import LineChart, Reference
from openpyxl.chart.marker import Marker
from openpyxl.chart.text import RichText
from openpyxl.drawing.text import (
    CharacterProperties,
    Paragraph,
    ParagraphProperties,
    RichTextProperties,
)
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.reports.generator import ReportGenerator

CPU_COLUMN = "CPU Utilization"
IOPS_COLUMN = "Total IOPS"
LATENCY_COLUMN = "Latency"
METRIC_COLUMNS = (CPU_COLUMN, IOPS_COLUMN, LATENCY_COLUMN)
SAMPLE_POINTS = 48
MAX_CALLOUTS = 6
DATA_COLUMN = 27  # column AA; hidden chart source
CHART_ROW_SPAN = 20
VISIBLE_COLUMNS = 9

LINE_COLORS = ["4F81BD", "C0504D", "9BBB59", "8064A2", "4BACC6", "F79646", "5B9BD5", "70AD47"]
MARKER_SHAPES = ["triangle", "diamond", "circle", "square", "star", "x", "plus", "dash"]

HEADER_FILL = PatternFill(start_color="C0C0C0", end_color="C0C0C0", fill_type="solid")
YELLOW_FILL = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
LIGHT_RED_FILL = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")
THIN_BORDER = Border(
    left=Side(style="thin", color="000000"),
    right=Side(style="thin", color="000000"),
    top=Side(style="thin", color="000000"),
    bottom=Side(style="thin", color="000000"),
)
TITLE_FONT = Font(name="Calibri", size=16, bold=True)
HEADING_FONT = Font(name="Calibri", size=13, bold=True)
SUBTITLE_FONT = Font(name="Calibri", size=11, italic=True, color="666666")
BODY_FONT = Font(name="Calibri", size=11)
HEADER_FONT = Font(name="Calibri", size=11, bold=True)
CAPTION_FONT = Font(name="Calibri", size=10, italic=True, color="666666")

TABLE_HEADERS = [
    "Device",
    "Metric",
    "Average",
    "Median",
    "Std dev",
    "Low",
    "Peak",
    "Peak at",
    "Unusual hours",
]
METRIC_LABELS = {
    CPU_COLUMN: "CPU",
    IOPS_COLUMN: "IOPS",
    LATENCY_COLUMN: "Latency (ms)",
}
SENTENCE_LABELS = {
    CPU_COLUMN: "CPU",
    IOPS_COLUMN: "IOPS",
    LATENCY_COLUMN: "Latency",
}
METRIC_FORMATS = {
    CPU_COLUMN: "0.0",
    IOPS_COLUMN: "#,##0",
    LATENCY_COLUMN: "0.00",
}
NOTHING_STANDS_OUT = (
    "Öne çıkan bir durum yok. CPU, IOPS ve latency her cihazda olağan seviyesinde."
)
_TR_MONTHS = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")


@dataclass
class _MetricStats:
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    low: float | None = None
    peak: float | None = None
    peak_at: pd.Timestamp | None = None
    count: int = 0


@dataclass
class _Device:
    name: str
    frame: pd.DataFrame
    metrics: dict[str, _MetricStats] = field(default_factory=dict)
    unusual: dict[str, int] = field(default_factory=dict)
    anomaly_times: dict[str, set[pd.Timestamp]] = field(default_factory=dict)

    @property
    def cpu_avg(self) -> float | None:
        return self.metrics.get(CPU_COLUMN, _MetricStats()).mean


def _time_frame(df: pd.DataFrame) -> pd.DataFrame | None:
    work = df.copy()
    if "DateTime" not in work.columns and "Timestamp" in work.columns:
        work = work.rename(columns={"Timestamp": "DateTime"})
    if "DateTime" not in work.columns:
        return None
    work["DateTime"] = pd.to_datetime(work["DateTime"], errors="coerce")
    work = work.dropna(subset=["DateTime"]).sort_values("DateTime")
    work = work.drop_duplicates(subset=["DateTime"], keep="last")
    for column in (CPU_COLUMN, IOPS_COLUMN, LATENCY_COLUMN):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    if work.empty:
        return None
    return work.reset_index(drop=True)


def _stats(frame: pd.DataFrame, column: str) -> _MetricStats:
    if column not in frame.columns:
        return _MetricStats()
    work = frame[["DateTime", column]].dropna()
    if work.empty:
        return _MetricStats()
    values = work[column]
    peak_row = work.loc[values.idxmax()]
    spread = float(values.std(ddof=0)) if len(values) else None
    if spread is None or pd.isna(spread) or abs(spread) < 1e-9:
        spread = 0.0
    return _MetricStats(
        mean=float(values.mean()),
        median=float(values.median()),
        std=spread,
        low=float(values.min()),
        peak=float(values.max()),
        peak_at=pd.Timestamp(peak_row["DateTime"]),
        count=int(len(values)),
    )


def _anomaly_times(frame: pd.DataFrame, column: str) -> set[pd.Timestamp]:
    if column not in frame.columns:
        return set()
    found = ReportGenerator.detect_anomalies(None, frame, column)
    times: set[pd.Timestamp] = set()
    for index in found:
        stamp = frame.at[index, "DateTime"]
        if pd.notna(stamp):
            times.add(pd.Timestamp(stamp))
    return times


def _summarize(name: str, df: pd.DataFrame) -> _Device | None:
    frame = _time_frame(df)
    if frame is None:
        return None
    device = _Device(name=name, frame=frame)
    for column in METRIC_COLUMNS:
        device.metrics[column] = _stats(frame, column)
        times = _anomaly_times(frame, column)
        device.anomaly_times[column] = times
        device.unusual[column] = len(times)
    return device


def _format_metric(column: str, value: float) -> str:
    if column == CPU_COLUMN:
        return f"%{value:.0f}"
    if column == IOPS_COLUMN:
        return f"{value:,.0f}".replace(",", ".")
    whole, frac = f"{value:.2f}".split(".")
    return f"{whole},{frac} ms"


def _unusual_phrase(device: _Device, column: str) -> str:
    unusual = device.unusual.get(column, 0)
    total = device.metrics.get(column, _MetricStats()).count
    share = (unusual / total * 100) if total else 0
    return f"{unusual} olağandışı saat, %{share:.0f}"


def _metric_clause(device: _Device, column: str) -> str | None:
    stats = device.metrics.get(column, _MetricStats())
    if stats.mean is None:
        return None
    label = SENTENCE_LABELS[column]
    average = _format_metric(column, stats.mean)
    if stats.peak is None:
        text = f"{label} ortalaması {average}"
    elif stats.peak_at is not None:
        when = stats.peak_at.strftime("%d/%m %H:%M")
        text = (
            f"{label} ortalaması {average}, {when} tarihinde "
            f"{_format_metric(column, stats.peak)} zirveye ulaşıyor"
        )
    else:
        text = f"{label} ortalaması {average} ve {_format_metric(column, stats.peak)} zirveye ulaşıyor"
    return f"{text} ({_unusual_phrase(device, column)})"


def _sentence(device: _Device) -> str | None:
    clauses = [clause for column in METRIC_COLUMNS if (clause := _metric_clause(device, column))]
    if not clauses:
        return None
    return f"{device.name}: " + ". ".join(clauses) + "."


def _callouts(devices: list[_Device]) -> list[str]:
    ranked = sorted(devices, key=lambda device: device.cpu_avg if device.cpu_avg is not None else -1, reverse=True)
    sentences = []
    for device in ranked[:MAX_CALLOUTS]:
        text = _sentence(device)
        if text:
            sentences.append(text)
    return sentences or [NOTHING_STANDS_OUT]


def _subtitle(devices: list[_Device]) -> str:
    count = len(devices)
    stamps = [stamp for device in devices for stamp in device.frame["DateTime"]]
    if not stamps:
        return f"{count} cihaz · saatlik"
    start = min(stamps)
    end = max(stamps)
    start_text = f"{start.day} {_TR_MONTHS[start.month - 1]} {start.year}"
    end_text = f"{end.day} {_TR_MONTHS[end.month - 1]} {end.year}"
    return f"{start_text} – {end_text} · {count} cihaz · saatlik"


def _sample_times(devices: list[_Device]) -> list[pd.Timestamp]:
    unique = sorted({pd.Timestamp(stamp) for device in devices for stamp in device.frame["DateTime"]})
    count = len(unique)
    if count <= SAMPLE_POINTS:
        return unique
    indexes: list[int] = []
    for step in range(SAMPLE_POINTS):
        indexes.append(round(step * (count - 1) / (SAMPLE_POINTS - 1)))
    chosen: list[int] = []
    for index in indexes:
        if not chosen or chosen[-1] != index:
            chosen.append(index)
    return [unique[index] for index in chosen]


def _cpu_fill(value: float | None) -> PatternFill | None:
    if value is None:
        return None
    if value > 80:
        return LIGHT_RED_FILL
    if value > 60:
        return YELLOW_FILL
    return None


def _write_number(cell, value: float | None, number_format: str) -> None:
    cell.font = BODY_FONT
    cell.border = THIN_BORDER
    cell.alignment = Alignment(horizontal="center", vertical="center")
    if value is None:
        return
    cell.value = value
    cell.number_format = number_format


def _merge_text(worksheet: Worksheet, row: int, value: str, font: Font, height: float | None = None) -> None:
    worksheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=VISIBLE_COLUMNS)
    cell = worksheet.cell(row=row, column=1, value=value)
    cell.font = font
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    if height is not None:
        worksheet.row_dimensions[row].height = height


def _style_category_axis(axis, point_count: int) -> None:
    axis.title = "Zaman"
    axis.tickLblSkip = 1 if point_count <= 12 else max(1, round(point_count / 12))
    axis.txPr = RichText(
        bodyPr=RichTextProperties(rot="-2700000", anchor="ctr"),
        p=[
            Paragraph(
                pPr=ParagraphProperties(defRPr=CharacterProperties(sz=900)),
                endParaRPr=CharacterProperties(sz=900),
            )
        ],
    )


def _chart_number(column: str, value: float) -> float:
    if column == CPU_COLUMN:
        return round(value, 1)
    if column == LATENCY_COLUMN:
        return round(value, 2)
    return round(value)


def _unusual_label(device: _Device, column: str) -> str:
    unusual = device.unusual.get(column, 0)
    total = device.metrics.get(column, _MetricStats()).count
    share = (unusual / total * 100) if total else 0
    return f"{unusual} ({share:.1f}%)"


def _lookup(device: _Device, column: str) -> dict[pd.Timestamp, float]:
    if column not in device.frame.columns:
        return {}
    found: dict[pd.Timestamp, float] = {}
    for stamp, value in zip(device.frame["DateTime"], device.frame[column], strict=False):
        if pd.notna(value):
            found[pd.Timestamp(stamp)] = float(value)
    return found


def _write_chart_block(
    worksheet: Worksheet,
    *,
    header_row: int,
    times: list[pd.Timestamp],
    devices: list[_Device],
    column: str,
) -> tuple[int, int, int]:
    """Write one hidden chart block. Returns (header row, last data row, last column)."""
    lookups = {device.name: _lookup(device, column) for device in devices}
    line_names = [name for name, values in lookups.items() if values]
    marker_names = []
    for device in devices:
        if device.name not in line_names:
            continue
        anomalies = device.anomaly_times.get(column, set())
        if any(stamp in anomalies for stamp in times):
            marker_names.append(device.name)

    headers = ["Time", *line_names, *[f"{name} olağandışı" for name in marker_names]]
    for offset, header in enumerate(headers):
        worksheet.cell(row=header_row, column=DATA_COLUMN + offset, value=header)

    for index, stamp in enumerate(times):
        row = header_row + 1 + index
        worksheet.cell(row=row, column=DATA_COLUMN, value=stamp.strftime("%d/%m %H:%M"))
        for offset, name in enumerate(line_names, start=1):
            value = lookups[name].get(stamp)
            if value is None:
                continue
            worksheet.cell(row=row, column=DATA_COLUMN + offset, value=_chart_number(column, value))
        for offset, name in enumerate(marker_names, start=1 + len(line_names)):
            device = next(item for item in devices if item.name == name)
            if stamp not in device.anomaly_times.get(column, set()):
                continue
            value = lookups[name].get(stamp)
            if value is None:
                continue
            worksheet.cell(row=row, column=DATA_COLUMN + offset, value=_chart_number(column, value))

    last_col = DATA_COLUMN + len(headers) - 1
    last_row = header_row + len(times)
    for col in range(DATA_COLUMN, last_col + 1):
        worksheet.column_dimensions[get_column_letter(col)].hidden = True
    return header_row, last_row, last_col


def _add_chart(
    worksheet: Worksheet,
    *,
    title: str,
    y_title: str,
    y_format: str,
    anchor: str,
    header_row: int,
    last_row: int,
    last_col: int,
    line_names: list[str],
    marker_names: list[str],
    colors: dict[str, str],
    shapes: dict[str, str],
    point_count: int,
) -> None:
    if point_count <= 0 or not line_names:
        return
    chart = LineChart()
    chart.title = title
    chart.y_axis.title = y_title
    chart.y_axis.scaling.min = 0
    chart.y_axis.numFmt = y_format
    chart.height = 8
    chart.width = 18
    chart.visible_cells_only = False
    chart.display_blanks = "gap"
    chart.legend.position = "r"
    _style_category_axis(chart.x_axis, point_count)

    data = Reference(
        worksheet,
        min_col=DATA_COLUMN + 1,
        min_row=header_row,
        max_row=last_row,
        max_col=last_col,
    )
    categories = Reference(worksheet, min_col=DATA_COLUMN, min_row=header_row + 1, max_row=last_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)

    for index, name in enumerate(line_names):
        if index >= len(chart.series):
            break
        series = chart.series[index]
        series.graphicalProperties.line.solidFill = colors[name]
        series.graphicalProperties.line.width = 25000
    for index, name in enumerate(marker_names):
        series_index = len(line_names) + index
        if series_index >= len(chart.series):
            break
        series = chart.series[series_index]
        series.graphicalProperties.line.noFill = True
        series.marker = Marker(symbol=shapes[name], size=9)
        series.marker.graphicalProperties.solidFill = colors[name]
        series.marker.graphicalProperties.line.solidFill = colors[name]

    worksheet.add_chart(chart, anchor)


def _line_and_marker_names(
    devices: list[_Device],
    times: list[pd.Timestamp],
    column: str,
) -> tuple[list[str], list[str]]:
    line_names = []
    marker_names = []
    for device in devices:
        values = _lookup(device, column)
        if not values:
            continue
        line_names.append(device.name)
        anomalies = device.anomaly_times.get(column, set())
        if any(stamp in anomalies for stamp in times):
            marker_names.append(device.name)
    return line_names, marker_names


def write_executive_sheet(
    workbook: Workbook,
    title: str,
    location: str,
    devices: list[tuple[str, pd.DataFrame]],
) -> Worksheet:
    """Add one readable executive sheet and return it."""
    summarized = []
    for name, frame in devices:
        device = _summarize(name, frame)
        if device is not None:
            summarized.append(device)
    summarized.sort(key=lambda device: device.cpu_avg if device.cpu_avg is not None else -1, reverse=True)

    worksheet = workbook.create_sheet(title=title)
    for column, width in zip("ABCDEFGHI", (24, 16, 12, 12, 12, 12, 12, 16, 18), strict=True):
        worksheet.column_dimensions[column].width = width

    _merge_text(worksheet, 1, f"{location} Yönetici Özeti", TITLE_FONT, 24)
    _merge_text(worksheet, 2, _subtitle(summarized), SUBTITLE_FONT, 18)
    _merge_text(worksheet, 4, "Öne çıkanlar", HEADING_FONT, 20)

    sentences = _callouts(summarized)
    row = 5
    for sentence in sentences:
        _merge_text(worksheet, row, sentence, BODY_FONT, 48 if len(sentence) > 90 else 18)
        row += 1

    row += 1
    header_row = row
    for column, header in enumerate(TABLE_HEADERS, start=1):
        cell = worksheet.cell(row=header_row, column=column, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = THIN_BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    worksheet.row_dimensions[header_row].height = 30

    data_row = header_row
    for device in summarized:
        for column in METRIC_COLUMNS:
            data_row += 1
            stats = device.metrics.get(column, _MetricStats())
            name_cell = worksheet.cell(row=data_row, column=1, value=device.name)
            name_cell.font = BODY_FONT
            name_cell.border = THIN_BORDER
            name_cell.alignment = Alignment(horizontal="left", vertical="center")
            metric_cell = worksheet.cell(row=data_row, column=2, value=METRIC_LABELS[column])
            metric_cell.font = BODY_FONT
            metric_cell.border = THIN_BORDER
            metric_cell.alignment = Alignment(horizontal="left", vertical="center")
            number_format = METRIC_FORMATS[column]
            _write_number(worksheet.cell(row=data_row, column=3), stats.mean, number_format)
            _write_number(worksheet.cell(row=data_row, column=4), stats.median, number_format)
            _write_number(worksheet.cell(row=data_row, column=5), stats.std, number_format)
            _write_number(worksheet.cell(row=data_row, column=6), stats.low, number_format)
            _write_number(worksheet.cell(row=data_row, column=7), stats.peak, number_format)
            peak_cell = worksheet.cell(row=data_row, column=8)
            peak_cell.font = BODY_FONT
            peak_cell.border = THIN_BORDER
            peak_cell.alignment = Alignment(horizontal="center", vertical="center")
            if stats.peak_at is not None:
                peak_cell.value = stats.peak_at.strftime("%d/%m %H:%M")
            unusual_cell = worksheet.cell(row=data_row, column=9, value=_unusual_label(device, column))
            unusual_cell.font = BODY_FONT
            unusual_cell.border = THIN_BORDER
            unusual_cell.alignment = Alignment(horizontal="center", vertical="center")
            if column == CPU_COLUMN:
                for index, value in ((3, stats.mean), (7, stats.peak)):
                    fill = _cpu_fill(value)
                    if fill is not None:
                        worksheet.cell(row=data_row, column=index).fill = fill

    table_end = data_row
    times = _sample_times(summarized) if summarized else []
    colors = {device.name: LINE_COLORS[index % len(LINE_COLORS)] for index, device in enumerate(summarized)}
    shapes = {device.name: MARKER_SHAPES[index % len(MARKER_SHAPES)] for index, device in enumerate(summarized)}

    cpu_anchor = table_end + 2
    iops_anchor = cpu_anchor + CHART_ROW_SPAN
    latency_anchor = iops_anchor + CHART_ROW_SPAN
    caption_row = latency_anchor + CHART_ROW_SPAN
    point_count = len(times)
    caption = (
        "İşaretler, cihazın olağan seviyesinden uzak saatleri gösterir. "
        "Yukarıdaki sayılar tüm saatleri kapsar. "
        f"Her grafik, dönemin tamamına yayılmış {point_count} nokta içerir."
    )
    _merge_text(worksheet, caption_row, caption, CAPTION_FONT, 32)

    charts = (
        (CPU_COLUMN, f"{location} — CPU", "CPU %", "0", cpu_anchor),
        (IOPS_COLUMN, f"{location} — IOPS", "IOPS", "#,##0", iops_anchor),
        (LATENCY_COLUMN, f"{location} — Latency", "Latency (ms)", "0.00", latency_anchor),
    )
    if times:
        hidden_header = 1
        for column, title_text, y_title, y_format, anchor_row in charts:
            hidden_header, last_row, last_col = _write_chart_block(
                worksheet,
                header_row=hidden_header,
                times=times,
                devices=summarized,
                column=column,
            )
            line_names, marker_names = _line_and_marker_names(summarized, times, column)
            _add_chart(
                worksheet,
                title=title_text,
                y_title=y_title,
                y_format=y_format,
                anchor=f"A{anchor_row}",
                header_row=hidden_header,
                last_row=last_row,
                last_col=last_col,
                line_names=line_names,
                marker_names=marker_names,
                colors=colors,
                shapes=shapes,
                point_count=point_count,
            )
            hidden_header = last_row + 2

    return worksheet
