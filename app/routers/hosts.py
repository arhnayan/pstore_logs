"""Host connectivity (initiator login sessions) API."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import collector
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
