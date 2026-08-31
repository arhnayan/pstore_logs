"""SQLite persistence layer."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import aiosqlite

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    event_code TEXT,
    severity TEXT,
    resource_type TEXT,
    resource_id TEXT,
    resource_name TEXT,
    description TEXT,
    generated_timestamp TEXT,
    raised_timestamp TEXT,
    state TEXT,
    state_l10n TEXT,
    is_acknowledged INTEGER DEFAULT 0,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    event_code TEXT,
    severity TEXT,
    resource_type TEXT,
    resource_id TEXT,
    resource_name TEXT,
    description TEXT,
    generated_timestamp TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hardware (
    id TEXT PRIMARY KEY,
    name TEXT,
    hw_type TEXT,
    lifecycle_state TEXT,
    appliance_id TEXT,
    slot INTEGER,
    part_number TEXT,
    serial_number TEXT,
    status_led_state TEXT,
    extra_json TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ports (
    id TEXT PRIMARY KEY,
    port_type TEXT NOT NULL,
    name TEXT,
    appliance_id TEXT,
    node_id TEXT,
    is_link_up INTEGER,
    is_in_use INTEGER,
    details_json TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    timestamp TEXT,
    payload_json TEXT NOT NULL,
    collected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    event_type TEXT,
    timestamp TEXT,
    username TEXT,
    is_successful INTEGER,
    client_address TEXT,
    resource_type TEXT,
    resource_action TEXT,
    message TEXT,
    appliance_id TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    item_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notified_alerts (
    alert_id TEXT PRIMARY KEY,
    notified_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collector_status (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS volumes (
    id TEXT PRIMARY KEY,
    name TEXT,
    vol_type TEXT,
    state TEXT,
    size INTEGER,
    wwn TEXT,
    appliance_id TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hosts (
    id TEXT PRIMARY KEY,
    name TEXT,
    os_type TEXT,
    host_connectivity TEXT,
    host_group_id TEXT,
    description TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS host_volume_maps (
    id TEXT PRIMARY KEY,
    host_id TEXT,
    host_group_id TEXT,
    volume_id TEXT,
    logical_unit_number INTEGER,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    slot INTEGER,
    appliance_id TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nas_servers (
    id TEXT PRIMARY KEY,
    name TEXT,
    operational_status TEXT,
    current_node_id TEXT,
    preferred_node_id TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_systems (
    id TEXT PRIMARY KEY,
    name TEXT,
    nas_server_id TEXT,
    filesystem_type TEXT,
    size_total INTEGER,
    size_used INTEGER,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pinned_volumes (
    volume_id TEXT PRIMARY KEY,
    pinned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_info (
    id TEXT PRIMARY KEY,
    name TEXT,
    global_id TEXT,
    management_address TEXT,
    appliance_count INTEGER,
    state TEXT,
    is_encryption_enabled INTEGER,
    system_time TEXT,
    raw_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appliances (
    id TEXT PRIMARY KEY,
    name TEXT,
    service_tag TEXT,
    model TEXT,
    node_count INTEGER,
    drive_failure_tolerance_level TEXT,
    storage_class TEXT,
    release_version TEXT,
    installed_date TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replication_sessions (
    id TEXT PRIMARY KEY,
    state TEXT,
    role TEXT,
    resource_type TEXT,
    session_type TEXT,
    last_sync_timestamp TEXT,
    local_resource_id TEXT,
    remote_resource_id TEXT,
    remote_system_id TEXT,
    progress_percentage INTEGER,
    replication_rule_id TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_systems (
    id TEXT PRIMARY KEY,
    name TEXT,
    management_address TEXT,
    system_type TEXT,
    state TEXT,
    data_connection_state TEXT,
    version TEXT,
    serial_number TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS protection_policies (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    policy_type TEXT,
    is_replica INTEGER,
    is_read_only INTEGER,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_rules (
    id TEXT PRIMARY KEY,
    name TEXT,
    interval TEXT,
    time_of_day TEXT,
    days_of_week TEXT,
    policy_id TEXT,
    raw_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_state ON alerts(state);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(generated_timestamp);
CREATE INDEX IF NOT EXISTS idx_hardware_lifecycle ON hardware(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_metrics_entity ON metrics_samples(entity, entity_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or str(settings.db_path)
        self._schema_ready = False

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        if not self._schema_ready:
            await conn.executescript(SCHEMA)
            await conn.commit()
            self._schema_ready = True

    async def _connect(self) -> aiosqlite.Connection:
        settings.ensure_data_dir()
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        await self._ensure_schema(conn)
        return conn

    @asynccontextmanager
    async def session(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = await self._connect()
        try:
            yield conn
        finally:
            await conn.close()

    async def set_status(self, key: str, value: str) -> None:
        async with self.session() as conn:
            await conn.execute(
                "INSERT INTO collector_status(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            await conn.commit()

    async def get_status(self) -> dict[str, str]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT key, value FROM collector_status")
            rows = await cursor.fetchall()
            return {row["key"]: row["value"] for row in rows}

    async def start_poll(self, poll_type: str) -> int:
        async with self.session() as conn:
            cursor = await conn.execute(
                "INSERT INTO poll_runs(poll_type, started_at, success) VALUES(?, ?, 0)",
                (poll_type, utc_now()),
            )
            await conn.commit()
            return cursor.lastrowid or 0

    async def finish_poll(
        self,
        poll_id: int,
        *,
        success: bool,
        item_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        async with self.session() as conn:
            await conn.execute(
                "UPDATE poll_runs SET finished_at=?, success=?, item_count=?, error_message=? WHERE id=?",
                (utc_now(), int(success), item_count, error_message, poll_id),
            )
            await conn.commit()

    async def upsert_alerts(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now()
        new_critical: list[dict[str, Any]] = []
        async with self.session() as conn:
            for item in items:
                alert_id = item["id"]
                cursor = await conn.execute(
                    "SELECT id, severity, state FROM alerts WHERE id=?",
                    (alert_id,),
                )
                existing = await cursor.fetchone()
                is_new = existing is None
                await conn.execute(
                    """
                    INSERT INTO alerts(
                        id, event_code, severity, resource_type, resource_id, resource_name,
                        description, generated_timestamp, raised_timestamp, state, state_l10n,
                        is_acknowledged, raw_json, first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        event_code=excluded.event_code,
                        severity=excluded.severity,
                        resource_type=excluded.resource_type,
                        resource_id=excluded.resource_id,
                        resource_name=excluded.resource_name,
                        description=excluded.description,
                        generated_timestamp=excluded.generated_timestamp,
                        raised_timestamp=excluded.raised_timestamp,
                        state=excluded.state,
                        state_l10n=excluded.state_l10n,
                        is_acknowledged=excluded.is_acknowledged,
                        raw_json=excluded.raw_json,
                        last_seen=excluded.last_seen
                    """,
                    (
                        alert_id,
                        item.get("event_code"),
                        item.get("severity"),
                        item.get("resource_type"),
                        item.get("resource_id"),
                        item.get("resource_name"),
                        item.get("description_l10n"),
                        item.get("generated_timestamp"),
                        item.get("raised_timestamp"),
                        item.get("state"),
                        item.get("state_l10n"),
                        int(bool(item.get("is_acknowledged"))),
                        json.dumps(item),
                        now if is_new else now,
                        now,
                    ),
                )
                if (
                    is_new
                    and item.get("severity") == "Critical"
                    and item.get("state") == "ACTIVE"
                ):
                    new_critical.append(item)
            await conn.commit()
        return new_critical

    async def upsert_events(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute("SELECT id FROM events WHERE id=?", (item["id"],))
                is_new = await cursor.fetchone() is None
                await conn.execute(
                    """
                    INSERT INTO events(
                        id, event_code, severity, resource_type, resource_id, resource_name,
                        description, generated_timestamp, raw_json, first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        event_code=excluded.event_code,
                        severity=excluded.severity,
                        resource_type=excluded.resource_type,
                        resource_id=excluded.resource_id,
                        resource_name=excluded.resource_name,
                        description=excluded.description,
                        generated_timestamp=excluded.generated_timestamp,
                        raw_json=excluded.raw_json,
                        last_seen=excluded.last_seen
                    """,
                    (
                        item["id"],
                        item.get("event_code"),
                        item.get("severity"),
                        item.get("resource_type"),
                        item.get("resource_id"),
                        item.get("resource_name"),
                        item.get("description_l10n"),
                        item.get("generated_timestamp"),
                        json.dumps(item),
                        now if is_new else now,
                        now,
                    ),
                )
            await conn.commit()

    async def upsert_hardware(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute("SELECT id FROM hardware WHERE id=?", (item["id"],))
                is_new = await cursor.fetchone() is None
                extra = item.get("extra_details")
                await conn.execute(
                    """
                    INSERT INTO hardware(
                        id, name, hw_type, lifecycle_state, appliance_id, slot, part_number,
                        serial_number, status_led_state, extra_json, raw_json, first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        hw_type=excluded.hw_type,
                        lifecycle_state=excluded.lifecycle_state,
                        appliance_id=excluded.appliance_id,
                        slot=excluded.slot,
                        part_number=excluded.part_number,
                        serial_number=excluded.serial_number,
                        status_led_state=excluded.status_led_state,
                        extra_json=excluded.extra_json,
                        raw_json=excluded.raw_json,
                        last_seen=excluded.last_seen
                    """,
                    (
                        item["id"],
                        item.get("name"),
                        item.get("type"),
                        item.get("lifecycle_state"),
                        item.get("appliance_id"),
                        item.get("slot"),
                        item.get("part_number"),
                        item.get("serial_number"),
                        item.get("status_led_state"),
                        json.dumps(extra) if extra else None,
                        json.dumps(item),
                        now if is_new else now,
                        now,
                    ),
                )
            await conn.commit()

    async def upsert_ports(self, port_type: str, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute("SELECT id FROM ports WHERE id=?", (item["id"],))
                is_new = await cursor.fetchone() is None
                details = {
                    k: item.get(k)
                    for k in (
                        "wwn",
                        "current_speed",
                        "current_speed_l10n",
                        "mac_address",
                        "current_mtu",
                        "stale_state",
                    )
                    if item.get(k) is not None
                }
                await conn.execute(
                    """
                    INSERT INTO ports(
                        id, port_type, name, appliance_id, node_id, is_link_up, is_in_use,
                        details_json, raw_json, first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        port_type=excluded.port_type,
                        name=excluded.name,
                        appliance_id=excluded.appliance_id,
                        node_id=excluded.node_id,
                        is_link_up=excluded.is_link_up,
                        is_in_use=excluded.is_in_use,
                        details_json=excluded.details_json,
                        raw_json=excluded.raw_json,
                        last_seen=excluded.last_seen
                    """,
                    (
                        item["id"],
                        port_type,
                        item.get("name"),
                        item.get("appliance_id"),
                        item.get("node_id"),
                        int(item["is_link_up"]) if item.get("is_link_up") is not None else None,
                        int(item["is_in_use"]) if item.get("is_in_use") is not None else None,
                        json.dumps(details),
                        json.dumps(item),
                        now if is_new else now,
                        now,
                    ),
                )
            await conn.commit()

    async def insert_metrics(
        self,
        entity: str,
        entity_id: str,
        metric_type: str,
        samples: list[dict[str, Any]],
    ) -> None:
        now = utc_now()
        async with self.session() as conn:
            for sample in samples:
                await conn.execute(
                    """
                    INSERT INTO metrics_samples(
                        entity, entity_id, metric_type, timestamp, payload_json, collected_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity,
                        entity_id,
                        metric_type,
                        sample.get("timestamp"),
                        json.dumps(sample),
                        now,
                    ),
                )
            await conn.commit()

    async def prune_metrics(self, hours: int | None = None) -> int:
        hours = hours or settings.metrics_retention_hours
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        async with self.session() as conn:
            cursor = await conn.execute(
                "DELETE FROM metrics_samples WHERE collected_at < ?",
                (cutoff,),
            )
            await conn.commit()
            return cursor.rowcount or 0

    async def upsert_volumes(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute("SELECT id FROM volumes WHERE id=?", (item["id"],))
                is_new = await cursor.fetchone() is None
                await conn.execute(
                    """
                    INSERT INTO volumes(id, name, vol_type, state, size, wwn, appliance_id, raw_json, first_seen, last_seen)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, vol_type=excluded.vol_type, state=excluded.state,
                        size=excluded.size, wwn=excluded.wwn, appliance_id=excluded.appliance_id,
                        raw_json=excluded.raw_json, last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("name"), item.get("type"), item.get("state"),
                        item.get("size"), item.get("wwn"), item.get("appliance_id"),
                        json.dumps(item), now if is_new else now, now,
                    ),
                )
            await conn.commit()

    async def upsert_hosts(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute("SELECT id FROM hosts WHERE id=?", (item["id"],))
                is_new = await cursor.fetchone() is None
                await conn.execute(
                    """
                    INSERT INTO hosts(id, name, os_type, host_connectivity, host_group_id, description, raw_json, first_seen, last_seen)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, os_type=excluded.os_type, host_connectivity=excluded.host_connectivity,
                        host_group_id=excluded.host_group_id, description=excluded.description,
                        raw_json=excluded.raw_json, last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("name"), item.get("os_type"),
                        item.get("host_connectivity"), item.get("host_group_id"),
                        item.get("description"), json.dumps(item), now if is_new else now, now,
                    ),
                )
            await conn.commit()

    async def upsert_host_volume_maps(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute("SELECT id FROM host_volume_maps WHERE id=?", (item["id"],))
                is_new = await cursor.fetchone() is None
                await conn.execute(
                    """
                    INSERT INTO host_volume_maps(id, host_id, host_group_id, volume_id, logical_unit_number, raw_json, first_seen, last_seen)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        host_id=excluded.host_id, host_group_id=excluded.host_group_id,
                        volume_id=excluded.volume_id, logical_unit_number=excluded.logical_unit_number,
                        raw_json=excluded.raw_json, last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("host_id"), item.get("host_group_id"),
                        item.get("volume_id"), item.get("logical_unit_number"),
                        json.dumps(item), now if is_new else now, now,
                    ),
                )
            await conn.commit()

    async def upsert_nodes(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute("SELECT id FROM nodes WHERE id=?", (item["id"],))
                is_new = await cursor.fetchone() is None
                await conn.execute(
                    """
                    INSERT INTO nodes(id, slot, appliance_id, raw_json, first_seen, last_seen)
                    VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        slot=excluded.slot, appliance_id=excluded.appliance_id,
                        raw_json=excluded.raw_json, last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("slot"), item.get("appliance_id"),
                        json.dumps(item), now if is_new else now, now,
                    ),
                )
            await conn.commit()

    async def upsert_nas_servers(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute("SELECT id FROM nas_servers WHERE id=?", (item["id"],))
                is_new = await cursor.fetchone() is None
                await conn.execute(
                    """
                    INSERT INTO nas_servers(id, name, operational_status, current_node_id, preferred_node_id, raw_json, first_seen, last_seen)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, operational_status=excluded.operational_status,
                        current_node_id=excluded.current_node_id, preferred_node_id=excluded.preferred_node_id,
                        raw_json=excluded.raw_json, last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("name"), item.get("operational_status"),
                        item.get("current_node_id"), item.get("preferred_node_id"),
                        json.dumps(item), now if is_new else now, now,
                    ),
                )
            await conn.commit()

    async def upsert_file_systems(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute("SELECT id FROM file_systems WHERE id=?", (item["id"],))
                is_new = await cursor.fetchone() is None
                await conn.execute(
                    """
                    INSERT INTO file_systems(id, name, nas_server_id, filesystem_type, size_total, size_used, raw_json, first_seen, last_seen)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, nas_server_id=excluded.nas_server_id,
                        filesystem_type=excluded.filesystem_type, size_total=excluded.size_total,
                        size_used=excluded.size_used, raw_json=excluded.raw_json, last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("name"), item.get("nas_server_id"),
                        item.get("filesystem_type"), item.get("size_total"), item.get("size_used"),
                        json.dumps(item), now if is_new else now, now,
                    ),
                )
            await conn.commit()

    async def pin_volume(self, volume_id: str) -> None:
        async with self.session() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO pinned_volumes(volume_id, pinned_at) VALUES(?, ?)",
                (volume_id, utc_now()),
            )
            await conn.commit()

    async def list_pinned_volumes(self) -> list[str]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT volume_id FROM pinned_volumes")
            rows = await cursor.fetchall()
            return [row["volume_id"] for row in rows]

    async def list_volumes(self, primary_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM volumes"
        if primary_only:
            query += " WHERE vol_type='Primary'"
        query += " ORDER BY name"
        async with self.session() as conn:
            cursor = await conn.execute(query)
            return [dict(row) for row in await cursor.fetchall()]

    async def list_hosts(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT * FROM hosts ORDER BY name")
            return [dict(row) for row in await cursor.fetchall()]

    async def list_host_volume_maps(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT * FROM host_volume_maps")
            return [dict(row) for row in await cursor.fetchall()]

    async def list_nodes(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT * FROM nodes ORDER BY slot")
            return [dict(row) for row in await cursor.fetchall()]

    async def list_nas_servers(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT * FROM nas_servers ORDER BY name")
            return [dict(row) for row in await cursor.fetchall()]

    async def list_file_systems(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT * FROM file_systems ORDER BY name")
            return [dict(row) for row in await cursor.fetchall()]

    async def get_latest_metric(
        self,
        entity: str,
        entity_id: str | None = None,
        metric_type: str | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM metrics_samples WHERE entity=?"
        params: list[Any] = [entity]
        if entity_id:
            query += " AND entity_id=?"
            params.append(entity_id)
        if metric_type:
            query += " AND metric_type=?"
            params.append(metric_type)
        query += " ORDER BY collected_at DESC LIMIT 1"
        async with self.session() as conn:
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            return item

    async def list_metrics_series(
        self,
        *,
        entity: str,
        entity_id: str | None = None,
        metric_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = limit or settings.chart_max_points
        query = "SELECT * FROM metrics_samples WHERE entity=?"
        params: list[Any] = [entity]
        if entity_id:
            query += " AND entity_id=?"
            params.append(entity_id)
        if metric_type:
            query += " AND metric_type=?"
            params.append(metric_type)
        query += " ORDER BY collected_at DESC LIMIT ?"
        params.append(limit)
        async with self.session() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                result.append(item)
            result.reverse()
            return result

    async def list_metrics_recent(
        self,
        entity: str,
        *,
        metric_type: str | None = None,
        minutes: int = 5,
    ) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        query = "SELECT * FROM metrics_samples WHERE entity=? AND collected_at >= ?"
        params: list[Any] = [entity, cutoff]
        if metric_type:
            query += " AND metric_type=?"
            params.append(metric_type)
        query += " ORDER BY collected_at ASC"
        async with self.session() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    async def top_io_by_entity(
        self,
        entity: str,
        *,
        sort_key: str = "total_iops",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = limit or settings.io_rank_top_n
        async with self.session() as conn:
            cursor = await conn.execute(
                """
                SELECT m.entity_id, m.payload_json, m.collected_at, m.timestamp
                FROM metrics_samples m
                INNER JOIN (
                    SELECT entity_id, MAX(collected_at) AS max_collected
                    FROM metrics_samples
                    WHERE entity=?
                    GROUP BY entity_id
                ) latest ON m.entity_id=latest.entity_id AND m.collected_at=latest.max_collected
                WHERE m.entity=?
                """,
                (entity, entity),
            )
            rows = await cursor.fetchall()
        ranked: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            sort_value = payload.get(sort_key)
            if sort_value is None and sort_key == "total_iops":
                sort_value = payload.get("avg_total_iops")
            ranked.append({
                "entity_id": row["entity_id"],
                "collected_at": row["collected_at"],
                "timestamp": row["timestamp"],
                "payload": payload,
                "sort_value": float(sort_value or 0),
            })
        ranked.sort(key=lambda x: x["sort_value"], reverse=True)
        return ranked[:limit]

    async def storage_overview(self) -> dict[str, Any]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT COUNT(*) AS c FROM volumes WHERE vol_type='Primary'")
            volume_count = (await cursor.fetchone())["c"]
            cursor = await conn.execute("SELECT COUNT(*) AS c FROM hosts")
            host_count = (await cursor.fetchone())["c"]
            cursor = await conn.execute("SELECT COUNT(*) AS c FROM host_volume_maps")
            map_count = (await cursor.fetchone())["c"]
        space = await self.get_latest_metric("space_metrics_by_cluster", metric_type="space")
        return {
            "volume_count": volume_count,
            "host_count": host_count,
            "mapping_count": map_count,
            "space": space,
        }

    async def upsert_audit_events(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                cursor = await conn.execute(
                    "SELECT id FROM audit_events WHERE id=?",
                    (item["id"],),
                )
                is_new = await cursor.fetchone() is None
                await conn.execute(
                    """
                    INSERT INTO audit_events(
                        id, event_type, timestamp, username, is_successful, client_address,
                        resource_type, resource_action, message, appliance_id, raw_json,
                        first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        event_type=excluded.event_type,
                        timestamp=excluded.timestamp,
                        username=excluded.username,
                        is_successful=excluded.is_successful,
                        client_address=excluded.client_address,
                        resource_type=excluded.resource_type,
                        resource_action=excluded.resource_action,
                        message=excluded.message,
                        appliance_id=excluded.appliance_id,
                        raw_json=excluded.raw_json,
                        last_seen=excluded.last_seen
                    """,
                    (
                        item["id"],
                        item.get("type"),
                        item.get("timestamp"),
                        item.get("username"),
                        int(item["is_successful"]) if item.get("is_successful") is not None else None,
                        item.get("client_address"),
                        item.get("resource_type"),
                        item.get("resource_action"),
                        item.get("message_l10n"),
                        item.get("appliance_id"),
                        json.dumps(item),
                        now if is_new else now,
                        now,
                    ),
                )
            await conn.commit()

    async def mark_notified(self, alert_id: str) -> None:
        async with self.session() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO notified_alerts(alert_id, notified_at) VALUES(?, ?)",
                (alert_id, utc_now()),
            )
            await conn.commit()

    async def is_notified(self, alert_id: str) -> bool:
        async with self.session() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM notified_alerts WHERE alert_id=?",
                (alert_id,),
            )
            return await cursor.fetchone() is not None

    async def list_alerts(
        self,
        *,
        severity: str | None = None,
        state: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM alerts WHERE 1=1"
        params: list[Any] = []
        if severity:
            query += " AND severity=?"
            params.append(severity)
        if state:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY generated_timestamp DESC LIMIT ?"
        params.append(limit)
        async with self.session() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def list_events(self, *, severity: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        query = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if severity:
            query += " AND severity=?"
            params.append(severity)
        query += " ORDER BY generated_timestamp DESC LIMIT ?"
        params.append(limit)
        async with self.session() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def list_hardware(self, *, unhealthy_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM hardware"
        if unhealthy_only:
            query += " WHERE lifecycle_state IS NOT NULL AND lifecycle_state != 'Healthy'"
        query += " ORDER BY hw_type, name"
        async with self.session() as conn:
            cursor = await conn.execute(query)
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item.get("extra_json"):
                    try:
                        item["extra"] = json.loads(item["extra_json"])
                    except json.JSONDecodeError:
                        item["extra"] = {}
                else:
                    item["extra"] = {}
                result.append(item)
            return result

    async def list_ports(self, port_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ports"
        params: list[Any] = []
        if port_type:
            query += " WHERE port_type=?"
            params.append(port_type)
        query += " ORDER BY port_type, name"
        async with self.session() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def list_audit_events(self, limit: int = 500) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute(
                "SELECT * FROM audit_events ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def list_metrics(
        self,
        *,
        entity: str | None = None,
        entity_id: str | None = None,
        metric_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM metrics_samples WHERE 1=1"
        params: list[Any] = []
        if entity:
            query += " AND entity=?"
            params.append(entity)
        if entity_id:
            query += " AND entity_id=?"
            params.append(entity_id)
        if metric_type:
            query += " AND metric_type=?"
            params.append(metric_type)
        query += " ORDER BY collected_at DESC LIMIT ?"
        params.append(limit)
        async with self.session() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                result.append(item)
            return result

    async def overview_stats(self) -> dict[str, Any]:
        async with self.session() as conn:
            severity_counts = {}
            cursor = await conn.execute(
                "SELECT severity, COUNT(*) AS count FROM alerts WHERE state='ACTIVE' GROUP BY severity"
            )
            for row in await cursor.fetchall():
                severity_counts[row["severity"]] = row["count"]

            cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM hardware WHERE lifecycle_state != 'Healthy'"
            )
            unhealthy_hardware = (await cursor.fetchone())["count"]

            cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM ports WHERE is_link_up=0"
            )
            down_ports = (await cursor.fetchone())["count"]

            cursor = await conn.execute(
                """
                SELECT * FROM alerts
                WHERE severity IN ('Critical', 'Major') AND state='ACTIVE'
                ORDER BY generated_timestamp DESC LIMIT 10
                """
            )
            recent_alerts = [dict(row) for row in await cursor.fetchall()]

            cursor = await conn.execute(
                """
                SELECT DATE(first_seen) AS day, severity, COUNT(*) AS count
                FROM alerts
                WHERE severity IN ('Critical', 'Major')
                GROUP BY day, severity
                ORDER BY day DESC
                LIMIT 14
                """
            )
            trend = [dict(row) for row in await cursor.fetchall()]

            cluster_perf = await self.get_latest_metric(
                "performance_metrics_by_cluster", metric_type="performance"
            )
            cluster_space = await self.get_latest_metric(
                "space_metrics_by_cluster", metric_type="space"
            )

            return {
                "severity_counts": severity_counts,
                "unhealthy_hardware": unhealthy_hardware,
                "down_ports": down_ports,
                "recent_alerts": recent_alerts,
                "trend": trend,
                "cluster_perf": cluster_perf,
                "cluster_space": cluster_space,
            }

    async def upsert_cluster_info(self, item: dict[str, Any]) -> None:
        now = utc_now()
        async with self.session() as conn:
            await conn.execute(
                """
                INSERT INTO cluster_info(
                    id, name, global_id, management_address, appliance_count, state,
                    is_encryption_enabled, system_time, raw_json, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    global_id=excluded.global_id,
                    management_address=excluded.management_address,
                    appliance_count=excluded.appliance_count,
                    state=excluded.state,
                    is_encryption_enabled=excluded.is_encryption_enabled,
                    system_time=excluded.system_time,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    item["id"],
                    item.get("name"),
                    item.get("global_id"),
                    item.get("management_address"),
                    item.get("appliance_count"),
                    item.get("state"),
                    1 if item.get("is_encryption_enabled") else 0,
                    item.get("system_time"),
                    json.dumps(item),
                    now,
                ),
            )
            await conn.commit()

    async def get_cluster_info(self) -> dict[str, Any] | None:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT * FROM cluster_info LIMIT 1")
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def upsert_appliances(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                sw = item.get("software_installed") or []
                sw0 = sw[0] if isinstance(sw, list) and sw else {}
                if isinstance(sw0, dict) and not sw0.get("release_version") and len(sw) > 1:
                    sw0 = sw[0]
                await conn.execute(
                    """
                    INSERT INTO appliances(
                        id, name, service_tag, model, node_count,
                        drive_failure_tolerance_level, storage_class,
                        release_version, installed_date, raw_json, first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        service_tag=excluded.service_tag,
                        model=excluded.model,
                        node_count=excluded.node_count,
                        drive_failure_tolerance_level=excluded.drive_failure_tolerance_level,
                        storage_class=excluded.storage_class,
                        release_version=excluded.release_version,
                        installed_date=excluded.installed_date,
                        raw_json=excluded.raw_json,
                        last_seen=excluded.last_seen
                    """,
                    (
                        item["id"],
                        item.get("name"),
                        item.get("service_tag"),
                        item.get("model"),
                        item.get("node_count"),
                        item.get("drive_failure_tolerance_level"),
                        item.get("storage_class"),
                        sw0.get("release_version") if isinstance(sw0, dict) else None,
                        sw0.get("installed_date") if isinstance(sw0, dict) else None,
                        json.dumps(item),
                        now,
                        now,
                    ),
                )
            await conn.commit()

    async def list_appliances(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT * FROM appliances ORDER BY name")
            return [dict(row) for row in await cursor.fetchall()]

    async def upsert_replication_sessions(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                await conn.execute(
                    """
                    INSERT INTO replication_sessions(
                        id, state, role, resource_type, session_type, last_sync_timestamp,
                        local_resource_id, remote_resource_id, remote_system_id,
                        progress_percentage, replication_rule_id, raw_json, first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        state=excluded.state, role=excluded.role,
                        resource_type=excluded.resource_type, session_type=excluded.session_type,
                        last_sync_timestamp=excluded.last_sync_timestamp,
                        local_resource_id=excluded.local_resource_id,
                        remote_resource_id=excluded.remote_resource_id,
                        remote_system_id=excluded.remote_system_id,
                        progress_percentage=excluded.progress_percentage,
                        replication_rule_id=excluded.replication_rule_id,
                        raw_json=excluded.raw_json, last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("state"), item.get("role"),
                        item.get("resource_type"), item.get("type"),
                        item.get("last_sync_timestamp"), item.get("local_resource_id"),
                        item.get("remote_resource_id"), item.get("remote_system_id"),
                        item.get("progress_percentage"), item.get("replication_rule_id"),
                        json.dumps(item), now, now,
                    ),
                )
            await conn.commit()

    async def list_replication_sessions(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute(
                "SELECT * FROM replication_sessions ORDER BY last_sync_timestamp DESC"
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def upsert_remote_systems(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                await conn.execute(
                    """
                    INSERT INTO remote_systems(
                        id, name, management_address, system_type, state,
                        data_connection_state, version, serial_number, raw_json,
                        first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, management_address=excluded.management_address,
                        system_type=excluded.system_type, state=excluded.state,
                        data_connection_state=excluded.data_connection_state,
                        version=excluded.version, serial_number=excluded.serial_number,
                        raw_json=excluded.raw_json, last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("name"), item.get("management_address"),
                        item.get("type"), item.get("state"), item.get("data_connection_state"),
                        item.get("version"), item.get("serial_number"), json.dumps(item),
                        now, now,
                    ),
                )
            await conn.commit()

    async def list_remote_systems(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT * FROM remote_systems ORDER BY name")
            return [dict(row) for row in await cursor.fetchall()]

    async def upsert_protection_policies(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                await conn.execute(
                    """
                    INSERT INTO protection_policies(
                        id, name, description, policy_type, is_replica, is_read_only,
                        raw_json, first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, description=excluded.description,
                        policy_type=excluded.policy_type, is_replica=excluded.is_replica,
                        is_read_only=excluded.is_read_only, raw_json=excluded.raw_json,
                        last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("name"), item.get("description"),
                        item.get("type"), 1 if item.get("is_replica") else 0,
                        1 if item.get("is_read_only") else 0, json.dumps(item), now, now,
                    ),
                )
            await conn.commit()

    async def list_protection_policies(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute(
                "SELECT * FROM protection_policies ORDER BY policy_type, name"
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def upsert_snapshot_rules(self, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        async with self.session() as conn:
            for item in items:
                days = item.get("days_of_week")
                policy_id = item.get("policy_id")
                if policy_id is None:
                    policies = item.get("policies") or []
                    if policies and isinstance(policies[0], dict):
                        policy_id = policies[0].get("id")
                await conn.execute(
                    """
                    INSERT INTO snapshot_rules(
                        id, name, interval, time_of_day, days_of_week, policy_id,
                        raw_json, first_seen, last_seen
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, interval=excluded.interval,
                        time_of_day=excluded.time_of_day, days_of_week=excluded.days_of_week,
                        policy_id=excluded.policy_id, raw_json=excluded.raw_json,
                        last_seen=excluded.last_seen
                    """,
                    (
                        item["id"], item.get("name"), item.get("interval"),
                        item.get("time_of_day"),
                        json.dumps(days) if days is not None else None,
                        policy_id, json.dumps(item), now, now,
                    ),
                )
            await conn.commit()

    async def list_snapshot_rules(self) -> list[dict[str, Any]]:
        async with self.session() as conn:
            cursor = await conn.execute("SELECT * FROM snapshot_rules ORDER BY name")
            return [dict(row) for row in await cursor.fetchall()]

    _HEALTH_HW_TYPES = ("Fan", "Power_Supply", "Drive", "Battery", "Node")

    @staticmethod
    def _health_bucket(lifecycle_state: str | None) -> str:
        if not lifecycle_state or lifecycle_state == "Empty":
            return "unknown"
        if lifecycle_state == "Healthy":
            return "ok"
        if lifecycle_state in ("Failed", "Disconnected"):
            return "failed"
        return "degraded"

    async def hardware_health_summary(self) -> dict[str, dict[str, int]]:
        """Aggregate hardware counts by type and health bucket (Netdata-style)."""
        summary: dict[str, dict[str, int]] = {
            t: {"ok": 0, "degraded": 0, "failed": 0, "unknown": 0, "total": 0}
            for t in self._HEALTH_HW_TYPES
        }
        async with self.session() as conn:
            cursor = await conn.execute(
                "SELECT hw_type, lifecycle_state FROM hardware WHERE hw_type IS NOT NULL"
            )
            rows = await cursor.fetchall()
        for row in rows:
            hw_type = row["hw_type"]
            if hw_type not in summary:
                continue
            bucket = self._health_bucket(row["lifecycle_state"])
            summary[hw_type][bucket] += 1
            summary[hw_type]["total"] += 1
        return summary

    async def get_latest_metrics_by_entity(self, entity: str) -> list[dict[str, Any]]:
        async with self.session() as conn:
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
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result
