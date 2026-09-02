"""Vodafone report location registry and seed data."""

from __future__ import annotations

from typing import Any

from app.db import Database

# Per-server MGMT IPs from VODAFONE NETWORK IP.xlsx (not VLAN columns).
DEFAULT_LOCATIONS: list[dict[str, Any]] = [
    {
        "name": "Adana",
        "cluster_ip": "10.197.131.201",
        "servers": [
            "ADNTOCDELLCLD01", "ADNTOCDELLAPP01", "ADNTOCDELLAPP02",
            "ADNTOCDELLAPP03", "ADNTOCDELLAPP04", "ADNTOCDELLOSS01",
        ],
        "server_ips": {
            "ADNTOCDELLCLD01": "10.197.131.201",
            "ADNTOCDELLAPP01": "10.197.131.205",
            "ADNTOCDELLAPP02": "10.197.131.213",
            "ADNTOCDELLAPP03": "10.197.131.217",
            "ADNTOCDELLAPP04": "10.197.131.221",
            "ADNTOCDELLOSS01": "10.197.131.209",
        },
        "enabled": True,
        "sort_order": 0,
    },
    {
        "name": "Diyarbakır",
        "cluster_ip": "10.197.202.70",
        "servers": ["DYBTOCDELLAPP01"],
        "server_ips": {
            "DYBTOCDELLAPP01": "10.197.202.70",
        },
        "enabled": True,
        "sort_order": 1,
    },
    {
        "name": "Esenyurt",
        "cluster_ip": "10.197.139.205",
        "servers": [
            "ESNTOCDELLCLD01", "ESNTOCDELLAPP01", "ESNTOCDELLAPP02",
            "ESNTOCDELLAPP03", "ESNTOCDELLAPP04", "ESNTOCDELLOSS01",
        ],
        "server_ips": {
            "ESNTOCDELLCLD01": "10.197.139.205",
            "ESNTOCDELLAPP01": "10.197.139.201",
            "ESNTOCDELLAPP02": "10.197.139.213",
            "ESNTOCDELLAPP03": "10.197.139.217",
            "ESNTOCDELLAPP04": "10.197.139.221",
            "ESNTOCDELLOSS01": "10.197.139.209",
        },
        "enabled": True,
        "sort_order": 2,
    },
    {
        "name": "Gaziemir",
        "cluster_ip": "10.197.147.205",
        "servers": [
            "IZMTOCDELLCLD01", "IZMTOCDELLAPP01", "IZMTOCDELLAPP02",
            "IZMTOCDELLAPP03", "IZMTOCDELLAPP04", "IZMTOCDELLOSS01",
        ],
        "server_ips": {
            "IZMTOCDELLCLD01": "10.197.147.205",
            "IZMTOCDELLAPP01": "10.197.147.201",
            "IZMTOCDELLAPP02": "10.197.147.213",
            "IZMTOCDELLAPP03": "10.197.147.217",
            "IZMTOCDELLAPP04": "10.197.147.221",
            "IZMTOCDELLOSS01": "10.197.147.209",
        },
        "enabled": True,
        "sort_order": 3,
    },
    {
        "name": "Pursaklar",
        "cluster_ip": "10.197.143.201",
        "servers": [
            "ANKTOCDELLCLD01", "ANKTOCDELLAPP01", "ANKTOCDELLAPP02",
            "ANKTOCDELLAPP03", "ANKTOCDELLAPP04", "ANKTOCDELLOSS01",
        ],
        "server_ips": {
            "ANKTOCDELLCLD01": "10.197.143.201",
            "ANKTOCDELLAPP01": "10.197.143.205",
            "ANKTOCDELLAPP02": "10.197.143.213",
            "ANKTOCDELLAPP03": "10.197.143.217",
            "ANKTOCDELLAPP04": "10.197.143.221",
            "ANKTOCDELLOSS01": "10.197.143.209",
        },
        "enabled": True,
        "sort_order": 4,
    },
    {
        "name": "Tuzla",
        "cluster_ip": "10.197.135.205",
        "servers": [
            "TZLTOCDELLCLD01", "TZLTOCDELLAPP01", "TZLTOCDELLAPP02",
            "TZLTOCDELLAPP03", "TZLTOCDELLAPP04", "TZLTOCDELLOSS01",
        ],
        "server_ips": {
            "TZLTOCDELLCLD01": "10.197.135.205",
            "TZLTOCDELLAPP01": "10.197.135.201",
            "TZLTOCDELLAPP02": "10.197.135.213",
            "TZLTOCDELLAPP03": "10.197.135.217",
            "TZLTOCDELLAPP04": "10.197.135.221",
            "TZLTOCDELLOSS01": "10.197.135.209",
        },
        "enabled": True,
        "sort_order": 5,
    },
]

DEFAULTS_BY_NAME = {loc["name"]: loc for loc in DEFAULT_LOCATIONS}


def location_servers(locations: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {loc["name"]: list(loc["servers"]) for loc in locations if loc.get("enabled", True)}


def location_has_ips(location: dict[str, Any]) -> bool:
    ips = location.get("server_ips") or {}
    if any(str(ip).strip() for ip in ips.values()):
        return True
    return bool((location.get("cluster_ip") or "").strip())


async def ensure_locations(db: Database) -> list[dict[str, Any]]:
    if await db.count_report_locations() == 0:
        await db.upsert_report_locations(DEFAULT_LOCATIONS)
    else:
        existing = {loc["name"]: loc for loc in await db.list_report_locations()}
        updates: list[dict[str, Any]] = []
        for name, default in DEFAULTS_BY_NAME.items():
            current = existing.get(name)
            if current is None:
                updates.append(default)
                continue
            if not (current.get("server_ips") or {}):
                updates.append({**default, **current, "servers": default["servers"], "server_ips": default["server_ips"]})
            elif not (current.get("cluster_ip") or "").strip() and default.get("cluster_ip"):
                updates.append({**current, "cluster_ip": default["cluster_ip"]})
        if updates:
            await db.upsert_report_locations(updates)
    return await db.list_report_locations()
