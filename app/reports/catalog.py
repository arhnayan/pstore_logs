"""Catalog of custom-report sections, metrics, and columns."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

SECTIONS: list[dict[str, Any]] = [
    {
        "id": "psk",
        "label": "PSK",
        "description": "Location-level aggregated I/O size and IOPS over time",
        "default": True,
    },
    {
        "id": "metric_sheets",
        "label": "Per-server metric sheets",
        "description": "One sheet per server and selected metric",
        "default": True,
    },
    {
        "id": "tmp_sheets",
        "label": "TMP sheets",
        "description": "Combined hourly table per server in each location workbook",
        "default": True,
    },
    {
        "id": "all_tmps",
        "label": "All_TMPs.xlsx",
        "description": "Combined hourly workbook across all locations and servers",
        "default": True,
    },
]

# Columns used by the legacy PSK sheet (subset of IOPS + IO Size + Latency).
PSK_COLUMNS = [
    "DateTime",
    "Latency",
    "Avg. Size",
    "Read Size",
    "Write Size",
    "Total IOPS",
    "Read IOPS",
    "Write IOPS",
]

METRICS: list[dict[str, Any]] = [
    {
        "id": "cpu",
        "label": "CPU",
        "sheet_suffix": "CPU",
        "default": True,
        "columns": [
            {"id": "DateTime", "label": "DateTime", "required": True},
            {"id": "Latency", "label": "Latency"},
            {"id": "CPU Utilization", "label": "CPU Utilization"},
            {"id": "Avg CPU Utilization", "label": "Avg CPU Utilization"},
            {"id": "Max CPU Utilization", "label": "Max CPU Utilization"},
        ],
    },
    {
        "id": "iops",
        "label": "IOPS",
        "sheet_suffix": "IOPS",
        "default": True,
        "columns": [
            {"id": "DateTime", "label": "DateTime", "required": True},
            {"id": "Latency", "label": "Latency"},
            {"id": "Total IOPS", "label": "Total IOPS"},
            {"id": "Read IOPS", "label": "Read IOPS"},
            {"id": "Write IOPS", "label": "Write IOPS"},
            {"id": "Max Total IOPS", "label": "Max Total IOPS"},
            {"id": "Max Read IOPS", "label": "Max Read IOPS"},
            {"id": "Max Write IOPS", "label": "Max Write IOPS"},
            {"id": "Normalized IOPS", "label": "Normalized IOPS"},
        ],
    },
    {
        "id": "iosize",
        "label": "IO Size",
        "sheet_suffix": "IOsize",
        "default": True,
        "columns": [
            {"id": "DateTime", "label": "DateTime", "required": True},
            {"id": "Latency", "label": "Latency"},
            {"id": "Avg. Size", "label": "Avg. Size"},
            {"id": "Read Size", "label": "Read Size"},
            {"id": "Write Size", "label": "Write Size"},
        ],
    },
    {
        "id": "latency",
        "label": "Latency",
        "sheet_suffix": "Latency",
        "default": True,
        "columns": [
            {"id": "DateTime", "label": "DateTime", "required": True},
            {"id": "Latency", "label": "Latency"},
            {"id": "Read Latency", "label": "Read Latency"},
            {"id": "Write Latency", "label": "Write Latency"},
            {"id": "Max Latency", "label": "Max Latency"},
            {"id": "Max Read Latency", "label": "Max Read Latency"},
            {"id": "Max Write Latency", "label": "Max Write Latency"},
        ],
    },
    {
        "id": "bandwidth",
        "label": "Bandwidth",
        "sheet_suffix": "BW",
        "default": False,
        "columns": [
            {"id": "DateTime", "label": "DateTime", "required": True},
            {"id": "Total Bandwidth", "label": "Total Bandwidth (MiB/s)"},
            {"id": "Read Bandwidth", "label": "Read Bandwidth (MiB/s)"},
            {"id": "Write Bandwidth", "label": "Write Bandwidth (MiB/s)"},
            {"id": "Max Total Bandwidth", "label": "Max Total Bandwidth (MiB/s)"},
            {"id": "Max Read Bandwidth", "label": "Max Read Bandwidth (MiB/s)"},
            {"id": "Max Write Bandwidth", "label": "Max Write Bandwidth (MiB/s)"},
        ],
    },
    {
        "id": "unaligned",
        "label": "Unaligned I/O",
        "sheet_suffix": "Unaligned",
        "default": False,
        "columns": [
            {"id": "DateTime", "label": "DateTime", "required": True},
            {"id": "Unaligned I/O", "label": "Unaligned I/O"},
            {"id": "Unaligned Read I/O", "label": "Unaligned Read I/O"},
            {"id": "Unaligned Write I/O", "label": "Unaligned Write I/O"},
        ],
    },
]

DATE_PRESETS: list[dict[str, Any]] = [
    {"id": "30d", "label": "Last 30 days", "days": 30},
    {"id": "14d", "label": "Last 2 weeks", "days": 14},
    {"id": "7d", "label": "Last 1 week", "days": 7},
    {"id": "custom", "label": "Specified range", "days": None},
]

# Preferred column order for combined TMP / All_TMPs sheets.
TMP_COLUMN_ORDER = [
    "DateTime",
    "Latency",
    "Read Latency",
    "Write Latency",
    "Max Latency",
    "Max Read Latency",
    "Max Write Latency",
    "Avg. Size",
    "Read Size",
    "Write Size",
    "Total IOPS",
    "Read IOPS",
    "Write IOPS",
    "Max Total IOPS",
    "Max Read IOPS",
    "Max Write IOPS",
    "Normalized IOPS",
    "CPU Utilization",
    "Avg CPU Utilization",
    "Max CPU Utilization",
    "Total Bandwidth",
    "Read Bandwidth",
    "Write Bandwidth",
    "Max Total Bandwidth",
    "Max Read Bandwidth",
    "Max Write Bandwidth",
    "Unaligned I/O",
    "Unaligned Read I/O",
    "Unaligned Write I/O",
]

IOPS_FORMAT_COLUMNS = {
    "Total IOPS",
    "Read IOPS",
    "Write IOPS",
    "Max Total IOPS",
    "Max Read IOPS",
    "Max Write IOPS",
    "Normalized IOPS",
}

NUMBER_FORMAT_COLUMNS = {
    "Latency",
    "Read Latency",
    "Write Latency",
    "Max Latency",
    "Max Read Latency",
    "Max Write Latency",
    "Avg. Size",
    "Read Size",
    "Write Size",
    "Total Bandwidth",
    "Read Bandwidth",
    "Write Bandwidth",
    "Max Total Bandwidth",
    "Max Read Bandwidth",
    "Max Write Bandwidth",
}

CPU_FORMAT_COLUMNS = {
    "CPU Utilization",
    "Avg CPU Utilization",
    "Max CPU Utilization",
}


def metric_by_id() -> dict[str, dict[str, Any]]:
    return {metric["id"]: metric for metric in METRICS}


def section_ids() -> set[str]:
    return {section["id"] for section in SECTIONS}


def default_metric_ids() -> list[str]:
    return [metric["id"] for metric in METRICS if metric.get("default")]


def default_section_ids() -> list[str]:
    return [section["id"] for section in SECTIONS if section.get("default")]


def columns_for_metric(metric_id: str) -> list[str]:
    metric = metric_by_id().get(metric_id)
    if not metric:
        return ["DateTime"]
    return [column["id"] for column in metric["columns"]]


def allowed_columns(metric_id: str) -> set[str]:
    return set(columns_for_metric(metric_id))


def resolve_metric_columns(metric_id: str, selected: list[str] | None) -> list[str]:
    allowed = columns_for_metric(metric_id)
    allowed_set = set(allowed)
    if not selected:
        return allowed
    resolved = [column for column in selected if column in allowed_set]
    if "DateTime" not in resolved:
        resolved = ["DateTime"] + resolved
    return resolved or ["DateTime"]


def resolve_psk_columns(selected: list[str] | None) -> list[str]:
    allowed = set(PSK_COLUMNS)
    if not selected:
        return list(PSK_COLUMNS)
    resolved = [column for column in PSK_COLUMNS if column in selected and column in allowed]
    if "DateTime" not in resolved:
        resolved = ["DateTime"] + resolved
    return resolved or ["DateTime"]


def combined_tmp_columns(metric_ids: list[str], column_map: dict[str, list[str]] | None) -> list[str]:
    """Build TMP column list from selected metrics (legacy order, extras appended)."""
    chosen: list[str] = ["DateTime"]
    seen = {"DateTime"}
    for metric_id in metric_ids:
        for column in resolve_metric_columns(
            metric_id,
            (column_map or {}).get(metric_id),
        ):
            if column not in seen:
                chosen.append(column)
                seen.add(column)
    ordered = [column for column in TMP_COLUMN_ORDER if column in seen]
    extras = [column for column in chosen if column not in ordered]
    return ordered + extras


def resolve_date_range(
    preset: str,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    """Return an inclusive [start, end] calendar-date range."""
    today = today or date.today()
    presets = {item["id"]: item for item in DATE_PRESETS}
    if preset not in presets:
        raise ValueError("Date range must be 30 days, 2 weeks, 1 week, or a specified range")

    if preset == "custom":
        if not start_date or not end_date:
            raise ValueError("Specified range requires both a start date and an end date")
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError("Dates must use YYYY-MM-DD") from exc
        if end < start:
            raise ValueError("End date must be on or after the start date")
        return start, end

    days = int(presets[preset]["days"])
    end = today
    start = today - timedelta(days=days - 1)
    return start, end


def catalog_payload() -> dict[str, Any]:
    return {
        "sections": SECTIONS,
        "metrics": METRICS,
        "date_presets": DATE_PRESETS,
        "psk_columns": [{"id": column, "label": column, "required": column == "DateTime"} for column in PSK_COLUMNS],
        "note": (
            "PowerStore hourly metrics cover roughly the last 30 days. "
            "Specified ranges are inclusive of both dates and clipped to available samples."
        ),
    }
