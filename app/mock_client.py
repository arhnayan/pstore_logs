"""In-process PowerStore stand-in used when the app is started with --mock."""

from __future__ import annotations

import io
import math
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

from app.client import PowerStoreClient

TB = 1024**4
_HOUR_HISTORY = 30 * 24


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(text: str) -> int:
    total = 0
    for index, char in enumerate(text or "mock"):
        total += (index + 1) * ord(char)
    return total


class MockPowerStoreClient(PowerStoreClient):
    """Returns a small stable cluster so the UI and reports can run offline."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._series_cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self._collections: dict[str, dict[str, Any]] = {}

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def login(self, username: str | None = None, password: str | None = None) -> None:
        self.username = username or self.username or "mock"
        self.password = password or self.password or "mock"

    def _base(self) -> int:
        return 4000 + (_seed(self.cluster_ip or "mock") % 9000)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)

    def _perf_sample(self, moment: datetime, phase: float, scale: float = 1.0) -> dict[str, Any]:
        wave = 0.75 + 0.25 * math.sin(phase)
        read_iops = self._base() * 0.4 * wave * scale
        write_iops = self._base() * 0.6 * wave * scale
        total_iops = read_iops + write_iops
        read_size = 8 * 1024
        write_size = 32 * 1024
        avg_size = 16 * 1024
        return {
            "timestamp": _iso(moment),
            "avg_read_iops": read_iops,
            "avg_write_iops": write_iops,
            "avg_total_iops": total_iops,
            "read_iops": read_iops,
            "write_iops": write_iops,
            "total_iops": total_iops,
            "max_read_iops": read_iops * 1.35,
            "max_write_iops": write_iops * 1.35,
            "max_total_iops": total_iops * 1.4,
            "avg_normalized_iops": total_iops * 1.1,
            "normalized_iops": total_iops * 1.1,
            "avg_latency": 800 + 400 * wave,
            "avg_read_latency": 600 + 250 * wave,
            "avg_write_latency": 1100 + 500 * wave,
            "max_latency": 1800 + 700 * wave,
            "max_read_latency": 1400 + 400 * wave,
            "max_write_latency": 2200 + 800 * wave,
            "avg_io_size": avg_size,
            "avg_read_size": read_size,
            "avg_write_size": write_size,
            "avg_read_bandwidth": read_iops * read_size,
            "avg_write_bandwidth": write_iops * write_size,
            "avg_bandwidth": read_iops * read_size + write_iops * write_size,
            "read_bandwidth": read_iops * read_size,
            "write_bandwidth": write_iops * write_size,
            "bandwidth": read_iops * read_size + write_iops * write_size,
            "max_read_bandwidth": read_iops * read_size * 1.4,
            "max_write_bandwidth": write_iops * write_size * 1.4,
            "max_bandwidth": (read_iops * read_size + write_iops * write_size) * 1.4,
            "avg_io_workload_cpu_utilization": 0.18 + 0.22 * wave,
            "max_io_workload_cpu_utilization": 0.35 + 0.25 * wave,
            "avg_unaligned_io_count": 8 + 6 * wave,
            "avg_unaligned_read_count": 3 + 2 * wave,
            "avg_unaligned_write_count": 5 + 4 * wave,
        }

    def _space_sample(self, moment: datetime) -> dict[str, Any]:
        physical_total = 48 * TB
        physical_used = 31 * TB
        logical_provisioned = 86 * TB
        logical_used = 42 * TB
        return {
            "timestamp": _iso(moment),
            "physical_total": physical_total,
            "physical_used": physical_used,
            "physical_free": physical_total - physical_used,
            "last_physical_total": physical_total,
            "last_physical_used": physical_used,
            "last_physical_free": physical_total - physical_used,
            "max_physical_total": physical_total,
            "max_physical_used": physical_used,
            "logical_provisioned": logical_provisioned,
            "logical_used": logical_used,
            "last_logical_provisioned": logical_provisioned,
            "last_logical_used": logical_used,
            "max_logical_provisioned": logical_provisioned,
            "max_logical_used": logical_used,
            "efficiency_ratio": 2.4,
            "data_reduction": 2.1,
            "snapshot_savings": 3 * TB,
            "thin_savings": 11 * TB,
            "last_efficiency_ratio": 2.4,
            "last_thin_savings": 11 * TB,
            "subscribed_capacity": logical_used,
        }

    def _history(self, kind: str, entity_id: str, interval: str, scale: float = 1.0) -> list[dict[str, Any]]:
        if interval not in {"One_Hour", "One_Day"}:
            moment = self._now()
            phase = moment.timestamp() / 90.0 + _seed(entity_id)
            if kind == "space":
                return [self._space_sample(moment)]
            if kind == "wear":
                return [{
                    "timestamp": _iso(moment),
                    "percent_endurance_remaining": 82 + (_seed(entity_id) % 15),
                }]
            return [self._perf_sample(moment, phase, scale)]

        key = (kind, entity_id, interval)
        cached = self._series_cache.get(key)
        if cached is not None:
            return cached

        step = timedelta(days=1) if interval == "One_Day" else timedelta(hours=1)
        count = 30 if interval == "One_Day" else _HOUR_HISTORY
        end = self._now().replace(minute=0, second=0, microsecond=0)
        samples: list[dict[str, Any]] = []
        for index in range(count):
            moment = end - step * (count - 1 - index)
            if kind == "space":
                samples.append(self._space_sample(moment))
            elif kind == "wear":
                samples.append({
                    "timestamp": _iso(moment),
                    "percent_endurance_remaining": 90 - (index % 8),
                })
            else:
                samples.append(self._perf_sample(moment, index / 6.0 + _seed(entity_id) / 50.0))
        self._series_cache[key] = samples
        return samples

    def _kind(self, entity: str) -> str:
        if entity.startswith("space_"):
            return "space"
        if entity.startswith("wear_"):
            return "wear"
        if entity.startswith("copy_"):
            return "copy"
        return "performance"

    async def generate_metrics(
        self,
        entity: str,
        entity_id: str,
        interval: str = "Five_Mins",
    ) -> list[dict[str, Any]]:
        self.last_metrics_error = None
        scale = 0.2 if any(token in entity for token in ("volume", "host", "file_system", "port", "copy_")) else 1.0
        return self._history(self._kind(entity), str(entity_id), interval, scale)

    async def get_cluster(self) -> list[dict[str, Any]]:
        return [{
            "id": "mock-cluster",
            "name": "Mock-PowerStore",
            "management_address": self.cluster_ip or "10.0.0.1",
        }]

    async def get_cluster_detail(self) -> list[dict[str, Any]]:
        now = _iso(self._now())
        return [{
            "id": "mock-cluster",
            "name": "Mock-PowerStore",
            "global_id": "MOCKCLUSTER01",
            "management_address": self.cluster_ip or "10.0.0.1",
            "appliance_count": 1,
            "state": "Configured",
            "is_encryption_enabled": True,
            "system_time": now,
            "primary_appliance_id": "mock-appliance-a",
            "physical_mtu": 1500,
        }]

    async def get_appliances(self) -> list[dict[str, Any]]:
        return [{
            "id": "mock-appliance-a",
            "name": "Appliance-A",
            "service_tag": "MOCKTAG01",
            "model": "PowerStore 500T",
        }]

    async def get_appliances_detail(self) -> list[dict[str, Any]]:
        appliances = await self.get_appliances()
        appliances[0].update({
            "node_count": 2,
            "drive_failure_tolerance_level": "Single",
            "storage_class": "Block",
            "software_installed": [{
                "release_version": "4.0.0.0",
                "build_version": "mock",
                "installed_date": "2026-01-15T00:00:00Z",
            }],
        })
        return appliances

    async def get_nodes(self) -> list[dict[str, Any]]:
        return [
            {"id": "mock-node-a", "slot": 0, "appliance_id": "mock-appliance-a"},
            {"id": "mock-node-b", "slot": 1, "appliance_id": "mock-appliance-a"},
        ]

    async def get_volumes(self, primary_only: bool = False) -> list[dict[str, Any]]:
        volumes = [
            {
                "id": "mock-vol-1",
                "name": "mock-oracle-data",
                "type": "Primary",
                "state": "Ready",
                "size": 4 * TB,
                "wwn": "6000000000000001",
                "appliance_id": "mock-appliance-a",
                "nsid": 1,
            },
            {
                "id": "mock-vol-2",
                "name": "mock-vm-datastore",
                "type": "Primary",
                "state": "Ready",
                "size": 8 * TB,
                "wwn": "6000000000000002",
                "appliance_id": "mock-appliance-a",
                "nsid": 2,
            },
            {
                "id": "mock-vol-3",
                "name": "mock-oracle-data-snap",
                "type": "Snapshot",
                "state": "Ready",
                "size": 4 * TB,
                "wwn": "6000000000000003",
                "appliance_id": "mock-appliance-a",
                "nsid": 3,
            },
        ]
        if primary_only:
            return [volume for volume in volumes if volume.get("type") == "Primary"]
        return volumes

    async def get_hosts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "mock-host-1",
                "name": "mock-esx-01",
                "os_type": "ESXi",
                "host_connectivity": "Connected",
                "host_group_id": None,
                "description": "Mock ESXi host",
            },
            {
                "id": "mock-host-2",
                "name": "mock-db-01",
                "os_type": "Linux",
                "host_connectivity": "Connected",
                "host_group_id": None,
                "description": "Mock database host",
            },
        ]

    async def get_host_volume_mappings(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "mock-map-1",
                "host_id": "mock-host-1",
                "host_group_id": None,
                "volume_id": "mock-vol-2",
                "logical_unit_number": 0,
            },
            {
                "id": "mock-map-2",
                "host_id": "mock-host-2",
                "host_group_id": None,
                "volume_id": "mock-vol-1",
                "logical_unit_number": 1,
            },
        ]

    async def get_alerts(self, severity_filter: str | None = None) -> list[dict[str, Any]]:
        now = self._now()
        alerts = [
            {
                "id": "mock-alert-critical",
                "event_code": "0x1001",
                "severity": "Critical",
                "severity_l10n": "Critical",
                "resource_type": "drive",
                "resource_id": "mock-drive-2",
                "resource_name": "Drive 2",
                "description_l10n": "Mock critical alert: drive predictive failure",
                "generated_timestamp": _iso(now - timedelta(minutes=12)),
                "raised_timestamp": _iso(now - timedelta(minutes=12)),
                "state": "ACTIVE",
                "state_l10n": "Active",
                "is_acknowledged": False,
            },
            {
                "id": "mock-alert-major",
                "event_code": "0x1002",
                "severity": "Major",
                "severity_l10n": "Major",
                "resource_type": "fc_port",
                "resource_id": "mock-fc-1",
                "resource_name": "FC Port 1",
                "description_l10n": "Mock major alert: fibre channel link down",
                "generated_timestamp": _iso(now - timedelta(hours=2)),
                "raised_timestamp": _iso(now - timedelta(hours=2)),
                "state": "ACTIVE",
                "state_l10n": "Active",
                "is_acknowledged": False,
            },
            {
                "id": "mock-alert-minor",
                "event_code": "0x1003",
                "severity": "Minor",
                "severity_l10n": "Minor",
                "resource_type": "volume",
                "resource_id": "mock-vol-1",
                "resource_name": "mock-oracle-data",
                "description_l10n": "Mock minor alert: volume space threshold",
                "generated_timestamp": _iso(now - timedelta(hours=6)),
                "raised_timestamp": _iso(now - timedelta(hours=6)),
                "state": "ACTIVE",
                "state_l10n": "Active",
                "is_acknowledged": True,
            },
        ]
        if severity_filter:
            return [alert for alert in alerts if alert["severity"] == severity_filter]
        return alerts

    async def get_events(self, severity_filter: str | None = None) -> list[dict[str, Any]]:
        now = self._now()
        events = [
            {
                "id": "mock-event-1",
                "event_code": "0x2001",
                "severity": "Info",
                "severity_l10n": "Info",
                "resource_type": "cluster",
                "resource_type_l10n": "Cluster",
                "resource_id": "mock-cluster",
                "resource_name": "Mock-PowerStore",
                "description_l10n": "Mock event: metrics collection completed",
                "generated_timestamp": _iso(now - timedelta(minutes=5)),
            },
            {
                "id": "mock-event-2",
                "event_code": "0x2002",
                "severity": "Major",
                "severity_l10n": "Major",
                "resource_type": "fc_port",
                "resource_type_l10n": "Fibre Channel Port",
                "resource_id": "mock-fc-1",
                "resource_name": "FC Port 1",
                "description_l10n": "Mock event: link state changed to down",
                "generated_timestamp": _iso(now - timedelta(hours=2)),
            },
        ]
        if severity_filter:
            return [event for event in events if event["severity"] == severity_filter]
        return events

    async def get_hardware(self) -> list[dict[str, Any]]:
        nodes = await self.get_nodes()
        items: list[dict[str, Any]] = []
        for node in nodes:
            items.append({
                "id": f"hw-{node['id']}",
                "name": f"Node {node['slot']}",
                "type": "Node",
                "type_l10n": "Node",
                "lifecycle_state": "Healthy",
                "lifecycle_state_l10n": "Healthy",
                "appliance_id": "mock-appliance-a",
                "slot": node["slot"],
                "part_number": "NODE-MOCK",
                "serial_number": f"SN-NODE-{node['slot']}",
                "status_led_state": "Off",
                "extra_details": None,
            })
        for hw_type, name, state, slot in (
            ("Fan", "Fan 0", "Healthy", 0),
            ("Power_Supply", "PSU 0", "Healthy", 0),
            ("Battery", "Battery 0", "Healthy", 0),
            ("Drive", "Drive 0", "Healthy", 0),
            ("Drive", "Drive 1", "Healthy", 1),
            ("Drive", "Drive 2", "Failed", 2),
        ):
            items.append({
                "id": f"mock-{hw_type.lower()}-{slot}",
                "name": name,
                "type": hw_type,
                "type_l10n": hw_type.replace("_", " "),
                "lifecycle_state": state,
                "lifecycle_state_l10n": state,
                "appliance_id": "mock-appliance-a",
                "slot": slot,
                "part_number": f"{hw_type}-MOCK",
                "serial_number": f"SN-{hw_type}-{slot}",
                "status_led_state": "Fault" if state == "Failed" else "Off",
                "extra_details": None,
            })
        return items

    async def get_fc_ports(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "mock-fc-0",
                "name": "FC Port 0",
                "appliance_id": "mock-appliance-a",
                "node_id": "mock-node-a",
                "wwn": "10:00:00:00:00:00:00:01",
                "is_link_up": True,
                "is_in_use": True,
                "current_speed": "32_Gbps",
                "current_speed_l10n": "32 Gbps",
            },
            {
                "id": "mock-fc-1",
                "name": "FC Port 1",
                "appliance_id": "mock-appliance-a",
                "node_id": "mock-node-b",
                "wwn": "10:00:00:00:00:00:00:02",
                "is_link_up": False,
                "is_in_use": True,
                "current_speed": "32_Gbps",
                "current_speed_l10n": "32 Gbps",
            },
        ]

    async def get_eth_ports(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "mock-eth-0",
                "name": "ETH Port 0",
                "appliance_id": "mock-appliance-a",
                "node_id": "mock-node-a",
                "mac_address": "00:11:22:33:44:55",
                "is_link_up": True,
                "is_in_use": True,
                "current_speed": "25_Gbps",
                "current_mtu": 1500,
                "stale_state": "Inactive",
            },
            {
                "id": "mock-eth-1",
                "name": "ETH Port 1",
                "appliance_id": "mock-appliance-a",
                "node_id": "mock-node-b",
                "mac_address": "00:11:22:33:44:66",
                "is_link_up": True,
                "is_in_use": True,
                "current_speed": "25_Gbps",
                "current_mtu": 1500,
                "stale_state": "Inactive",
            },
        ]

    async def get_audit_events(self) -> list[dict[str, Any]]:
        now = self._now()
        return [
            {
                "id": "mock-audit-1",
                "type": "Login",
                "timestamp": _iso(now - timedelta(minutes=3)),
                "username": "mock-admin",
                "is_successful": True,
                "client_address": "127.0.0.1",
                "resource_type": "user",
                "resource_action": "login",
                "message_l10n": "Mock audit: local login succeeded",
                "appliance_id": "mock-appliance-a",
            }
        ]

    async def acknowledge_alert(self, alert_id: str) -> None:
        return None

    async def get_nas_servers(self) -> list[dict[str, Any]]:
        return [{
            "id": "mock-nas-1",
            "name": "mock-nas",
            "operational_status": "Started",
            "current_node_id": "mock-node-a",
            "preferred_node_id": "mock-node-a",
        }]

    async def get_file_systems(self) -> list[dict[str, Any]]:
        return [{
            "id": "mock-fs-1",
            "name": "mock-share",
            "nas_server_id": "mock-nas-1",
            "filesystem_type": "Primary",
            "size_total": 2 * TB,
            "size_used": int(0.6 * 2 * TB),
        }]

    async def get_replication_sessions(self) -> list[dict[str, Any]]:
        return [{
            "id": "mock-repl-1",
            "state": "OK",
            "role": "Source",
            "resource_type": "volume",
            "type": "Metro",
            "last_sync_timestamp": _iso(self._now() - timedelta(minutes=1)),
            "local_resource_id": "mock-vol-1",
            "remote_resource_id": "mock-remote-vol-1",
            "remote_system_id": "mock-remote-1",
            "progress_percentage": 100,
            "replication_rule_id": "mock-rule-1",
        }]

    async def get_remote_systems(self) -> list[dict[str, Any]]:
        return [{
            "id": "mock-remote-1",
            "name": "Mock-Remote",
            "management_address": "10.255.0.10",
            "type": "PowerStore",
            "state": "OK",
            "data_connection_state": "OK",
            "version": "4.0.0.0",
            "serial_number": "MOCKREMOTE01",
        }]

    async def get_policies(self, policy_type: str | None = None) -> list[dict[str, Any]]:
        policies = [{
            "id": "mock-policy-1",
            "name": "Mock-Daily-Protection",
            "description": "Sample protection policy",
            "type": "Protection",
            "is_replica": False,
            "is_read_only": False,
        }]
        if policy_type:
            return [policy for policy in policies if policy["type"] == policy_type]
        return policies

    async def get_snapshot_rules(self) -> list[dict[str, Any]]:
        return [{
            "id": "mock-snap-rule-1",
            "name": "Mock-Daily-Snapshot",
            "interval": "One_Day",
            "time_of_day": "02:00",
            "days_of_week": ["Monday", "Wednesday", "Friday"],
            "policies": [{"id": "mock-policy-1"}],
        }]

    async def get_volume_groups(self) -> list[dict[str, Any]]:
        return [{
            "id": "mock-vg-1",
            "name": "mock-group",
            "description": "Sample volume group",
            "volume_count": 2,
        }]

    async def enable_fast_metrics(self, volume_id: str) -> None:
        return None

    async def create_datacollection(self, description: str = "") -> dict[str, Any]:
        now = _iso(self._now())
        item = {
            "id": "mock-datacollection-1",
            "status": "SUCCESS",
            "status_message": "Mock collection ready",
            "start_timestamp": now,
            "end_timestamp": now,
            "description": description or "Mock log bundle",
            "compressed_size": 128,
            "uncompressed_size": 256,
            "appliances": [{
                "id": "mock-appliance-a",
                "status": "Success",
                "download_uri": "/mock/datacollection/mock-datacollection-1",
            }],
        }
        self._collections[item["id"]] = item
        return item

    async def get_datacollection(self, collection_id: str) -> dict[str, Any]:
        existing = self._collections.get(collection_id)
        if existing:
            return existing
        created = await self.create_datacollection("Mock log bundle")
        created["id"] = collection_id
        self._collections[collection_id] = created
        return created

    async def list_datacollections(self, limit: int = 25) -> list[dict[str, Any]]:
        if not self._collections:
            await self.create_datacollection("Mock log bundle")
        return list(self._collections.values())[:limit]

    async def stream_binary(self, path: str, chunk_size: int = 1024 * 1024):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mock-bundle.txt", "Mock PowerStore log bundle. No cluster was contacted.\n")
        payload = buffer.getvalue()
        for offset in range(0, len(payload), chunk_size):
            yield payload[offset:offset + chunk_size]

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return {"id": job_id, "state": "COMPLETED", "description": "Mock job"}
