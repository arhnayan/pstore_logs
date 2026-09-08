"""Report generation API."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.credentials import get_credentials, has_credentials
from app.deps import db, event_bus
from app.locations import ensure_locations, location_has_ips
from app.report_collector import ReportCollector

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports", tags=["reports"])

_job_state: dict = {
    "running": False,
    "progress": "",
    "location_status": {},
    "error": None,
    "output_file": None,
    "filename": None,
    "report_type": None,
    "servers_with_data": 0,
}

ALLOWED_REPORT_FILES = {
    "All_Locations_Storage_Report.xlsx",
    "All_TMPs.xlsx",
}


class LocationUpdate(BaseModel):
    name: str
    cluster_ip: str = ""
    servers: list[str] = Field(default_factory=list)
    server_ips: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    sort_order: int = 0


class LocationsPayload(BaseModel):
    locations: list[LocationUpdate]


class GeneratePayload(BaseModel):
    days: int = Field(default=30, ge=30, le=30)


def _start_report_job(report_type: str) -> None:
    _set_job(
        running=True,
        progress="Starting report generation…",
        location_status={},
        error=None,
        output_file=None,
        filename=None,
        report_type=report_type,
        servers_with_data=0,
    )


async def _schedule_report_job(report_type: str, runner) -> dict:
    if _job_state.get("running"):
        raise HTTPException(status_code=409, detail="Report generation already in progress")

    _start_report_job(report_type)
    await event_bus.publish("report", dict(_job_state))

    async def run_job() -> None:
        collector = ReportCollector(db)

        def on_progress(location: str, data: dict) -> None:
            if location:
                _job_state.setdefault("location_status", {})[location] = data
            phase = data.get("phase")
            if phase == "host":
                _job_state["progress"] = (
                    f"{location}: fetching {data.get('server')} "
                    f"({data.get('current')}/{data.get('total')})"
                )
            elif phase == "location_start":
                _job_state["progress"] = f"Connecting to {location}…"
            elif phase == "location_done":
                if data.get("error"):
                    _job_state["progress"] = f"{location} failed: {data['error']}"
                else:
                    _job_state["progress"] = f"Finished {location}"
            elif phase == "generating":
                _job_state["progress"] = "Generating summary Excel report…"
            elif phase == "generating_hourly":
                _job_state["progress"] = "Generating hourly TMP Excel report…"
            asyncio.create_task(event_bus.publish("report", dict(_job_state)))

        try:
            result = await runner(collector, on_progress)
            _set_job(
                running=False,
                progress="Report ready",
                output_file=result["output_file"],
                filename=result["filename"],
                report_type=result.get("report_type", report_type),
                error=None,
                servers_with_data=result["servers_with_data"],
            )
        except Exception as exc:
            logger.exception("Report generation failed")
            _set_job(running=False, progress="Failed", error=str(exc))
        finally:
            await event_bus.publish("report", dict(_job_state))

    asyncio.create_task(run_job())
    return {"ok": True, "started": True, "report_type": report_type}


def _set_job(**kwargs) -> None:
    _job_state.update(kwargs)


@router.get("/locations")
async def list_locations() -> dict:
    locations = await ensure_locations(db)
    return {"locations": locations}


@router.put("/locations")
async def update_locations(payload: LocationsPayload) -> dict:
    items = [loc.model_dump() for loc in payload.locations]
    await db.upsert_report_locations(items)
    return {"ok": True, "locations": await db.list_report_locations()}


@router.get("/status")
async def report_status() -> dict:
    return dict(_job_state)


@router.post("/generate")
async def generate_report(payload: GeneratePayload | None = None) -> dict:
    request = payload or GeneratePayload()
    if not await has_credentials():
        raise HTTPException(status_code=400, detail="Configure credentials in Settings first")

    creds = await get_credentials()
    if not creds:
        raise HTTPException(status_code=400, detail="Configure credentials in Settings first")
    username, password = creds

    locations = await ensure_locations(db)
    enabled = [loc for loc in locations if loc.get("enabled", True)]
    if not any(location_has_ips(loc) for loc in enabled):
        raise HTTPException(status_code=400, detail="No locations have server MGMT IPs configured")

    async def runner(collector: ReportCollector, on_progress) -> dict:
        return await collector.generate_combined_report(
            locations,
            username,
            password,
            days=request.days,
            on_progress=on_progress,
        )

    return await _schedule_report_job("summary", runner)


@router.post("/generate-hourly")
async def generate_hourly_report(payload: GeneratePayload | None = None) -> dict:
    request = payload or GeneratePayload()
    if not await has_credentials():
        raise HTTPException(status_code=400, detail="Configure credentials in Settings first")

    creds = await get_credentials()
    if not creds:
        raise HTTPException(status_code=400, detail="Configure credentials in Settings first")
    username, password = creds

    locations = await ensure_locations(db)
    enabled = [loc for loc in locations if loc.get("enabled", True)]
    if not any(location_has_ips(loc) for loc in enabled):
        raise HTTPException(status_code=400, detail="No locations have server MGMT IPs configured")

    async def runner(collector: ReportCollector, on_progress) -> dict:
        return await collector.generate_hourly_tmp_report(
            locations,
            username,
            password,
            days=request.days,
            on_progress=on_progress,
        )

    return await _schedule_report_job("hourly", runner)


@router.get("/download/{filename}")
async def download_report(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name not in ALLOWED_REPORT_FILES:
        raise HTTPException(status_code=404, detail="Report not found")
    path = settings.reports_dir / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=safe_name,
    )
