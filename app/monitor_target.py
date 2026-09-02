"""Active live-monitoring target (Vodafone location + cluster MGMT IP)."""

from __future__ import annotations

import os
from typing import Any

from app.db import Database
from app.locations import DEFAULTS_BY_NAME, ensure_locations

MONITOR_LOCATION_KEY = "monitor_location_name"


def location_management_ip(location: dict[str, Any]) -> str:
    """Primary cluster MGMT IP for live monitoring (prefers *CLD01*)."""
    server_ips = dict(location.get("server_ips") or {})
    for server, ip in server_ips.items():
        if "CLD" in server.upper():
            value = str(ip).strip()
            if value:
                return value

    cluster_ip = str(location.get("cluster_ip") or "").strip()
    if cluster_ip:
        return cluster_ip

    for ip in server_ips.values():
        value = str(ip).strip()
        if value:
            return value
    return ""


def monitor_options(locations: list[dict[str, Any]]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for loc in locations:
        if not loc.get("enabled", True):
            continue
        ip = location_management_ip(loc)
        if not ip:
            continue
        options.append({"name": loc["name"], "cluster_ip": ip})
    return options


async def get_monitor_location_name(db: Database) -> str | None:
    value = await db.get_setting(MONITOR_LOCATION_KEY)
    return value.strip() if value else None


async def get_monitor_location(db: Database) -> dict[str, Any] | None:
    name = await get_monitor_location_name(db)
    if not name:
        return None
    for loc in await ensure_locations(db):
        if loc["name"] == name:
            return loc
    return None


async def get_active_cluster_ip(db: Database) -> str | None:
    env_ip = os.environ.get("PSTORE_CLUSTER_IP", "").strip()
    if env_ip:
        return env_ip

    location = await get_monitor_location(db)
    if location:
        ip = location_management_ip(location)
        return ip or None

    locations = await ensure_locations(db)
    for loc in locations:
        if loc.get("enabled", True):
            ip = location_management_ip(loc)
            if ip:
                return ip
    return None


async def set_monitor_location(db: Database, name: str) -> dict[str, str]:
    name = name.strip()
    if name not in DEFAULTS_BY_NAME:
        locations = {loc["name"] for loc in await ensure_locations(db)}
        if name not in locations:
            raise ValueError(f"Unknown location: {name}")

    await db.set_setting(MONITOR_LOCATION_KEY, name)
    location = await get_monitor_location(db)
    if not location:
        raise ValueError(f"Location not found: {name}")

    cluster_ip = location_management_ip(location)
    if not cluster_ip:
        raise ValueError(f"No management IP configured for {name}")

    return {"name": name, "cluster_ip": cluster_ip}


async def ensure_default_monitor_location(db: Database) -> str | None:
    if await get_monitor_location_name(db):
        return await get_monitor_location_name(db)

    for loc in await ensure_locations(db):
        if not loc.get("enabled", True):
            continue
        ip = location_management_ip(loc)
        if ip:
            await db.set_setting(MONITOR_LOCATION_KEY, loc["name"])
            return loc["name"]
    return None
