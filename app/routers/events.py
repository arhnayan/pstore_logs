"""Events API."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import db

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
async def list_events(severity: str | None = None) -> dict:
    items = await db.list_events(severity=severity)
    return {"items": items, "count": len(items)}
