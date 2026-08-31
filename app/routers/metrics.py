"""Metrics and live I/O API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import collector, db

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("")
async def list_metrics(
    entity: str | None = None,
    entity_id: str | None = None,
    metric_type: str | None = None,
    limit: int = 500,
) -> dict:
    items = await db.list_metrics(
        entity=entity,
        entity_id=entity_id,
        metric_type=metric_type,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/series")
async def metrics_series(
    entity: str,
    entity_id: str | None = None,
    metric_type: str | None = None,
    limit: int | None = None,
) -> dict:
    items = await db.list_metrics_series(
        entity=entity,
        entity_id=entity_id,
        metric_type=metric_type,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/live")
async def live_metrics() -> dict:
    cluster = await db.get_latest_metric("performance_metrics_by_cluster", metric_type="performance")
    appliances = []
    nodes = []
    async with db.session() as conn:
        import json
        for entity in ("performance_metrics_by_appliance", "performance_metrics_by_node"):
            cursor = await conn.execute(
                """
                SELECT m.* FROM metrics_samples m
                INNER JOIN (
                    SELECT entity_id, MAX(collected_at) AS max_collected
                    FROM metrics_samples WHERE entity=?
                    GROUP BY entity_id
                ) latest ON m.entity_id=latest.entity_id AND m.collected_at=latest.max_collected
                WHERE m.entity=?
                """,
                (entity, entity),
            )
            rows = await cursor.fetchall()
            parsed = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                parsed.append(item)
            if entity.endswith("appliance"):
                appliances = parsed
            else:
                nodes = parsed

    top_volumes = await db.top_io_by_entity("performance_metrics_by_volume")
    top_hosts = await db.top_io_by_entity("performance_metrics_by_host")

    def cpu_util(payload: dict | None) -> float | None:
        if not payload:
            return None
        v = payload.get("io_workload_cpu_utilization")
        if v is None:
            v = payload.get("avg_io_workload_cpu_utilization")
        return float(v) if v is not None else None

    def avg_field(
        samples: list[dict],
        field: str,
        *alt_keys: str,
        require_iops: bool = False,
    ) -> float | None:
        values: list[float] = []
        for sample in samples:
            payload = sample.get("payload") or {}
            if require_iops:
                iops = payload.get("total_iops")
                if iops is None:
                    iops = payload.get("avg_total_iops")
                if not (iops or 0):
                    continue
            value = payload.get(field)
            if value is None and alt_keys:
                for key in alt_keys:
                    if payload.get(key) is not None:
                        value = payload[key]
                        break
            if value is not None:
                values.append(float(value))
        if not values:
            return None
        return sum(values) / len(values)

    recent_nodes = await db.list_metrics_recent(
        "performance_metrics_by_node", metric_type="performance", minutes=5
    )
    recent_by_node: dict[str, list[dict]] = {}
    for sample in recent_nodes:
        recent_by_node.setdefault(sample["entity_id"], []).append(sample)

    cluster_cpu = cpu_util(cluster.get("payload") if cluster else None)
    if cluster_cpu is None and recent_nodes:
        cluster_cpu = avg_field(
            recent_nodes,
            "io_workload_cpu_utilization",
            "avg_io_workload_cpu_utilization",
        )

    for node in nodes:
        recent = recent_by_node.get(node["entity_id"], [])
        node["recent_avg"] = {
            "total_iops": avg_field(recent, "total_iops", "avg_total_iops"),
            "io_workload_cpu_utilization": avg_field(
                recent,
                "io_workload_cpu_utilization",
                "avg_io_workload_cpu_utilization",
            ),
        }

    return {
        "cluster": cluster,
        "cluster_cpu_utilization": cluster_cpu,
        "appliances": appliances,
        "nodes": nodes,
        "top_volumes": top_volumes,
        "top_hosts": top_hosts,
    }


@router.post("/pin/{volume_id}")
async def pin_volume(volume_id: str) -> dict:
    client = collector.get_client()
    try:
        await client.enable_fast_metrics(volume_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await db.pin_volume(volume_id)
    return {"ok": True, "volume_id": volume_id}


@router.get("/ports")
async def port_metrics() -> dict:
    fc = await db.get_latest_metrics_by_entity("performance_metrics_by_fe_fc_port")
    eth = await db.get_latest_metrics_by_entity("performance_metrics_by_fe_eth_port")
    return {"fc_ports": fc, "eth_ports": eth, "count": len(fc) + len(eth)}
