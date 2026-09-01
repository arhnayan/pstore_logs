"""Fetch performance and capacity data from multiple PowerStore arrays."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import pandas as pd

from app.client import PowerStoreClient, PowerStoreAuthError
from app.config import settings
from app.db import Database, utc_now
from app.locations import location_has_ips, location_servers
from app.reports.generator import ReportGenerator

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, dict[str, Any]], None]
TB = 1024**4


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
    server: str,
    mgmt_ip: str,
    username: str,
    password: str,
    *,
    interval: str = "One_Hour",
) -> tuple[pd.DataFrame | None, dict[str, float], str | None]:
    mgmt_ip = mgmt_ip.strip()
    if not mgmt_ip:
        return None, {}, f"No MGMT IP for {server}"

    client = PowerStoreClient(cluster_ip=mgmt_ip, username=username, password=password)
    await client.open()
    try:
        await client.login(username, password)
        hosts = await client.get_hosts()
        host = match_host(hosts, server)
        if not host:
            return None, {}, f"Host {server} not found on {mgmt_ip}"

        samples = await client.generate_metrics(
            "performance_metrics_by_host",
            host["id"],
            interval,
        )
        df = samples_to_dataframe(samples)
        mappings = await client.get_host_volume_mappings()
        volumes = await client.get_volumes(primary_only=False)
        cap = await _compute_host_capacity(client, host["id"], mappings, volumes)
        return df, cap, None
    except PowerStoreAuthError as exc:
        return None, {}, str(exc)
    except Exception as exc:
        logger.exception("Failed fetching %s at %s", server, mgmt_ip)
        return None, {}, str(exc)
    finally:
        await client.close()


def _server_ip_map(location: dict[str, Any]) -> dict[str, str]:
    server_ips = dict(location.get("server_ips") or {})
    if server_ips:
        return server_ips
    cluster_ip = (location.get("cluster_ip") or "").strip()
    if cluster_ip:
        return {server: cluster_ip for server in location.get("servers", [])}
    return {}


async def fetch_location_data(
    location: dict[str, Any],
    username: str,
    password: str,
    *,
    interval: str = "One_Hour",
    on_progress: ProgressFn | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, float]], str | None]:
    name = location["name"]
    servers = location.get("servers", [])
    server_ips = _server_ip_map(location)
    if not servers or not any(server_ips.get(s, "").strip() for s in servers):
        return {}, {}, "No server MGMT IPs configured"

    server_data: dict[str, pd.DataFrame] = {}
    capacity_data: dict[str, dict[str, float]] = {}
    errors: list[str] = []

    for idx, server in enumerate(servers, start=1):
        mgmt_ip = server_ips.get(server, "").strip()
        if on_progress:
            on_progress(
                name,
                {
                    "phase": "host",
                    "current": idx,
                    "total": len(servers),
                    "server": server,
                    "mgmt_ip": mgmt_ip,
                },
            )
        if not mgmt_ip:
            errors.append(f"{server}: missing MGMT IP")
            continue

        df, cap, err = await fetch_server_data(
            server,
            mgmt_ip,
            username,
            password,
            interval=interval,
        )
        if err:
            errors.append(f"{server} ({mgmt_ip}): {err}")
            continue
        if df is not None:
            server_data[server] = df
        if cap:
            capacity_data[server] = cap

    if not server_data and errors:
        return server_data, capacity_data, "; ".join(errors)
    if errors:
        return server_data, capacity_data, "; ".join(errors)
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

        if on_progress:
            on_progress("", {"phase": "generating"})

        generator = ReportGenerator(
            output_dir=str(settings.reports_dir),
            location_servers=loc_map,
            server_data=all_server_data,
            enable_analytics=True,
        )
        generator.set_capacity_data(all_capacity)
        output_file = generator.generate_combined_report()
        return {
            "output_file": output_file,
            "filename": "All_Locations_Storage_Report.xlsx",
            "locations": len(enabled),
            "servers_with_data": len(all_server_data),
        }
