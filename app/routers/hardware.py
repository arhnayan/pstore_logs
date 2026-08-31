"""Hardware and ports API."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import db

router = APIRouter(prefix="/api", tags=["hardware"])


@router.get("/hardware")
async def list_hardware(unhealthy_only: bool = False) -> dict:
    items = await db.list_hardware(unhealthy_only=unhealthy_only)
    return {"items": items, "count": len(items)}


@router.get("/ports")
async def list_ports(port_type: str | None = None) -> dict:
    items = await db.list_ports(port_type=port_type)
    return {"items": items, "count": len(items)}
