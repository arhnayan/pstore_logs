"""Fetch performance and capacity data from multiple PowerStore arrays."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from app.client import PowerStoreClient, PowerStoreAuthError
from app.config import settings
from app.db import Database, utc_now
from app.locations import location_has_ips, location_servers
from app.monitor_target import location_management_ip
from app.paths import report_capacity_dir, report_csv_dir
from app.reports.generator import ReportGenerator

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, dict[str, Any]], None]
TB = 1024**4

_LOCATION_CSV_ALIASES: dict[str, list[str]] = {
    "Diyarbakır": ["Diyarbakir", "Diyarbakır"],
}


def _us_to_ms(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


def _num(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _format_timestamp(value: Any) -> str:
    if not value:
        return ""
    text = str(value).replace("T", " ").replace("Z", "")
    if "+" in text:
        text = text.split("+", 1)[0]
    if "." in text:
        text = text.split(".", 1)[0]
    return text.strip()


def _host_aliases(name: str) -> list[str]:
    upper = name.upper()
    aliases = [upper]
    if upper.startswith("IZM"):
        aliases.append("GZM" + upper[3:])
    elif upper.startswith("GZM"):
        aliases.append("IZM" + upper[3:])
    return aliases


def match_host(hosts: list[dict[str, Any]], expected_name: str) -> dict[str, Any] | None:
    aliases = _host_aliases(expected_name)
    for host in hosts:
        host_name = (host.get("name") or "").upper()
        if host_name in aliases:
            return host
    expected = expected_name.upper()
    for host in hosts:
        host_name = (host.get("name") or "").upper()
        if expected in host_name or host_name in expected:
            return host
    return None


def match_appliance(appliances: list[dict[str, Any]], expected_name: str) -> dict[str, Any] | None:
    aliases = _host_aliases(expected_name)
    for appliance in appliances:
        for field in ("name", "service_tag"):
            value = (appliance.get(field) or "").upper()
            if value in aliases or any(alias in value or value in alias for alias in aliases):
                return appliance
    return None


def samples_to_dataframe(samples: list[dict[str, Any]]) -> pd.DataFrame | None:
    if not samples:
        return None
    rows = []
    for sample in samples:
        rows.append(
            {
                "Timestamp": _format_timestamp(sample.get("timestamp")),
                "Latency": _us_to_ms(sample.get("avg_latency")),
                "Read Latency": _us_to_ms(sample.get("avg_read_latency")),
                "Write Latency": _us_to_ms(sample.get("avg_write_latency")),
                "Avg. Size": _num(sample.get("avg_io_size")),
                "Read Size": _num(sample.get("avg_read_size")),
                "Write Size": _num(sample.get("avg_write_size")),
                "Total IOPS": _num(sample.get("avg_total_iops"), sample.get("total_iops")),
                "Read IOPS": _num(sample.get("avg_read_iops"), sample.get("read_iops")),
                "Write IOPS": _num(sample.get("avg_write_iops"), sample.get("write_iops")),
                "CPU Utilization": None,
            }
        )
    df = pd.DataFrame(rows)
    data_cols = [c for c in df.columns if c != "Timestamp"]
    if df.empty:
        return None
    df = df.dropna(how="all", subset=data_cols)
    if df.empty:
        return None
    mask = pd.Series([False] * len(df), index=df.index)
    for metric in ("Total IOPS", "Latency"):
        if metric in df.columns:
            mask = mask | ((df[metric].notna()) & (df[metric] > 0))
    df = df[mask]
    df = df[df["Timestamp"].astype(str).str.strip() != ""]
    return df.reset_index(drop=True) if not df.empty else None


def _location_csv_names(location_name: str) -> list[str]:
    names = [location_name]
    names.extend(_LOCATION_CSV_ALIASES.get(location_name, []))
    return list(dict.fromkeys(names))


def load_csv_performance(csv_dir: Path | None, location_name: str, server: str) -> pd.DataFrame | None:
    if csv_dir is None or not csv_dir.is_dir():
        return None
    reader = ReportGenerator()
    for loc_name in _location_csv_names(location_name):
        prefix = f"{loc_name}_{server}-"
        for path in sorted(csv_dir.glob("*.csv")):
            if path.name.startswith(prefix):
                return reader.read_performance_data(str(path))
    return None


async def _compute_host_capacity(
    client: PowerStoreClient,
    host_id: str,
    mappings: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
) -> dict[str, float]:
    volume_by_id = {v["id"]: v for v in volumes}
    mapped_ids = [m["volume_id"] for m in mappings if m.get("host_id") == host_id and m.get("volume_id")]
    total_bytes = 0.0
    used_bytes = 0.0
    for volume_id in mapped_ids:
        volume = volume_by_id.get(volume_id)
        if not volume:
            continue
        size = _num(volume.get("size")) or 0.0
        total_bytes += size
        try:
            samples = await client.generate_metrics("space_metrics_by_volume", volume_id, "One_Hour")
        except Exception:
            samples = []
        if samples:
            payload = samples[-1]
            used_bytes += _num(
                payload.get("logical_used"),
                payload.get("physical_used"),
                payload.get("subscribed_capacity"),
            ) or 0.0
        else:
            used_bytes += size
    if total_bytes <= 0:
        return {}
    free_bytes = max(total_bytes - used_bytes, 0.0)
    return {
        "Total_TB": total_bytes / TB,
        "Free_TB": free_bytes / TB,
        "Used_TB": used_bytes / TB,
    }


async def fetch_server_data(
    client: PowerStoreClient,
    server: str,
    *,
    hosts: list[dict[str, Any]],
    appliances: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
    interval: str = "One_Hour",
) -> tuple[pd.DataFrame | None, dict[str, float], str | None]:
    host = match_host(hosts, server)
    entity = "performance_metrics_by_host"
    entity_id: str | None = host["id"] if host else None

    if not entity_id:
        appliance = match_appliance(appliances, server)
        if appliance:
            entity = "performance_metrics_by_appliance"
            entity_id = appliance["id"]

    if not entity_id:
        return None, {}, f"Host/appliance {server} not found on cluster"

    try:
        samples = await client.generate_metrics(entity, entity_id, interval)
        df = samples_to_dataframe(samples)
        cap: dict[str, float] = {}
        if host:
            cap = await _compute_host_capacity(client, host["id"], mappings, volumes)
        if df is None and not cap:
            return None, {}, f"No metrics returned for {server}"
        return df, cap, None
    except PowerStoreAuthError as exc:
        return None, {}, str(exc)
    except Exception as exc:
        logger.exception("Failed fetching %s", server)
        return None, {}, str(exc)


async def fetch_location_data(
    location: dict[str, Any],
    username: str,
    password: str,
    *,
    csv_dir: Path | None = None,
    interval: str = "One_Hour",
    on_progress: ProgressFn | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, float]], str | None]:
    name = location["name"]
    servers = location.get("servers", [])
    cluster_ip = location_management_ip(location)
    if not servers:
        return {}, {}, "No servers configured"
    if not cluster_ip:
        return {}, {}, "No cluster management IP configured"

    server_data: dict[str, pd.DataFrame] = {}
    capacity_data: dict[str, dict[str, float]] = {}
    errors: list[str] = []
    api_errors: list[str] = []

    client = PowerStoreClient(cluster_ip=cluster_ip, username=username, password=password)
    await client.open()
    hosts: list[dict[str, Any]] = []
    appliances: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []
    try:
        await client.login(username, password)
        hosts = await client.get_hosts()
        appliances = await client.get_appliances()
        mappings = await client.get_host_volume_mappings()
        volumes = await client.get_volumes(primary_only=False)
    except PowerStoreAuthError as exc:
        api_errors.append(f"{cluster_ip}: {exc}")
    except Exception as exc:
        logger.exception("Failed connecting to %s (%s)", name, cluster_ip)
        api_errors.append(f"Cannot connect to {cluster_ip}: {exc.__class__.__name__}")
    else:
        for idx, server in enumerate(servers, start=1):
            if on_progress:
                on_progress(
                    name,
                    {
                        "phase": "host",
                        "current": idx,
                        "total": len(servers),
                        "server": server,
                        "mgmt_ip": cluster_ip,
                    },
                )
            df, cap, err = await fetch_server_data(
                client,
                server,
                hosts=hosts,
                appliances=appliances,
                mappings=mappings,
                volumes=volumes,
                interval=interval,
            )
            if err:
                api_errors.append(f"{server}: {err}")
            if df is not None:
                server_data[server] = df
            if cap:
                capacity_data[server] = cap
    finally:
        await client.close()

    for server in servers:
        if server in server_data:
            continue
        df = load_csv_performance(csv_dir, name, server)
        if df is not None and not df.empty:
            server_data[server] = df

    if api_errors:
        errors.extend(api_errors)

    if not server_data and errors:
        return server_data, capacity_data, "; ".join(errors)
    if errors and not api_errors:
        return server_data, capacity_data, "; ".join(errors)
    if api_errors and server_data:
        return server_data, capacity_data, f"Partial API failures: {'; '.join(api_errors[:3])}"
    if api_errors:
        return server_data, capacity_data, "; ".join(api_errors)
    return server_data, capacity_data, None


class ReportCollector:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def generate_combined_report(
        self,
        locations: list[dict[str, Any]],
        username: str,
        password: str,
        *,
        on_progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        enabled = [loc for loc in locations if loc.get("enabled", True) and location_has_ips(loc)]
        if not enabled:
            raise ValueError("No enabled locations with server MGMT IPs configured")

        csv_dir = report_csv_dir()
        capacity_dir = report_capacity_dir()
        all_server_data: dict[str, pd.DataFrame] = {}
        all_capacity: dict[str, dict[str, float]] = {}
        loc_map = location_servers(enabled)
        sem = asyncio.Semaphore(settings.report_fetch_concurrency)

        async def fetch_one(loc: dict[str, Any]) -> None:
            async with sem:
                if on_progress:
                    on_progress(loc["name"], {"phase": "location_start"})
                data, cap, err = await fetch_location_data(
                    loc,
                    username,
                    password,
                    csv_dir=csv_dir,
                    on_progress=on_progress,
                )
                all_server_data.update(data)
                all_capacity.update(cap)
                await self.db.update_report_location_status(
                    loc["name"],
                    status="partial" if err and data else ("error" if err else "ok"),
                    error=err,
                    fetched_at=utc_now() if data else None,
                )
                if on_progress:
                    on_progress(loc["name"], {"phase": "location_done", "error": err})

        await asyncio.gather(*(fetch_one(loc) for loc in enabled))

        if not all_server_data:
            hint = (
                "No performance data retrieved from any location. "
                "Common causes: metrics API denied (403 — need Administrator/Performance Monitor role), "
                "hosts not registered under expected names, or network cannot reach cluster IPs."
            )
            if csv_dir:
                hint += f" CSV fallback directory found at {csv_dir} but no matching files loaded."
            raise ValueError(hint)

        if on_progress:
            on_progress("", {"phase": "generating"})

        generator = ReportGenerator(
            output_dir=str(settings.reports_dir),
            location_servers=loc_map,
            server_data=all_server_data,
            raw_csv_dir=str(csv_dir) if csv_dir else None,
            formatted_csv_dir=str(capacity_dir) if capacity_dir else None,
            load_capacity_from_formatted_csv=capacity_dir is not None and not all_capacity,
            enable_analytics=True,
        )
        if all_capacity:
            generator.set_capacity_data(all_capacity)
        output_file = generator.generate_combined_report()
        return {
            "output_file": output_file,
            "filename": "All_Locations_Storage_Report.xlsx",
            "locations": len(enabled),
            "servers_with_data": len(all_server_data),
            "used_csv_fallback": csv_dir is not None,
        }
