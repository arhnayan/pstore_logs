"""Settings and credentials API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.client import PowerStoreAuthError, PowerStoreClient
from app.config import settings
from app.credentials import clear_credentials, get_credentials, has_credentials, set_credentials

router = APIRouter(prefix="/api/settings", tags=["settings"])


class CredentialsPayload(BaseModel):
    username: str
    password: str


@router.get("")
async def get_settings() -> dict:
    creds = get_credentials()
    return {
        "cluster_ip": settings.cluster_ip,
        "has_credentials": has_credentials(),
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


@router.post("/credentials")
async def save_credentials(payload: CredentialsPayload) -> dict:
    client = PowerStoreClient(
        cluster_ip=settings.cluster_ip,
        username=payload.username,
        password=payload.password,
    )
    await client.open()
    try:
        await client.login(payload.username, payload.password)
    except PowerStoreAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    finally:
        await client.close()

    set_credentials(payload.username, payload.password)
    return {"ok": True, "has_credentials": True}


@router.delete("/credentials")
async def delete_credentials() -> dict:
    clear_credentials()
    return {"ok": True, "has_credentials": False}
