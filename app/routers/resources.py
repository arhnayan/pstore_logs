"""Aggregated system resources dashboard (Netdata-style)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.deps import db

router = APIRouter(prefix="/api/resources", tags=["resources"])

_HEALTH_LABELS = {
    "Fan": "Fans",
    "Power_Supply": "Power Supplies",
    "Drive": "Drives",
    "Battery": "Batteries",
    "Node": "Nodes",
}


def _num(payload: dict | None, *keys: str) -> float | int | None:
    if not payload:
        return None
    for key in keys:
        if payload.get(key) is not None:
            return payload[key]
    return None


def _cpu_pct(payload: dict | None) -> float | None:
    v = _num(payload, "io_workload_cpu_utilization", "avg_io_workload_cpu_utilization")
    return float(v) if v is not None else None


def _cluster_cpu_fallback(
    appliance_perf: list[dict[str, Any]],
    node_perf: list[dict[str, Any]],
) -> float | None:
    """Cluster perf often omits io_workload_cpu_utilization; derive from appliances/nodes."""
    values: list[float] = []
    for metric in appliance_perf:
        cpu = _cpu_pct(metric.get("payload"))
        if cpu is not None:
            values.append(cpu)
    if not values:
        for metric in node_perf:
            cpu = _cpu_pct(metric.get("payload"))
            if cpu is not None:
                values.append(cpu)
    if not values:
        return None
    return sum(values) / len(values)


def _iops(payload: dict | None) -> float | None:
    v = _num(payload, "total_iops", "avg_total_iops")
    return float(v) if v is not None else None


def _space_pct(used: int | float | None, total: int | float | None) -> float | None:
    if used is None or not total:
        return None
    return float(used) / float(total) * 100.0


def _avg_field(
    samples: list[dict[str, Any]],
    field: str,
    *alt_keys: str,
    require_iops: bool = False,
) -> float | None:
    values: list[float] = []
    for sample in samples:
        payload = sample.get("payload") or {}
        if require_iops and not (_iops(payload) or 0):
            continue
        value = _num(payload, field, *alt_keys)
        if value is not None:
            values.append(float(value))
    if not values:
        return None
    return sum(values) / len(values)


def _group_by_entity(samples: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        entity_id = sample.get("entity_id") or ""
        grouped.setdefault(entity_id, []).append(sample)
    return grouped


@router.get("")
async def get_resources() -> dict[str, Any]:
    appliances = await db.list_appliances()
    appliance_by_id = {a["id"]: a for a in appliances}
    nodes = await db.list_nodes()
    ports = await db.list_ports()

    cluster_perf = await db.get_latest_metric(
        "performance_metrics_by_cluster", metric_type="performance"
    )
    recent_cluster_perf = await db.list_metrics_recent(
        "performance_metrics_by_cluster", metric_type="performance", minutes=5
    )
    recent_appliance_perf = await db.list_metrics_recent(
        "performance_metrics_by_appliance", metric_type="performance", minutes=5
    )
    recent_node_perf = await db.list_metrics_recent(
        "performance_metrics_by_node", metric_type="performance", minutes=5
    )
    recent_app_by_id = _group_by_entity(recent_appliance_perf)
    recent_node_by_id = _group_by_entity(recent_node_perf)
    cluster_space = await db.get_latest_metric("space_metrics_by_cluster", metric_type="space")
    appliance_perf = await db.get_latest_metrics_by_entity("performance_metrics_by_appliance")
    appliance_space = await db.get_latest_metrics_by_entity("space_metrics_by_appliance")
    node_perf = await db.get_latest_metrics_by_entity("performance_metrics_by_node")
    wear = await db.get_latest_metrics_by_entity("wear_metrics_by_drive_daily")
    copy_cluster = await db.get_latest_metric("copy_metrics_by_cluster", metric_type="copy")
    copy_appliances = await db.get_latest_metrics_by_entity("copy_metrics_by_appliance")
    eth_perf = await db.get_latest_metrics_by_entity("performance_metrics_by_fe_eth_port")
    hardware = await db.list_hardware()
    health = await db.hardware_health_summary()

    perf_by_app = {m["entity_id"]: m for m in appliance_perf}
    space_by_app = {m["entity_id"]: m for m in appliance_space}
    perf_by_node = {m["entity_id"]: m for m in node_perf}
    eth_by_port = {m["entity_id"]: m for m in eth_perf}
    port_by_id = {p["id"]: p for p in ports}

    appliance_rows = []
    for app in appliances:
        perf = perf_by_app.get(app["id"], {}).get("payload") or {}
        recent = recent_app_by_id.get(app["id"], [])
        space = space_by_app.get(app["id"], {}).get("payload") or {}
        appliance_rows.append({
            "id": app["id"],
            "name": app.get("name") or app["id"],
            "model": app.get("model"),
            "service_tag": app.get("service_tag"),
            "cpu_utilization": _avg_field(recent, "io_workload_cpu_utilization", "avg_io_workload_cpu_utilization") or _cpu_pct(perf),
            "total_iops": _avg_field(recent, "total_iops", "avg_total_iops") if recent else _iops(perf),
            "avg_latency": _avg_field(recent, "avg_latency", require_iops=True),
            "physical_used": space.get("physical_used"),
            "physical_total": space.get("physical_total"),
            "physical_pct": _space_pct(space.get("physical_used"), space.get("physical_total")),
            "logical_used": space.get("logical_used"),
            "logical_provisioned": space.get("logical_provisioned"),
            "efficiency_ratio": space.get("efficiency_ratio"),
            "data_reduction": space.get("data_reduction"),
            "snapshot_savings": space.get("snapshot_savings"),
            "thin_savings": space.get("thin_savings"),
        })

    node_rows = []
    # /node IDs (N1, N2) differ from /hardware Node UUIDs — match by appliance + slot.
    node_hw = {
        (h.get("appliance_id"), h.get("slot")): h
        for h in hardware
        if h.get("hw_type") == "Node"
    }
    for node in nodes:
        perf = perf_by_node.get(node["id"], {}).get("payload") or {}
        recent = recent_node_by_id.get(node["id"], [])
        hw = node_hw.get((node.get("appliance_id"), node.get("slot"))) or {}
        extra = hw.get("extra") or {}
        app = appliance_by_id.get(node.get("appliance_id") or "", {})
        node_rows.append({
            "id": node["id"],
            "slot": node.get("slot"),
            "appliance_name": app.get("name"),
            "cpu_utilization": _avg_field(recent, "io_workload_cpu_utilization", "avg_io_workload_cpu_utilization") or _cpu_pct(perf),
            "total_iops": _avg_field(recent, "total_iops", "avg_total_iops") if recent else _iops(perf),
            "avg_latency": _avg_field(recent, "avg_latency", require_iops=True),
            "current_logins": _num(perf, "current_logins", "avg_current_logins"),
            "cpu_model": extra.get("cpu_model"),
            "cpu_cores": extra.get("cpu_cores"),
            "physical_memory_gb": extra.get("physical_memory_size_gb"),
            "lifecycle_state": hw.get("lifecycle_state"),
        })

    drive_rows = []
    drive_hw = {h["id"]: h for h in hardware if h.get("hw_type") == "Drive"}
    for w in wear:
        hw = drive_hw.get(w["entity_id"], {})
        extra = hw.get("extra") or {}
        payload = w.get("payload") or {}
        drive_rows.append({
            "id": w["entity_id"],
            "name": hw.get("name") or w["entity_id"],
            "endurance_remaining": payload.get("percent_endurance_remaining"),
            "size": extra.get("size"),
            "lifecycle_state": hw.get("lifecycle_state"),
        })
    drive_rows.sort(key=lambda d: (d.get("endurance_remaining") is None, d.get("endurance_remaining") or 100))

    eth_rows = []
    for port_id, metric in eth_by_port.items():
        port = port_by_id.get(port_id, {})
        payload = metric.get("payload") or {}
        eth_rows.append({
            "id": port_id,
            "name": port.get("name") or port_id,
            "is_link_up": port.get("is_link_up"),
            "bytes_rx": _num(payload, "bytes_rx_ps", "avg_bytes_rx_ps"),
            "bytes_tx": _num(payload, "bytes_tx_ps", "avg_bytes_tx_ps"),
            "pkt_rx": _num(payload, "pkt_rx_ps", "avg_pkt_rx_ps"),
            "pkt_tx": _num(payload, "pkt_tx_ps", "avg_pkt_tx_ps"),
            "crc_errors": _num(payload, "pkt_rx_crc_error_ps", "avg_pkt_rx_crc_error_ps"),
            "tx_errors": _num(payload, "pkt_tx_error_ps", "avg_pkt_tx_error_ps"),
            "total_iops": _iops(payload),
        })
    eth_rows.sort(
        key=lambda r: (r.get("crc_errors") or 0) + (r.get("tx_errors") or 0),
        reverse=True,
    )

    cs = cluster_space.get("payload") if cluster_space else {}
    cp = cluster_perf.get("payload") if cluster_perf else {}
    cc = copy_cluster.get("payload") if copy_cluster else {}

    health_display = [
        {"type": hw_type, "label": _HEALTH_LABELS.get(hw_type, hw_type), **counts}
        for hw_type, counts in health.items()
    ]

    cluster_cpu = _cpu_pct(cp)
    if cluster_cpu is None:
        cluster_cpu = _cluster_cpu_fallback(appliance_perf, node_perf)
    if recent_cluster_perf:
        cluster_cpu = _avg_field(
            recent_cluster_perf,
            "io_workload_cpu_utilization",
            "avg_io_workload_cpu_utilization",
        ) or cluster_cpu
    cluster_iops = (
        _avg_field(recent_cluster_perf, "total_iops", "avg_total_iops")
        if recent_cluster_perf
        else None
    ) or _iops(cp)
    cluster_latency = (
        _avg_field(recent_cluster_perf, "avg_latency", require_iops=True)
        if recent_cluster_perf
        else None
    )

    return {
        "cluster": {
            "cpu_utilization": cluster_cpu,
            "total_iops": cluster_iops,
            "avg_latency": cluster_latency,
            "physical_used": cs.get("physical_used"),
            "physical_total": cs.get("physical_total"),
            "physical_pct": _space_pct(cs.get("physical_used"), cs.get("physical_total")),
            "logical_used": cs.get("logical_used"),
            "logical_provisioned": cs.get("logical_provisioned"),
            "efficiency_ratio": cs.get("efficiency_ratio"),
            "data_reduction": cs.get("data_reduction"),
            "snapshot_savings": cs.get("snapshot_savings"),
            "thin_savings": cs.get("thin_savings"),
        },
        "appliances": appliance_rows,
        "nodes": node_rows,
        "hardware_health": health_display,
        "drives": drive_rows,
        "eth_ports": eth_rows[:20],
        "copy": {
            "cluster": {
                "data_remaining": cc.get("data_remaining"),
                "data_transferred": cc.get("data_transferred"),
                "transfer_rate": cc.get("transfer_rate"),
                "session_type": cc.get("session_type"),
            } if cc else None,
            "appliances": [
                {
                    "appliance_id": m["entity_id"],
                    "name": appliance_by_id.get(m["entity_id"], {}).get("name"),
                    **(m.get("payload") or {}),
                }
                for m in copy_appliances
            ],
        },
    }
