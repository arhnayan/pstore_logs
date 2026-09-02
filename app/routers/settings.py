"""Settings and credentials API."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.client import PowerStoreAuthError, PowerStoreClient
from app.config import settings
from app.credentials import clear_credentials, get_credentials, has_credentials, set_credentials
from app.deps import collector, db
from app.locations import ensure_locations
from app.monitor_target import (
    get_active_cluster_ip,
    get_monitor_location_name,
    location_management_ip,
    monitor_options,
    set_monitor_location,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_NETWORK_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.NetworkError,
    OSError,
)


class CredentialsPayload(BaseModel):
    username: str
    password: str


class ClusterLocationPayload(BaseModel):
    name: str


@router.get("")
async def get_settings() -> dict:
    creds = await get_credentials()
    locations = await ensure_locations(db)
    cluster_ip = await get_active_cluster_ip(db) or ""
    return {
        "cluster_ip": cluster_ip,
        "monitor_location": await get_monitor_location_name(db) or "",
        "locations": monitor_options(locations),
        "has_credentials": await has_credentials(),
        "username": creds[0] if creds else None,
        "poll_intervals": {
            "alerts": settings.poll_alerts_sec,
            "events": settings.poll_events_sec,
            "hardware": settings.poll_hardware_sec,
            "perf_fast": settings.poll_perf_fast_sec,
            "space": settings.poll_space_sec,
            "inventory": settings.poll_inventory_sec,
            "io_rank": settings.poll_io_rank_sec,
            "wear": settings.poll_wear_sec,
            "audit": settings.poll_audit_sec,
            "cluster_info": settings.poll_cluster_info_sec,
            "port_perf": settings.poll_port_perf_sec,
            "object_space": settings.poll_object_space_sec,
            "protection": settings.poll_protection_sec,
        },
    }


async def _validation_ips() -> list[str]:
    ips: list[str] = []
    seen: set[str] = set()

    def add(ip: str | None) -> None:
        value = (ip or "").strip()
        if value and value not in seen:
            seen.add(value)
            ips.append(value)

    add(await get_active_cluster_ip(db))
    for location in await ensure_locations(db):
        if not location.get("enabled", True):
            continue
        add(location_management_ip(location))

    return ips


async def _try_login(cluster_ip: str, username: str, password: str) -> None:
    client = PowerStoreClient(
        cluster_ip=cluster_ip,
        username=username,
        password=password,
    )
    await client.open()
    try:
        await client.login(username, password)
    finally:
        await client.close()


@router.put("/cluster-location")
async def update_cluster_location(payload: ClusterLocationPayload) -> dict:
    try:
        target = await set_monitor_location(db, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await collector.set_cluster_ip(target["cluster_ip"])
    return {"ok": True, **target}


@router.post("/credentials")
async def save_credentials(payload: CredentialsPayload) -> dict:
    username = payload.username.strip()
    password = payload.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    tested_ip: str | None = None
    connection_errors: list[str] = []

    for cluster_ip in await _validation_ips():
        try:
            await _try_login(cluster_ip, username, password)
            tested_ip = cluster_ip
            break
        except PowerStoreAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except _NETWORK_ERRORS as exc:
            connection_errors.append(f"{cluster_ip}: {exc.__class__.__name__}")

    await set_credentials(username, password)

    if tested_ip:
        return {
            "ok": True,
            "has_credentials": True,
            "validated": True,
            "tested_ip": tested_ip,
        }

    tried_ips = await _validation_ips()
    tried = ", ".join(tried_ips) or "none configured"
    warning = (
        f"Credentials saved, but login could not be verified. "
        f"None of the configured IPs responded ({tried}). "
        f"Pick a location in Settings and ensure you can reach its management IP."
    )
    return {
        "ok": True,
        "has_credentials": True,
        "validated": False,
        "warning": warning,
        "errors": connection_errors,
    }


@router.delete("/credentials")
async def delete_credentials() -> dict:
    await clear_credentials()
    return {"ok": True, "has_credentials": False}
