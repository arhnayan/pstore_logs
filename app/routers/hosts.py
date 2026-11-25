"""Host connectivity (initiator login sessions) API."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.client import PowerStoreAuthError, PowerStoreError, create_client
from app.config import settings
from app.credentials import get_credentials
from app.deps import collector, db
from app.host_provisioning import (
    MAX_HOSTS, annotate_volume_groups, apply_volume_group, classify, delete_hosts, parse_hosts, provision,
)
from app.locations import ensure_locations
from app.mailer import get_email_config

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


@router.get("/health")
async def host_health() -> dict:
    items = collector.host_health
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "items": items,
        "count": len(items),
        "counts": counts,
        "checked_at": collector.host_health_checked_at,
        "email_configured": (await get_email_config()).configured,
        "alert_error": collector.host_alert_error,
    }


class BulkHostsPayload(BaseModel):
    location: str
    server: str
    hosts: str
    dry_run: bool = True
    # Write-capable account, used only when dry_run is false. Never stored.
    username: str | None = None
    password: str | None = None


async def _resolve_target(location_name: str, server: str) -> str:
    location = next((loc for loc in await ensure_locations(db) if loc["name"] == location_name), None)
    if location is None:
        raise HTTPException(status_code=400, detail=f"Unknown location: {location_name}")
    if server not in (location.get("servers") or []):
        raise HTTPException(status_code=400, detail=f"{server} is not a machine in {location_name}")
    target_ip = str((location.get("server_ips") or {}).get(server) or "").strip()
    if not target_ip:
        raise HTTPException(status_code=400, detail=f"Machine {server} has no management IP configured")
    return target_ip


async def _credentials(dry_run: bool, username: str | None, password: str | None, what: str) -> tuple[str, str]:
    if dry_run:
        # Read-only preview: the stored monitoring account is enough.
        creds = await get_credentials()
        if settings.mock:
            return creds if creds else ("mock", "mock")
        if creds:
            return creds
        raise HTTPException(status_code=400, detail="Configure credentials in Settings first")
    # Writing needs an account with write permission, supplied per request.
    user, pwd = (username or "").strip(), password or ""
    if settings.mock:
        return user or "mock", pwd or "mock"
    if not user or not pwd:
        raise HTTPException(status_code=401, detail=f"An account with write permission is required to {what}")
    return user, pwd


@router.post("/bulk")
async def bulk_create_hosts(payload: BulkHostsPayload) -> dict:
    """Create ESXi/NVMe hosts from a comma/newline separated list of FQDNs.

    ``dry_run`` (default) only parses the list and flags what already exists on the
    chosen machine; nothing is written until the caller sends ``dry_run=false``.
    """
    rows = parse_hosts(payload.hosts)
    if not rows:
        raise HTTPException(status_code=400, detail="Enter at least one host")
    if len(rows) > MAX_HOSTS:
        raise HTTPException(status_code=400, detail=f"Too many hosts ({len(rows)}); the limit is {MAX_HOSTS}")
    target_ip = await _resolve_target(payload.location, payload.server)

    username, password = await _credentials(payload.dry_run, payload.username, payload.password, "create hosts")

    client = create_client(cluster_ip=target_ip, username=username, password=password)
    await client.open()
    try:
        await client.login(username, password)
        await classify(client, rows)
        volume_groups: list[dict] = []
        if payload.dry_run:
            volume_groups = await annotate_volume_groups(client, rows)
        else:
            await provision(client, rows)
    except PowerStoreAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (PowerStoreError, httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Cannot reach {payload.server} ({target_ip}): {exc}") from exc
    finally:
        await client.close()

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "dry_run": payload.dry_run, "server": payload.server, "mgmt_ip": target_ip,
        "items": rows, "counts": counts, "volume_groups": volume_groups,
    }


class VolumeGroupPayload(BaseModel):
    location: str
    server: str
    hosts: list[str]
    volume_group_id: str
    action: str  # "map" or "unmap"
    username: str | None = None
    password: str | None = None


@router.post("/volume-group")
async def map_hosts_to_volume_group(payload: VolumeGroupPayload) -> dict:
    """Map or unmap a volume group for many existing hosts (a few calls in parallel)."""
    if payload.action not in ("map", "unmap"):
        raise HTTPException(status_code=400, detail="action must be 'map' or 'unmap'")
    rows = parse_hosts("\n".join(payload.hosts))
    if not rows:
        raise HTTPException(status_code=400, detail="Select at least one host")
    if len(rows) > MAX_HOSTS:
        raise HTTPException(status_code=400, detail=f"Too many hosts ({len(rows)}); the limit is {MAX_HOSTS}")
    target_ip = await _resolve_target(payload.location, payload.server)
    username, password = await _credentials(False, payload.username, payload.password, "map volume groups")

    client = create_client(cluster_ip=target_ip, username=username, password=password)
    await client.open()
    try:
        await client.login(username, password)
        await classify(client, rows)
        groups = await annotate_volume_groups(client, rows)
        if not any(g["id"] == payload.volume_group_id for g in groups):
            raise HTTPException(status_code=400, detail="Unknown volume group on this machine")
        await apply_volume_group(client, rows, payload.volume_group_id, payload.action)
    except PowerStoreAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (PowerStoreError, httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Cannot reach {payload.server} ({target_ip}): {exc}") from exc
    finally:
        await client.close()

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"action": payload.action, "server": payload.server, "items": rows, "counts": counts}


class DeleteHostsPayload(BaseModel):
    location: str
    server: str
    hosts: list[str]
    username: str | None = None
    password: str | None = None


@router.post("/delete")
async def delete_hosts_endpoint(payload: DeleteHostsPayload) -> dict:
    """Delete many existing hosts. Hosts with mapped volumes or in a host group are skipped."""
    rows = parse_hosts("\n".join(payload.hosts))
    if not rows:
        raise HTTPException(status_code=400, detail="Select at least one host")
    if len(rows) > MAX_HOSTS:
        raise HTTPException(status_code=400, detail=f"Too many hosts ({len(rows)}); the limit is {MAX_HOSTS}")
    target_ip = await _resolve_target(payload.location, payload.server)
    username, password = await _credentials(False, payload.username, payload.password, "delete hosts")

    client = create_client(cluster_ip=target_ip, username=username, password=password)
    await client.open()
    try:
        await client.login(username, password)
        await classify(client, rows)
        await delete_hosts(client, rows)
    except PowerStoreAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (PowerStoreError, httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Cannot reach {payload.server} ({target_ip}): {exc}") from exc
    finally:
        await client.close()

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"server": payload.server, "items": rows, "counts": counts}
