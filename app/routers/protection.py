"""Replication and protection inventory API."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import db

router = APIRouter(prefix="/api/protection", tags=["protection"])


@router.get("")
async def get_protection() -> dict:
    sessions = await db.list_replication_sessions()
    remote_systems = await db.list_remote_systems()
    policies = await db.list_protection_policies()
    snapshot_rules = await db.list_snapshot_rules()
    copy_cluster = await db.get_latest_metric("copy_metrics_by_cluster", metric_type="copy")
    copy_appliances = await db.get_latest_metrics_by_entity("copy_metrics_by_appliance")
    return {
        "replication_sessions": sessions,
        "remote_systems": remote_systems,
        "policies": policies,
        "snapshot_rules": snapshot_rules,
        "copy_metrics": {
            "cluster": copy_cluster,
            "appliances": copy_appliances,
        },
    }
