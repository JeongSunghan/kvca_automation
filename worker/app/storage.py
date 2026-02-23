from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from .config import Settings


@dataclass
class SourceRecordInput:
    source_type: str
    source_id: str
    category_id: int | None
    course_id: int | None
    term_id: int | None
    user_id: str | None
    user_name: str | None
    company_name: str | None
    dept_name: str | None
    job_position: str | None
    status: str | None
    status_msg: str | None
    code_name: str | None
    ds_date: str | None
    gc_date: str | None
    sjc_date: str | None
    update_time: str | None
    payload: dict[str, Any]
    payload_hash: str


class Storage(Protocol):
    async def start_run(self, job_name: str, trigger_type: str) -> int | None: ...

    async def finish_run(
        self,
        run_id: int | None,
        success: bool,
        summary: dict[str, Any],
        error_message: str | None = None,
    ) -> None: ...

    async def upsert_source_records(self, records: list[SourceRecordInput]) -> int: ...

    async def aclose(self) -> None: ...


class NoopStorage:
    async def start_run(self, job_name: str, trigger_type: str) -> int | None:
        return None

    async def finish_run(
        self,
        run_id: int | None,
        success: bool,
        summary: dict[str, Any],
        error_message: str | None = None,
    ) -> None:
        return None

    async def upsert_source_records(self, records: list[SourceRecordInput]) -> int:
        return len(records)

    async def aclose(self) -> None:
        return None


class SupabaseStorage:
    def __init__(self, settings: Settings) -> None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for Supabase storage.")
        base_url = f"{settings.supabase_url.rstrip('/')}/rest/v1"
        timeout = httpx.Timeout(settings.supabase_request_timeout_ms / 1000)
        key = settings.supabase_service_role_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )

    async def start_run(self, job_name: str, trigger_type: str) -> int | None:
        payload = {
            "job_name": job_name,
            "trigger_type": trigger_type,
            "status": "RUNNING",
            "started_at": _utc_now(),
        }
        response = await self._client.post("/run_log?select=id", json=payload, headers={"Prefer": "return=representation"})
        response.raise_for_status()
        rows = response.json()
        if isinstance(rows, list) and rows:
            run_id = rows[0].get("id")
            if isinstance(run_id, int):
                return run_id
            if isinstance(run_id, str) and run_id.isdigit():
                return int(run_id)
        return None

    async def finish_run(
        self,
        run_id: int | None,
        success: bool,
        summary: dict[str, Any],
        error_message: str | None = None,
    ) -> None:
        if run_id is None:
            return
        payload = {
            "status": "SUCCESS" if success else "FAILED",
            "finished_at": _utc_now(),
            "total_records": int(summary.get("status_rows_processed", 0)),
            "changed_records": 0,
            "created_alerts": 0,
            "retry_count": int(summary.get("failed_detail_calls", 0)),
        }
        if error_message:
            payload["error_message"] = error_message[:1500]
        response = await self._client.patch(f"/run_log?id=eq.{run_id}", json=payload)
        response.raise_for_status()

    async def upsert_source_records(self, records: list[SourceRecordInput]) -> int:
        if not records:
            return 0

        now = _utc_now()
        source_rows: list[dict[str, Any]] = []
        snapshot_rows: list[dict[str, Any]] = []
        for record in records:
            source_rows.append(
                {
                    "source_type": record.source_type,
                    "source_id": record.source_id,
                    "category_id": record.category_id,
                    "course_id": record.course_id,
                    "term_id": record.term_id,
                    "user_id": record.user_id,
                    "user_name": record.user_name,
                    "company_name": record.company_name,
                    "dept_name": record.dept_name,
                    "job_position": record.job_position,
                    "status": record.status,
                    "status_msg": record.status_msg,
                    "code_name": record.code_name,
                    "ds_date": record.ds_date,
                    "gc_date": record.gc_date,
                    "sjc_date": record.sjc_date,
                    "update_time": record.update_time,
                    "payload": record.payload,
                    "payload_hash": record.payload_hash,
                    "last_seen_at": now,
                }
            )
            snapshot_rows.append(
                {
                    "source_type": record.source_type,
                    "source_id": record.source_id,
                    "snapshot_hash": record.payload_hash,
                    "payload": record.payload,
                }
            )

        await self._upsert_source_rows(source_rows)
        await self._insert_snapshots(snapshot_rows)
        return len(records)

    async def _upsert_source_rows(self, rows: list[dict[str, Any]]) -> None:
        for chunk in _chunks(rows, 500):
            response = await self._client.post(
                "/source_record?on_conflict=source_type,source_id",
                json=chunk,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            response.raise_for_status()

    async def _insert_snapshots(self, rows: list[dict[str, Any]]) -> None:
        for chunk in _chunks(rows, 500):
            response = await self._client.post(
                "/snapshot",
                json=chunk,
                headers={"Prefer": "return=minimal"},
            )
            response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()


def create_storage(settings: Settings) -> Storage:
    has_url = bool(settings.supabase_url)
    has_key = bool(settings.supabase_service_role_key)
    if has_url and has_key:
        return SupabaseStorage(settings)
    if has_url or has_key:
        raise RuntimeError("Set both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, or leave both empty.")
    return NoopStorage()


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
