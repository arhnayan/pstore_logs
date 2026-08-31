"""NAS servers and file systems API."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import db

router = APIRouter(prefix="/api/nas", tags=["nas"])


@router.get("")
async def get_nas() -> dict:
    nas_servers = await db.list_nas_servers()
    file_systems = await db.list_file_systems()
    return {
        "nas_servers": nas_servers,
        "file_systems": file_systems,
        "count": len(nas_servers) + len(file_systems),
    }
