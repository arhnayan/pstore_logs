"""Async PowerStore REST API client with paging and session management."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import settings


class PowerStoreError(Exception):
    """Base error for PowerStore API failures."""


class PowerStoreAuthError(PowerStoreError):
    """Authentication or authorization failure."""


class PowerStoreConflictError(PowerStoreError):
    """Operation conflict (e.g. datacollection already running)."""


def parse_error_response(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return resp.text or resp.reason_phrase or f"HTTP {resp.status_code}"
    messages = data.get("messages") or []
    parts = []
    for msg in messages:
        if isinstance(msg, dict):
            text = msg.get("message_l10n") or msg.get("code") or str(msg)
            parts.append(text)
        else:
            parts.append(str(msg))
    if parts:
        return "; ".join(parts)
    if isinstance(data.get("detail"), str):
        return data["detail"]
    return resp.text or f"HTTP {resp.status_code}"


class PowerStoreClient:
    def __init__(
        self,
        cluster_ip: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = False,
    ) -> None:
        self.cluster_ip = cluster_ip or settings.cluster_ip
        self.base_url = f"https://{self.cluster_ip}/api/rest"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._client: httpx.AsyncClient | None = None
        self._csrf_token: str | None = None

    async def __aenter__(self) -> PowerStoreClient:
        await self.open()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=self.verify_ssl,
                timeout=httpx.Timeout(30.0, connect=10.0),
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._csrf_token = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise PowerStoreError("Client is not open")
        return self._client

    async def login(self, username: str | None = None, password: str | None = None) -> None:
        user = username or self.username
        pwd = password or self.password
        if not user or not pwd:
            raise PowerStoreAuthError("Username and password are required")

        client = self._require_client()
        resp = await client.get(
            f"{self.base_url}/login_session",
            auth=(user, pwd),
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 401:
            raise PowerStoreAuthError("Invalid credentials")
        resp.raise_for_status()

        self.username = user
        self.password = pwd
        self._csrf_token = resp.headers.get("DELL-EMC-TOKEN")

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self._csrf_token:
            headers["DELL-EMC-TOKEN"] = self._csrf_token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> httpx.Response:
        client = self._require_client()
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        resp = await client.request(
            method,
            url,
            params=params,
            json=json,
            headers=self._headers(json_body=json is not None),
        )

        if resp.status_code == 401 and retry_auth and self.username and self.password:
            await self.login()
            resp = await client.request(
                method,
                url,
                params=params,
                json=json,
                headers=self._headers(json_body=json is not None),
                auth=None,
            )

        return resp

    async def _fetch_paged(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("limit", settings.page_limit)
        offset = int(params.get("offset", 0))
        results: list[dict[str, Any]] = []

        while True:
            params["offset"] = offset
            resp = await self._request("GET", path, params=params)
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list):
                raise PowerStoreError(f"Expected list from {path}, got {type(batch).__name__}")
            results.extend(batch)
            if resp.status_code != 206 or len(batch) < int(params["limit"]):
                break
            offset += int(params["limit"])

        return results

    async def get_alerts(self, severity_filter: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "select": (
                "id,event_code,severity,resource_type,resource_id,resource_name,"
                "description_l10n,generated_timestamp,raised_timestamp,state,"
                "state_l10n,is_acknowledged,severity_l10n"
            ),
        }
        if severity_filter:
            params["filter"] = f"severity=eq.{severity_filter}"
        return await self._fetch_paged("/alert", params=params)

    async def get_events(self, severity_filter: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "select": (
                "id,event_code,severity,resource_type,resource_id,resource_name,"
                "generated_timestamp,description_l10n,severity_l10n,resource_type_l10n"
            ),
            "order": "generated_timestamp.desc",
            "limit": settings.event_fetch_limit,
        }
        if severity_filter:
            params["filter"] = f"severity=eq.{severity_filter}"
        return await self._fetch_paged("/event", params=params)

    async def get_hardware(self) -> list[dict[str, Any]]:
        params = {
            "select": (
                "id,name,type,type_l10n,lifecycle_state,lifecycle_state_l10n,"
                "appliance_id,slot,part_number,serial_number,status_led_state,"
                "extra_details"
            ),
        }
        return await self._fetch_paged("/hardware", params=params)

    async def get_appliances(self) -> list[dict[str, Any]]:
        params = {"select": "id,name,service_tag,model"}
        return await self._fetch_paged("/appliance", params=params)

    async def get_appliances_detail(self) -> list[dict[str, Any]]:
        params = {
            "select": (
                "id,name,service_tag,model,node_count,drive_failure_tolerance_level,"
                "storage_class,software_installed(release_version,installed_date,build_version)"
            ),
        }
        return await self._fetch_paged("/appliance", params=params)

    async def get_cluster_detail(self) -> list[dict[str, Any]]:
        params = {
            "select": (
                "id,name,global_id,management_address,appliance_count,state,"
                "is_encryption_enabled,system_time,primary_appliance_id,physical_mtu"
            ),
        }
        resp = await self._request("GET", "/cluster", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else [data]

    async def get_replication_sessions(self) -> list[dict[str, Any]]:
        params = {
            "select": (
                "id,state,role,resource_type,type,last_sync_timestamp,"
                "local_resource_id,remote_resource_id,remote_system_id,"
                "progress_percentage,replication_rule_id"
            ),
        }
        return await self._fetch_paged("/replication_session", params=params)

    async def get_remote_systems(self) -> list[dict[str, Any]]:
        params = {
            "select": (
                "id,name,management_address,type,state,data_connection_state,version,serial_number"
            ),
        }
        return await self._fetch_paged("/remote_system", params=params)

    async def get_policies(self, policy_type: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "select": "id,name,description,type,is_replica,is_read_only",
        }
        if policy_type:
            params["type"] = f"eq.{policy_type}"
        return await self._fetch_paged("/policy", params=params)

    async def get_snapshot_rules(self) -> list[dict[str, Any]]:
        params = {
            "select": "id,name,interval,time_of_day,days_of_week,policies",
        }
        return await self._fetch_paged("/snapshot_rule", params=params)

    async def get_volume_groups(self) -> list[dict[str, Any]]:
        params = {"select": "id,name,description,volume_count"}
        return await self._fetch_paged("/volume_group", params=params)

    async def get_fc_ports(self) -> list[dict[str, Any]]:
        params = {
            "select": (
                "id,name,appliance_id,node_id,wwn,is_link_up,is_in_use,"
                "current_speed,current_speed_l10n"
            ),
        }
        return await self._fetch_paged("/fc_port", params=params)

    async def get_eth_ports(self) -> list[dict[str, Any]]:
        params = {
            "select": (
                "id,name,appliance_id,node_id,mac_address,is_link_up,is_in_use,"
                "current_speed,current_mtu,stale_state"
            ),
        }
        return await self._fetch_paged("/eth_port", params=params)

    async def get_audit_events(self) -> list[dict[str, Any]]:
        params = {
            "select": (
                "id,type,timestamp,username,is_successful,client_address,"
                "resource_type,resource_action,message_l10n,appliance_id"
            ),
            "order": "timestamp.desc",
            "limit": settings.event_fetch_limit,
        }
        params_copy = dict(params)
        params_copy.setdefault("limit", settings.page_limit)
        offset = 0
        results: list[dict[str, Any]] = []
        while True:
            params_copy["offset"] = offset
            resp = await self._request("GET", "/audit_event", params=params_copy)
            if resp.status_code == 403:
                raise PowerStoreAuthError("Insufficient permissions for audit events")
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list):
                raise PowerStoreError("Expected list from /audit_event")
            results.extend(batch)
            if resp.status_code != 206 or len(batch) < int(params_copy["limit"]):
                break
            offset += int(params_copy["limit"])
        return results

    async def get_cluster(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", "/cluster")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else [data]

    async def acknowledge_alert(self, alert_id: str) -> None:
        resp = await self._request(
            "PATCH",
            f"/alert/{alert_id}",
            json={"is_acknowledged": True},
        )
        if resp.status_code == 403:
            raise PowerStoreAuthError("Insufficient permissions to acknowledge alert")
        resp.raise_for_status()

    async def generate_metrics(
        self,
        entity: str,
        entity_id: str,
        interval: str = "Five_Mins",
    ) -> list[dict[str, Any]]:
        resp = await self._request(
            "POST",
            "/metrics/generate",
            json={"entity": entity, "entity_id": entity_id, "interval": interval},
        )
        if resp.status_code in (400, 422):
            return []
        resp.raise_for_status()
        if resp.status_code == 204:
            return []
        data = resp.json()
        return data if isinstance(data, list) else [data]

    async def generate_metrics_with_fallback(
        self,
        entity: str,
        entity_id: str,
        primary_interval: str = "Twenty_Sec",
        fallback_interval: str = "Five_Mins",
    ) -> list[dict[str, Any]]:
        samples = await self.generate_metrics(entity, entity_id, primary_interval)
        if samples:
            return samples
        return await self.generate_metrics(entity, entity_id, fallback_interval)

    async def get_nodes(self) -> list[dict[str, Any]]:
        params = {"select": "id,slot,appliance_id"}
        return await self._fetch_paged("/node", params=params)

    async def get_volumes(self, primary_only: bool = False) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "select": "id,name,type,state,size,wwn,appliance_id,nsid",
        }
        volumes = await self._fetch_paged("/volume", params=params)
        if primary_only:
            volumes = [v for v in volumes if v.get("type") == "Primary"]
        return volumes

    async def get_hosts(self) -> list[dict[str, Any]]:
        params = {
            "select": "id,name,os_type,host_connectivity,host_group_id,description",
        }
        return await self._fetch_paged("/host", params=params)

    async def get_host_volume_mappings(self) -> list[dict[str, Any]]:
        params = {
            "select": "id,host_id,host_group_id,volume_id,logical_unit_number",
        }
        return await self._fetch_paged("/host_volume_mapping", params=params)

    async def get_nas_servers(self) -> list[dict[str, Any]]:
        params = {
            "select": "id,name,operational_status,current_node_id,preferred_node_id",
        }
        return await self._fetch_paged("/nas_server", params=params)

    async def get_file_systems(self) -> list[dict[str, Any]]:
        params = {
            "select": "id,name,nas_server_id,filesystem_type,size_total,size_used",
        }
        return await self._fetch_paged("/file_system", params=params)

    async def enable_fast_metrics(self, volume_id: str) -> None:
        resp = await self._request(
            "POST",
            "/fast_metrics_config",
            json={"resource_id": volume_id, "resource_type": "volume"},
        )
        if resp.status_code in (400, 409, 422):
            return
        resp.raise_for_status()

    async def create_datacollection(self, description: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"description": description or "PowerStore Monitor log bundle"}
        resp = await self._request("POST", "/datacollection", json=body)
        if resp.status_code == 409:
            raise PowerStoreConflictError(parse_error_response(resp))
        if resp.status_code == 403:
            raise PowerStoreAuthError(parse_error_response(resp))
        resp.raise_for_status()
        return resp.json()

    async def get_datacollection(self, collection_id: str) -> dict[str, Any]:
        params = {
            "select": (
                "id,status,status_message,start_timestamp,end_timestamp,description,"
                "compressed_size,uncompressed_size,appliances"
            ),
        }
        resp = await self._request("GET", f"/datacollection/{collection_id}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def list_datacollections(self, limit: int = 25) -> list[dict[str, Any]]:
        params = {
            "select": (
                "id,status,status_message,start_timestamp,end_timestamp,description,"
                "compressed_size,appliances"
            ),
            "order": "start_timestamp.desc",
            "limit": limit,
        }
        resp = await self._request("GET", "/datacollection", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else [data]

    async def download_binary(self, path: str) -> bytes:
        chunks: list[bytes] = []
        async for chunk in self.stream_binary(path):
            chunks.append(chunk)
        return b"".join(chunks)

    async def stream_binary(self, path: str, chunk_size: int = 1024 * 1024):
        """Stream a large binary download from PowerStore (log bundles, etc.)."""
        client = self._require_client()
        url = path if path.startswith("http") else f"https://{self.cluster_ip}{path}"
        timeout = httpx.Timeout(60.0, connect=15.0, read=3600.0, write=60.0, pool=60.0)
        headers = self._headers()
        async with client.stream("GET", url, headers=headers, timeout=timeout) as resp:
            if resp.status_code == 401 and self.username and self.password:
                await self.login()
                headers = self._headers()
                await resp.aclose()
                async with client.stream("GET", url, headers=headers, timeout=timeout) as retry:
                    retry.raise_for_status()
                    async for chunk in retry.aiter_bytes(chunk_size):
                        yield chunk
                return
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size):
                yield chunk

    async def get_job(self, job_id: str) -> dict[str, Any]:
        resp = await self._request("GET", f"/job/{job_id}")
        resp.raise_for_status()
        return resp.json()


class SyncPowerStoreClient:
    """Synchronous wrapper for CLI usage."""

    def __init__(self, **kwargs: Any) -> None:
        self._async = PowerStoreClient(**kwargs)
        self._loop: asyncio.AbstractEventLoop | None = None

    def __enter__(self) -> SyncPowerStoreClient:
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._async.open())
        return self

    def __exit__(self, *args: Any) -> None:
        if self._loop:
            self._loop.run_until_complete(self._async.close())
            self._loop.close()
            self._loop = None

    def _run(self, coro: Any) -> Any:
        if not self._loop:
            raise PowerStoreError("Client is not open")
        return self._loop.run_until_complete(coro)

    def login(self, username: str, password: str) -> None:
        self._run(self._async.login(username, password))

    def get_alerts(self, severity_filter: str | None = None) -> list[dict[str, Any]]:
        return self._run(self._async.get_alerts(severity_filter))

    def get_events(self, severity_filter: str | None = None) -> list[dict[str, Any]]:
        return self._run(self._async.get_events(severity_filter))

    def get_hardware(self) -> list[dict[str, Any]]:
        return self._run(self._async.get_hardware())
