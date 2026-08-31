"""Application configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.paths import user_data_dir, user_downloads_dir


@dataclass(frozen=True)
class Settings:
    cluster_ip: str = "192.168.1.40"
    host: str = "127.0.0.1"
    port: int = 8080

    poll_alerts_sec: int = 5
    poll_events_sec: int = 15
    poll_hardware_sec: int = 60
    poll_perf_fast_sec: int = 20
    poll_space_sec: int = 60
    poll_inventory_sec: int = 60
    poll_io_rank_sec: int = 60
    poll_wear_sec: int = 300
    poll_audit_sec: int = 60
    poll_cluster_info_sec: int = 120
    poll_protection_sec: int = 120
    poll_port_perf_sec: int = 60
    poll_object_space_sec: int = 120

    page_limit: int = 100
    event_fetch_limit: int = 200
    io_rank_volume_cap: int = 50
    io_rank_host_cap: int = 50
    io_rank_top_n: int = 15
    port_perf_cap: int = 20
    space_volume_cap: int = 50
    metrics_retention_hours: int = 24
    chart_max_points: int = 180

    keyring_service: str = "pstore-monitor"

    @property
    def base_url(self) -> str:
        return f"https://{self.cluster_ip}/api/rest"

    @property
    def data_dir(self) -> Path:
        return user_data_dir()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pstore.db"

    def ensure_data_dir(self) -> Path:
        path = self.data_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def downloads_dir(self) -> Path:
        path = user_downloads_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
