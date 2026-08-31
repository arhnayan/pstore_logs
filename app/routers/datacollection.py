"""Log bundle / datacollection API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.client import PowerStoreAuthError, PowerStoreConflictError, PowerStoreError
from app.deps import collector, db

router = APIRouter(prefix="/api/datacollection", tags=["datacollection"])


class CollectionCreate(BaseModel):
    description: str = "PowerStore Monitor log bundle"


@router.get("")
async def list_collections() -> dict:
    client = collector.get_client()
    try:
        items = await client.list_datacollections()
    except PowerStoreAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": items, "count": len(items)}


@router.post("")
async def create_collection(body: CollectionCreate | None = None) -> dict:
    client = collector.get_client()
    description = body.description if body else "PowerStore Monitor log bundle"
    try:
        result = await client.create_datacollection(description)
    except PowerStoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PowerStoreAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PowerStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "result": result}


@router.get("/{collection_id}")
async def get_collection(collection_id: str) -> dict:
    client = collector.get_client()
    try:
        item = await client.get_datacollection(collection_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return item


@router.post("/{collection_id}/download")
async def download_collection(collection_id: str) -> StreamingResponse:
    client = collector.get_client()
    try:
        item = await client.get_datacollection(collection_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    appliances = item.get("appliances") or []
    if not appliances:
        raise HTTPException(status_code=404, detail="No appliance bundles available yet")

    download_uri = None
    for appliance in appliances:
        if appliance.get("download_uri") and appliance.get("status") in ("Success", "SUCCESS"):
            download_uri = appliance["download_uri"]
            break

    if not download_uri:
        raise HTTPException(
            status_code=409,
            detail="Collection not ready for download — wait until status is SUCCESS",
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"pstore_datacollection_{collection_id[:8]}_{stamp}.zip"

    async def iter_chunks():
        async for chunk in client.stream_binary(download_uri):
            yield chunk

    await db.set_status("last_datacollection_download", filename)
    return StreamingResponse(
        iter_chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
