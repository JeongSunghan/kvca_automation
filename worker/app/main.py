from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .kvca_client import KVCAClient
from .storage import Storage, create_storage
from .sync_service import EnrolmentSyncService, summary_to_dict


class SyncRequest(BaseModel):
    category_id: int = Field(description="sync target category_id")
    trigger_type: Literal["MANUAL", "SCHEDULER", "RETRY"] = Field(default="MANUAL")
    max_categories: int | None = Field(default=1, ge=1, le=100)
    max_users_per_course: int | None = Field(default=None, ge=1, le=10000)


class OutboxDispatchRequest(BaseModel):
    batch_size: int | None = Field(default=None, ge=1, le=1000)


class OutboxChainRequest(BaseModel):
    sheet_batch_size: int | None = Field(default=None, ge=1, le=1000)
    notification_batch_size: int | None = Field(default=None, ge=1, le=1000)


class FinalCheckRequest(BaseModel):
    category_id: int = Field(description="sync target category_id")
    trigger_type: Literal["MANUAL", "SCHEDULER", "RETRY"] = Field(default="MANUAL")
    max_categories: int | None = Field(default=1, ge=1, le=100)
    max_users_per_course: int | None = Field(default=None, ge=1, le=10000)
    sheet_batch_size: int | None = Field(default=None, ge=1, le=1000)
    notification_batch_size: int | None = Field(default=None, ge=1, le=1000)


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
            trigger_type=request.trigger_type,
            max_categories=request.max_categories,
            max_users_per_course=request.max_users_per_course or settings.kvca_max_users_per_course,
            lock_ttl_seconds=settings.job_lock_ttl_seconds,
        )
        return {"ok": True, "summary": summary_to_dict(summary)}
    except RuntimeError as exc:
        message = str(exc)
        if "job_lock active" in message:
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(status_code=500, detail=f"Sync failed: {message}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}") from exc


@app.post("/jobs/outbox/sheet-dispatch")
async def dispatch_sheet_outbox(request: OutboxDispatchRequest) -> dict[str, Any]:
    storage: Storage = app.state.storage
    try:
        summary = await storage.dispatch_sheet_outbox(batch_size=request.batch_size)
        return {"ok": True, "summary": summary.__dict__}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sheet outbox dispatch failed: {exc}") from exc


@app.post("/jobs/outbox/notification-dispatch")
async def dispatch_notification_outbox(request: OutboxDispatchRequest) -> dict[str, Any]:
    storage: Storage = app.state.storage
    try:
        summary = await storage.dispatch_notification_outbox(batch_size=request.batch_size)
        return {"ok": True, "summary": summary.__dict__}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Notification outbox dispatch failed: {exc}") from exc


@app.post("/jobs/outbox/dispatch")
async def dispatch_outbox_chain(request: OutboxChainRequest) -> dict[str, Any]:
    storage: Storage = app.state.storage
    try:
        sheet_summary = await storage.dispatch_sheet_outbox(batch_size=request.sheet_batch_size)
        notification_summary = await storage.dispatch_notification_outbox(batch_size=request.notification_batch_size)
        return {
            "ok": True,
            "summary": {
                "sheet": sheet_summary.__dict__,
                "notification": notification_summary.__dict__,
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Outbox chain dispatch failed: {exc}") from exc


@app.post("/jobs/ops/final-check")
async def run_final_check(request: FinalCheckRequest) -> dict[str, Any]:
    service: EnrolmentSyncService = app.state.sync_service
    settings: Settings = app.state.settings
    storage: Storage = app.state.storage

    try:
        sync_summary = await service.sync(
            category_id=request.category_id,
            trigger_type=request.trigger_type,
            max_categories=request.max_categories,
            max_users_per_course=request.max_users_per_course or settings.kvca_max_users_per_course,
            lock_ttl_seconds=settings.job_lock_ttl_seconds,
        )
        sheet_summary = await storage.dispatch_sheet_outbox(batch_size=request.sheet_batch_size)
        notification_summary = await storage.dispatch_notification_outbox(
            batch_size=request.notification_batch_size
        )
        return {
            "ok": True,
            "summary": {
                "sync": summary_to_dict(sync_summary),
                "sheet": sheet_summary.__dict__,
                "notification": notification_summary.__dict__,
            },
        }
    except RuntimeError as exc:
        message = str(exc)
        if "job_lock active" in message:
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(status_code=500, detail=f"Final check failed: {message}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Final check failed: {exc}") from exc
