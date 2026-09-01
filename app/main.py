"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.deps import collector, db
from app.paths import static_dir
from app.routers import (
    alerts,
    audit,
    capacity,
    cluster,
    datacollection,
    events,
    hardware,
    metrics,
    nas,
    overview,
    protection,
    reports,
    resources,
    settings,
    storage,
    stream,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = static_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.set_status("app_state", "starting")
    await collector.start()
    await db.set_status("app_state", "running")
    logger.info("PowerStore monitor started")
    yield
    await db.set_status("app_state", "stopping")
    await collector.stop()
    await db.set_status("app_state", "stopped")
    logger.info("PowerStore monitor stopped")


app = FastAPI(
    title="PowerStore Local Monitor",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(overview.router)
app.include_router(cluster.router)
app.include_router(protection.router)
app.include_router(resources.router)
app.include_router(alerts.router)
app.include_router(events.router)
app.include_router(hardware.router)
app.include_router(metrics.router)
app.include_router(capacity.router)
app.include_router(storage.router)
app.include_router(nas.router)
app.include_router(audit.router)
app.include_router(datacollection.router)
app.include_router(stream.router)
app.include_router(settings.router)
app.include_router(reports.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
