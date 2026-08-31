"""Alerts API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.client import PowerStoreAuthError
from app.deps import collector, db

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(severity: str | None = None, state: str | None = None) -> dict:
    items = await db.list_alerts(severity=severity, state=state)
    return {"items": items, "count": len(items)}


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str) -> dict:
    client = collector.get_client()
    try:
        await client.acknowledge_alert(alert_id)
    except PowerStoreAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    async with db.session() as conn:
        await conn.execute(
            "UPDATE alerts SET is_acknowledged=1 WHERE id=?",
            (alert_id,),
        )
        await conn.commit()
    return {"ok": True, "alert_id": alert_id}
