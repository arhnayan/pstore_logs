"""Audit events API."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import db

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
async def list_audit_events(limit: int = 500) -> dict:
    items = await db.list_audit_events(limit=limit)
    status = await db.get_status()
    return {
        "items": items,
        "count": len(items),
        "access": status.get("audit_access", "unknown"),
    }
