"""Cluster and appliance metadata API."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import db

router = APIRouter(prefix="/api/cluster", tags=["cluster"])


@router.get("")
async def get_cluster() -> dict:
    cluster = await db.get_cluster_info()
    appliances = await db.list_appliances()
    return {"cluster": cluster, "appliances": appliances}
