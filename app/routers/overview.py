"""Overview and status endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import collector, db

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/overview")
async def get_overview() -> dict:
    stats = await db.overview_stats()
    status = await db.get_status()
    cluster = await db.get_cluster_info()
    appliances = await db.list_appliances()
    return {
        "stats": stats,
        "status": status,
        "cluster": cluster,
        "appliances": appliances,
    }


@router.get("/status")
async def get_status() -> dict:
    return await db.get_status()
