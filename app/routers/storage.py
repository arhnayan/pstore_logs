"""Storage / provisioning inventory API."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import db

router = APIRouter(prefix="/api/storage", tags=["storage"])


@router.get("")
async def get_storage() -> dict:
    volumes = await db.list_volumes(primary_only=True)
    hosts = await db.list_hosts()
    mappings = await db.list_host_volume_maps()
    nodes = await db.list_nodes()
    overview = await db.storage_overview()

    host_map_counts: dict[str, int] = {}
    for m in mappings:
        if m.get("host_id"):
            host_map_counts[m["host_id"]] = host_map_counts.get(m["host_id"], 0) + 1

    vol_map_counts: dict[str, int] = {}
    for m in mappings:
        if m.get("volume_id"):
            vol_map_counts[m["volume_id"]] = vol_map_counts.get(m["volume_id"], 0) + 1

    for vol in volumes:
        vol["mapped_hosts"] = vol_map_counts.get(vol["id"], 0)
    for host in hosts:
        host["mapped_volumes"] = host_map_counts.get(host["id"], 0)

    top_volumes = await db.top_io_by_entity("performance_metrics_by_volume")
    top_hosts = await db.top_io_by_entity("performance_metrics_by_host")
    volume_spaces = await db.get_latest_metrics_by_entity("space_metrics_by_volume")
    space_by_vol = {v["entity_id"]: v["payload"] for v in volume_spaces}

    for vol in volumes:
        vol["space"] = space_by_vol.get(vol["id"])

    return {
        "overview": overview,
        "volumes": volumes,
        "hosts": hosts,
        "mappings": mappings,
        "nodes": nodes,
        "top_volumes": top_volumes,
        "top_hosts": top_hosts,
    }
