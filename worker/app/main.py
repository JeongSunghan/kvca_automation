from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .kvca_client import KVCAClient
from .storage import Storage, create_storage
from .sync_service import EnrolmentSyncService, summary_to_dict


class SyncRequest(BaseModel):
    category_id: int = Field(description="실행 시 입력할 category_id")
    max_categories: int | None = Field(default=1, ge=1, le=100)
    max_users_per_course: int | None = Field(default=None, ge=1, le=10000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    kvca_client = KVCAClient(settings)
    storage = create_storage(settings)
    app.state.settings = settings
    app.state.kvca_client = kvca_client
    app.state.storage = storage
    app.state.sync_service = EnrolmentSyncService(kvca_client, storage)
    try:
        yield
    finally:
        await kvca_client.aclose()
        await storage.aclose()


app = FastAPI(
    title="KVCA Worker",
    version="0.4.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/storage")
async def storage_info() -> dict[str, str]:
    storage: Storage = app.state.storage
    return {"storage": storage.__class__.__name__}


@app.post("/jobs/enrolment-sync")
async def run_enrolment_sync(request: SyncRequest) -> dict[str, Any]:
    service: EnrolmentSyncService = app.state.sync_service
    settings: Settings = app.state.settings

    try:
        summary = await service.sync(
            category_id=request.category_id,
            max_categories=request.max_categories,
            max_users_per_course=request.max_users_per_course or settings.kvca_max_users_per_course,
            lock_ttl_seconds=settings.job_lock_ttl_seconds,
        )
        return {"ok": True, "summary": summary_to_dict(summary)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}") from exc
