"""Host connectivity (initiator login sessions) API."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.client import PowerStoreAuthError, PowerStoreError, create_client
from app.config import settings
from app.credentials import get_credentials
from app.deps import collector, db
from app.host_provisioning import MAX_HOSTS, classify, parse_hosts, provision
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

    location = next((loc for loc in await ensure_locations(db) if loc["name"] == payload.location), None)
    if location is None:
        raise HTTPException(status_code=400, detail=f"Unknown location: {payload.location}")
    if payload.server not in (location.get("servers") or []):
        raise HTTPException(status_code=400, detail=f"{payload.server} is not a machine in {payload.location}")
    target_ip = str((location.get("server_ips") or {}).get(payload.server) or "").strip()
    if not target_ip:
        raise HTTPException(status_code=400, detail=f"Machine {payload.server} has no management IP configured")

    creds = await get_credentials()
    if settings.mock:
        username, password = creds if creds else ("mock", "mock")
    elif creds:
        username, password = creds
    else:
        raise HTTPException(status_code=400, detail="Configure credentials in Settings first")

    client = create_client(cluster_ip=target_ip, username=username, password=password)
    await client.open()
    try:
        await client.login(username, password)
        await classify(client, rows)
        if not payload.dry_run:
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
        "items": rows, "counts": counts,
    }
