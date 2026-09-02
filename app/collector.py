"""Background polling collector and SSE event bus."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from app.client import PowerStoreAuthError, PowerStoreClient
from app.config import settings
from app.credentials import get_credentials
from app.db import Database, utc_now
from app.monitor_target import get_active_cluster_ip, get_monitor_location_name
from app.notify import notify_critical

logger = logging.getLogger(__name__)

PollFn = Callable[[], Awaitable[int]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[str]] = []

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        payload = json.dumps({"type": event_type, "data": data or {}})
        dead: list[asyncio.Queue[str]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self.unsubscribe(queue)


class Collector:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus
        self._client = PowerStoreClient()
        self._tasks: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()
        self._cluster_id: str | None = None
        self._appliance_ids: list[str] = []
        self._node_ids: list[str] = []

    async def start(self) -> None:
        await self._client.open()
        self._stop.clear()
        loops: list[tuple[str, int, PollFn, int]] = [
            ("alerts", settings.poll_alerts_sec, self._poll_alerts, 0),
            ("events", settings.poll_events_sec, self._poll_events, 1),
            ("hardware", settings.poll_hardware_sec, self._poll_hardware, 2),
            ("perf_fast", settings.poll_perf_fast_sec, self._poll_perf_fast, 3),
            ("space", settings.poll_space_sec, self._poll_space, 4),
            ("inventory", settings.poll_inventory_sec, self._poll_inventory, 5),
            ("io_rank", settings.poll_io_rank_sec, self._poll_io_rank, 6),
            ("wear", settings.poll_wear_sec, self._poll_wear, 7),
            ("audit", settings.poll_audit_sec, self._poll_audit, 8),
            ("cluster_info", settings.poll_cluster_info_sec, self._poll_cluster_info, 9),
            ("port_perf", settings.poll_port_perf_sec, self._poll_port_perf, 10),
            ("object_space", settings.poll_object_space_sec, self._poll_object_space, 11),
            ("protection", settings.poll_protection_sec, self._poll_protection, 12),
        ]
        for name, interval, fn, stagger in loops:
            self._tasks.append(asyncio.create_task(self._run_loop(name, interval, fn, stagger)))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._client.close()

    async def set_cluster_ip(self, cluster_ip: str) -> None:
        cluster_ip = cluster_ip.strip()
        if not cluster_ip:
            return
        self._client.cluster_ip = cluster_ip
        self._client.base_url = f"https://{cluster_ip}/api/rest"
        self._client._csrf_token = None
        self._cluster_id = None
        self._appliance_ids = []
        self._node_ids = []
        await self.db.set_status("cluster_ip", cluster_ip)
        location_name = await get_monitor_location_name(self.db) or ""
        await self.bus.publish("status", {
            "cluster_ip": cluster_ip,
            "monitor_location": location_name,
            "connection": "pending",
        })

    async def _sync_cluster_target(self) -> str | None:
        cluster_ip = await get_active_cluster_ip(self.db)
        if cluster_ip:
            await self.set_cluster_ip(cluster_ip)
        return cluster_ip

    async def _ensure_logged_in(self) -> bool:
        creds = await get_credentials()
        if not creds:
            await self.db.set_status("connection", "no_credentials")
            await self.bus.publish("status", {"connection": "no_credentials"})
            return False
        cluster_ip = await self._sync_cluster_target()
        if not cluster_ip:
            await self.db.set_status("connection", "error")
            await self.db.set_status("last_error", "No monitoring location selected")
            await self.bus.publish("status", {"connection": "error", "error": "No monitoring location selected"})
            return False
        username, password = creds
        try:
            await self._client.login(username, password)
            await self.db.set_status("connection", "connected")
            await self.db.set_status("cluster_ip", cluster_ip)
            location_name = await get_monitor_location_name(self.db) or ""
            await self.bus.publish("status", {
                "connection": "connected",
                "cluster_ip": cluster_ip,
                "monitor_location": location_name,
            })
            return True
        except PowerStoreAuthError as exc:
            logger.warning("Auth failed: %s", exc)
            await self.db.set_status("connection", "auth_failed")
            await self.db.set_status("last_error", str(exc))
            await self.bus.publish("status", {"connection": "auth_failed", "error": str(exc)})
            return False
        except Exception as exc:
            logger.exception("Connection failed")
            await self.db.set_status("connection", "error")
            await self.db.set_status("last_error", str(exc))
            await self.bus.publish("status", {"connection": "error", "error": str(exc)})
            return False

    async def _run_loop(self, poll_type: str, interval: int, fn: PollFn, stagger: int) -> None:
        await asyncio.sleep(stagger)
        while not self._stop.is_set():
            poll_id = await self.db.start_poll(poll_type)
            try:
                if not await self._ensure_logged_in():
                    await self.db.finish_poll(poll_id, success=False, error_message="Not logged in")
                    await asyncio.sleep(30)
                    continue
                count = await fn()
                await self.db.finish_poll(poll_id, success=True, item_count=count)
                await self.db.set_status(f"last_{poll_type}_poll", utc_now())
            except PowerStoreAuthError as exc:
                await self.db.finish_poll(poll_id, success=False, error_message=str(exc))
                await self.db.set_status("connection", "auth_failed")
            except Exception as exc:
                logger.exception("Poll %s failed", poll_type)
                await self.db.finish_poll(poll_id, success=False, error_message=str(exc))
                await self.db.set_status(f"last_{poll_type}_error", str(exc))

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                continue

    async def _refresh_entity_ids(self) -> None:
        try:
            clusters = await self._client.get_cluster()
            if clusters:
                self._cluster_id = clusters[0]["id"]
        except Exception as exc:
            logger.warning("Cluster list failed: %s", exc)

        self._appliance_ids = []
        try:
            appliances = await self._client.get_appliances()
            self._appliance_ids = [a["id"] for a in appliances]
        except Exception as exc:
            logger.warning("Appliance list failed: %s", exc)

        self._node_ids = []
        try:
            nodes = await self._client.get_nodes()
            self._node_ids = [n["id"] for n in nodes]
            if not self._appliance_ids:
                self._appliance_ids = list(dict.fromkeys(
                    n["appliance_id"] for n in nodes if n.get("appliance_id")
                ))
        except Exception as exc:
            logger.warning("Node list failed, using DB cache: %s", exc)
            db_nodes = await self.db.list_nodes()
            self._node_ids = [n["id"] for n in db_nodes]
            if not self._appliance_ids:
                self._appliance_ids = list(dict.fromkeys(
                    n["appliance_id"] for n in db_nodes if n.get("appliance_id")
                ))

    async def _poll_alerts(self) -> int:
        items = await self._client.get_alerts()
        new_critical = await self.db.upsert_alerts(items)
        for alert in new_critical:
            if not await self.db.is_notified(alert["id"]):
                notify_critical(
                    "PowerStore Critical Alert",
                    alert.get("description_l10n") or alert.get("resource_name") or alert["id"],
                )
                await self.db.mark_notified(alert["id"])
        await self.bus.publish("alerts", {"count": len(items), "new_critical": len(new_critical)})
        return len(items)

    async def _poll_events(self) -> int:
        items = await self._client.get_events()
        await self.db.upsert_events(items)
        await self.bus.publish("events", {"count": len(items)})
        return len(items)

    async def _poll_hardware(self) -> int:
        hardware = await self._client.get_hardware()
        await self.db.upsert_hardware(hardware)
        fc_ports = await self._client.get_fc_ports()
        eth_ports = await self._client.get_eth_ports()
        await self.db.upsert_ports("fc", fc_ports)
        await self.db.upsert_ports("eth", eth_ports)
        await self.bus.publish(
            "hardware",
            {"hardware": len(hardware), "fc_ports": len(fc_ports), "eth_ports": len(eth_ports)},
        )
        return len(hardware) + len(fc_ports) + len(eth_ports)

    async def _poll_perf_fast(self) -> int:
        await self._refresh_entity_ids()
        count = 0
        if self._cluster_id:
            samples = await self._client.generate_metrics_with_fallback(
                "performance_metrics_by_cluster",
                self._cluster_id,
                "Twenty_Sec",
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "performance_metrics_by_cluster",
                    self._cluster_id,
                    "performance",
                    samples[-1:],
                )
                count += 1

        for appliance_id in self._appliance_ids:
            samples = await self._client.generate_metrics_with_fallback(
                "performance_metrics_by_appliance",
                appliance_id,
                "Twenty_Sec",
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "performance_metrics_by_appliance",
                    appliance_id,
                    "performance",
                    samples[-1:],
                )
                count += 1

        for node_id in self._node_ids:
            samples = await self._client.generate_metrics_with_fallback(
                "performance_metrics_by_node",
                node_id,
                "Twenty_Sec",
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "performance_metrics_by_node",
                    node_id,
                    "performance",
                    samples[-1:],
                )
                count += 1

        pinned = await self.db.list_pinned_volumes()
        for volume_id in pinned:
            samples = await self._client.generate_metrics_with_fallback(
                "performance_metrics_by_volume",
                volume_id,
                "Five_Sec",
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "performance_metrics_by_volume",
                    volume_id,
                    "performance",
                    samples[-1:],
                )
                count += 1

        await self.db.prune_metrics()
        await self.bus.publish("perf", {"count": count})
        return count

    async def _poll_space(self) -> int:
        await self._refresh_entity_ids()
        count = 0
        if self._cluster_id:
            samples = await self._client.generate_metrics(
                "space_metrics_by_cluster",
                self._cluster_id,
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "space_metrics_by_cluster",
                    self._cluster_id,
                    "space",
                    samples[-1:],
                )
                count += 1

        for appliance_id in self._appliance_ids:
            samples = await self._client.generate_metrics(
                "space_metrics_by_appliance",
                appliance_id,
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "space_metrics_by_appliance",
                    appliance_id,
                    "space",
                    samples[-1:],
                )
                count += 1

        await self.bus.publish("space", {"count": count})
        return count

    async def _poll_inventory(self) -> int:
        volumes = await self._client.get_volumes(primary_only=False)
        hosts = await self._client.get_hosts()
        mappings = await self._client.get_host_volume_mappings()
        nodes = await self._client.get_nodes()
        await self.db.upsert_volumes(volumes)
        await self.db.upsert_hosts(hosts)
        await self.db.upsert_host_volume_maps(mappings)
        await self.db.upsert_nodes(nodes)

        nas_count = 0
        fs_count = 0
        try:
            nas = await self._client.get_nas_servers()
            await self.db.upsert_nas_servers(nas)
            nas_count = len(nas)
            if nas:
                file_systems = await self._client.get_file_systems()
                await self.db.upsert_file_systems(file_systems)
                fs_count = len(file_systems)
        except Exception as exc:
            logger.debug("NAS inventory skipped: %s", exc)

        total = len(volumes) + len(hosts) + len(mappings) + len(nodes) + nas_count + fs_count
        await self.bus.publish(
            "inventory",
            {
                "volumes": len(volumes),
                "hosts": len(hosts),
                "mappings": len(mappings),
                "nodes": len(nodes),
                "nas_servers": nas_count,
                "file_systems": fs_count,
            },
        )
        return total

    async def _poll_io_rank(self) -> int:
        try:
            volumes = await self._client.get_volumes(primary_only=True)
        except Exception as exc:
            logger.warning("Volume list failed, using DB cache: %s", exc)
            volumes = [{"id": v["id"]} for v in await self.db.list_volumes(primary_only=True)]
        volumes = volumes[: settings.io_rank_volume_cap]
        try:
            hosts = (await self._client.get_hosts())[: settings.io_rank_host_cap]
        except Exception as exc:
            logger.warning("Host list failed, using DB cache: %s", exc)
            hosts = [{"id": h["id"]} for h in (await self.db.list_hosts())[: settings.io_rank_host_cap]]
        count = 0

        for vol in volumes:
            samples = await self._client.generate_metrics(
                "performance_metrics_by_volume",
                vol["id"],
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "performance_metrics_by_volume",
                    vol["id"],
                    "performance",
                    samples[-1:],
                )
                count += 1

        for host in hosts:
            samples = await self._client.generate_metrics(
                "performance_metrics_by_host",
                host["id"],
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "performance_metrics_by_host",
                    host["id"],
                    "performance",
                    samples[-1:],
                )
                count += 1

        file_systems = await self.db.list_file_systems()
        for fs in file_systems[:10]:
            try:
                samples = await self._client.generate_metrics(
                    "performance_metrics_by_file_system",
                    fs["id"],
                    "Five_Mins",
                )
                if samples:
                    await self.db.insert_metrics(
                        "performance_metrics_by_file_system",
                        fs["id"],
                        "performance",
                        samples[-1:],
                    )
                    count += 1
            except Exception:
                pass

        await self.bus.publish("io_rank", {"count": count})
        return count

    async def _poll_wear(self) -> int:
        drives = [h for h in await self._client.get_hardware() if h.get("type") == "Drive"]
        count = 0
        for drive in drives:
            samples = await self._client.generate_metrics(
                "wear_metrics_by_drive_daily",
                drive["id"],
                "One_Day",
            )
            if samples:
                await self.db.insert_metrics(
                    "wear_metrics_by_drive_daily",
                    drive["id"],
                    "wear",
                    samples[-1:],
                )
                count += 1
        await self.bus.publish("metrics", {"count": count, "type": "wear"})
        return count

    async def _poll_audit(self) -> int:
        try:
            items = await self._client.get_audit_events()
        except PowerStoreAuthError:
            await self.db.set_status("audit_access", "denied")
            await self.bus.publish("audit", {"count": 0, "access": "denied"})
            raise
        await self.db.set_status("audit_access", "ok")
        await self.db.upsert_audit_events(items)
        await self.bus.publish("audit", {"count": len(items), "access": "ok"})
        return len(items)

    async def _poll_cluster_info(self) -> int:
        count = 0
        clusters = await self._client.get_cluster_detail()
        for cluster in clusters:
            await self.db.upsert_cluster_info(cluster)
            count += 1
            self._cluster_id = cluster["id"]

        appliances = await self._client.get_appliances_detail()
        await self.db.upsert_appliances(appliances)
        count += len(appliances)
        self._appliance_ids = [a["id"] for a in appliances]

        await self.bus.publish("cluster_info", {"appliances": len(appliances)})
        return count

    async def _poll_port_perf(self) -> int:
        count = 0
        fc_ports = (await self._client.get_fc_ports())[: settings.port_perf_cap]
        eth_ports = (await self._client.get_eth_ports())[: settings.port_perf_cap]

        for port in fc_ports:
            samples = await self._client.generate_metrics_with_fallback(
                "performance_metrics_by_fe_fc_port",
                port["id"],
                "Twenty_Sec",
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "performance_metrics_by_fe_fc_port",
                    port["id"],
                    "performance",
                    samples[-1:],
                )
                count += 1

        for port in eth_ports:
            samples = await self._client.generate_metrics_with_fallback(
                "performance_metrics_by_fe_eth_port",
                port["id"],
                "Twenty_Sec",
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "performance_metrics_by_fe_eth_port",
                    port["id"],
                    "performance",
                    samples[-1:],
                )
                count += 1

        await self.bus.publish("port_perf", {"count": count})
        return count

    async def _poll_object_space(self) -> int:
        count = 0
        volumes = (await self._client.get_volumes(primary_only=True))[: settings.space_volume_cap]
        for vol in volumes:
            samples = await self._client.generate_metrics(
                "space_metrics_by_volume",
                vol["id"],
                "Five_Mins",
            )
            if samples:
                await self.db.insert_metrics(
                    "space_metrics_by_volume",
                    vol["id"],
                    "space",
                    samples[-1:],
                )
                count += 1

        file_systems = await self.db.list_file_systems()
        for fs in file_systems[:10]:
            try:
                samples = await self._client.generate_metrics(
                    "space_metrics_by_file_system",
                    fs["id"],
                    "Five_Mins",
                )
                if samples:
                    await self.db.insert_metrics(
                        "space_metrics_by_file_system",
                        fs["id"],
                        "space",
                        samples[-1:],
                    )
                    count += 1
            except Exception:
                pass

        await self.db.prune_metrics()
        await self.bus.publish("object_space", {"count": count})
        return count

    async def _poll_protection(self) -> int:
        count = 0
        try:
            sessions = await self._client.get_replication_sessions()
            await self.db.upsert_replication_sessions(sessions)
            count += len(sessions)
        except Exception as exc:
            logger.debug("Replication sessions skipped: %s", exc)

        try:
            remote_systems = await self._client.get_remote_systems()
            await self.db.upsert_remote_systems(remote_systems)
            count += len(remote_systems)
        except Exception as exc:
            logger.debug("Remote systems skipped: %s", exc)

        try:
            policies = await self._client.get_policies(policy_type="Protection")
            await self.db.upsert_protection_policies(policies)
            count += len(policies)
        except Exception as exc:
            logger.debug("Protection policies skipped: %s", exc)

        try:
            rules = await self._client.get_snapshot_rules()
            await self.db.upsert_snapshot_rules(rules)
            count += len(rules)
        except Exception as exc:
            logger.debug("Snapshot rules skipped: %s", exc)

        copy_count = 0
        await self._refresh_entity_ids()
        if self._cluster_id:
            try:
                samples = await self._client.generate_metrics(
                    "copy_metrics_by_cluster",
                    self._cluster_id,
                    "Five_Mins",
                )
                if samples:
                    await self.db.insert_metrics(
                        "copy_metrics_by_cluster",
                        self._cluster_id,
                        "copy",
                        samples[-1:],
                    )
                    copy_count += 1
            except Exception:
                pass

        for appliance_id in self._appliance_ids:
            try:
                samples = await self._client.generate_metrics(
                    "copy_metrics_by_appliance",
                    appliance_id,
                    "Five_Mins",
                )
                if samples:
                    await self.db.insert_metrics(
                        "copy_metrics_by_appliance",
                        appliance_id,
                        "copy",
                        samples[-1:],
                    )
                    copy_count += 1
            except Exception:
                pass

        await self.bus.publish("protection", {"count": count, "copy_metrics": copy_count})
        return count + copy_count

    def get_client(self) -> PowerStoreClient:
        return self._client
