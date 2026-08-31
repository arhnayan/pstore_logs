"""Capacity / space metrics API."""

from __future__ import annotations

import json

from fastapi import APIRouter

from app.deps import db

router = APIRouter(prefix="/api/capacity", tags=["capacity"])


@router.get("")
async def get_capacity() -> dict:
    cluster_space = await self_get_space("space_metrics_by_cluster")
    appliance_spaces = await self_get_all_appliance_space()
    volume_spaces = await db.get_latest_metrics_by_entity("space_metrics_by_volume")
    fs_spaces = await db.get_latest_metrics_by_entity("space_metrics_by_file_system")
    return {
        "cluster": cluster_space,
        "appliances": appliance_spaces,
        "volumes": volume_spaces,
        "file_systems": fs_spaces,
    }


async def self_get_space(entity: str) -> dict | None:
    return await db.get_latest_metric(entity, metric_type="space")


async def self_get_all_appliance_space() -> list[dict]:
    async with db.session() as conn:
        cursor = await conn.execute(
            """
            SELECT m.* FROM metrics_samples m
            INNER JOIN (
                SELECT entity_id, MAX(collected_at) AS max_collected
                FROM metrics_samples
                WHERE entity='space_metrics_by_appliance'
                GROUP BY entity_id
            ) latest ON m.entity_id=latest.entity_id AND m.collected_at=latest.max_collected
            WHERE m.entity='space_metrics_by_appliance'
            """
        )
        rows = await cursor.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        result.append(item)
    return result
