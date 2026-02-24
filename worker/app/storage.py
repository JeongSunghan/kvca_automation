from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

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


@dataclass
class PersistResult:
    upserted_count: int
    new_count: int
    changed_count: int
    alert_count: int


class Storage(Protocol):
    async def acquire_job_lock(self, job_name: str, ttl_seconds: int) -> bool: ...

    async def release_job_lock(self, job_name: str) -> None: ...

    async def start_run(self, job_name: str, trigger_type: str) -> int | None: ...

    async def finish_run(
        self,
        run_id: int | None,
        success: bool,
        summary: dict[str, Any],
        error_message: str | None = None,
    ) -> None: ...

    async def upsert_source_records(self, records: list[SourceRecordInput]) -> PersistResult: ...

    async def aclose(self) -> None: ...


class NoopStorage:
    async def acquire_job_lock(self, job_name: str, ttl_seconds: int) -> bool:
        return True

    async def release_job_lock(self, job_name: str) -> None:
        return None

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

    async def upsert_source_records(self, records: list[SourceRecordInput]) -> PersistResult:
        return PersistResult(
            upserted_count=len(records),
            new_count=0,
            changed_count=0,
            alert_count=0,
        )

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
        self._alert_cooldown_minutes = max(0, settings.alert_cooldown_minutes)
        self._lock_owner = f"worker-{uuid4()}"

    async def acquire_job_lock(self, job_name: str, ttl_seconds: int) -> bool:
        now = _utc_now()
        expires = _utc_after(seconds=max(1, ttl_seconds))
        payload = {
            "job_name": job_name,
            "locked_by": self._lock_owner,
            "locked_at": now,
            "lock_expires_at": expires,
        }

        # 1) try fresh insert
        insert_response = await self._client.post("/job_lock", json=payload, headers={"Prefer": "return=minimal"})
        if insert_response.is_success:
            return True
        if insert_response.status_code not in {409}:
            insert_response.raise_for_status()

        # 2) if existing lock is expired, take over
        takeover_query = (
            f"/job_lock?"
            f"job_name=eq.{_encode_eq_value(job_name)}&"
            f"lock_expires_at=lt.{_encode_eq_value(now)}&"
            "select=job_name"
        )
        takeover_response = await self._client.patch(
            takeover_query,
            json={"locked_by": self._lock_owner, "locked_at": now, "lock_expires_at": expires},
            headers={"Prefer": "return=representation"},
        )
        takeover_response.raise_for_status()
        takeover_rows = takeover_response.json()
        if isinstance(takeover_rows, list) and takeover_rows:
            return True

        # 3) if already owned by this worker, just refresh ttl
        refresh_query = (
            f"/job_lock?"
            f"job_name=eq.{_encode_eq_value(job_name)}&"
            f"locked_by=eq.{_encode_eq_value(self._lock_owner)}&"
            "select=job_name"
        )
        refresh_response = await self._client.patch(
            refresh_query,
            json={"locked_at": now, "lock_expires_at": expires},
            headers={"Prefer": "return=representation"},
        )
        refresh_response.raise_for_status()
        refresh_rows = refresh_response.json()
        return isinstance(refresh_rows, list) and bool(refresh_rows)

    async def release_job_lock(self, job_name: str) -> None:
        query = (
            f"/job_lock?"
            f"job_name=eq.{_encode_eq_value(job_name)}&"
            f"locked_by=eq.{_encode_eq_value(self._lock_owner)}"
        )
        response = await self._client.delete(query, headers={"Prefer": "return=minimal"})
        response.raise_for_status()

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
            "total_records": int(summary.get("source_records_upserted", 0)),
            "changed_records": int(summary.get("changed_records", 0)),
            "created_alerts": int(summary.get("created_alerts", 0)),
            "retry_count": int(summary.get("failed_detail_calls", 0)),
        }
        if error_message:
            payload["error_message"] = error_message[:1500]
        response = await self._client.patch(f"/run_log?id=eq.{run_id}", json=payload)
        response.raise_for_status()

    async def upsert_source_records(self, records: list[SourceRecordInput]) -> PersistResult:
        if not records:
            return PersistResult(upserted_count=0, new_count=0, changed_count=0, alert_count=0)

        existing_hashes = await self._fetch_existing_hashes(records)
        diff = self._build_diff(records, existing_hashes)
        alert_rows = self._build_alert_rows(records, diff)
        alert_rows = await self._filter_alert_rows_by_cooldown(alert_rows)
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
        if alert_rows:
            await self._insert_alerts(alert_rows)
        business_diff_counts = self._count_business_diff(records, diff)
        return PersistResult(
            upserted_count=len(records),
            new_count=business_diff_counts["NEW"],
            changed_count=business_diff_counts["CHANGED"],
            alert_count=len(alert_rows),
        )

    async def _fetch_existing_hashes(self, records: list[SourceRecordInput]) -> dict[tuple[str, str], str]:
        result: dict[tuple[str, str], str] = {}
        source_types = sorted({record.source_type for record in records})
        source_ids = sorted({record.source_id for record in records})
        if not source_types or not source_ids:
            return result

        type_filter = _build_in_filter(source_types)
        for id_chunk in _chunks_text(source_ids, 200):
            id_filter = _build_in_filter(id_chunk)
            query = (
                "/source_record"
                f"?select=source_type,source_id,payload_hash"
                f"&source_type=in.{quote(type_filter, safe='(),\"')}"
                f"&source_id=in.{quote(id_filter, safe='(),\"@:._-')}"
            )
            response = await self._client.get(query)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                source_type = row.get("source_type")
                source_id = row.get("source_id")
                payload_hash = row.get("payload_hash")
                if isinstance(source_type, str) and isinstance(source_id, str) and isinstance(payload_hash, str):
                    result[(source_type, source_id)] = payload_hash
        return result

    def _build_diff(
        self,
        records: list[SourceRecordInput],
        existing_hashes: dict[tuple[str, str], str],
    ) -> dict[tuple[str, str], str]:
        diff: dict[tuple[str, str], str] = {}
        for record in records:
            key = (record.source_type, record.source_id)
            old_hash = existing_hashes.get(key)
            if old_hash is None:
                diff[key] = "NEW"
            elif old_hash != record.payload_hash:
                diff[key] = "CHANGED"
        return diff

    def _build_alert_rows(
        self,
        records: list[SourceRecordInput],
        diff: dict[tuple[str, str], str],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in records:
            # MVP: business-facing alerts only from enrolment status records.
            if record.source_type != "enrolment_status":
                continue
            key = (record.source_type, record.source_id)
            alert_type = diff.get(key)
            if alert_type not in {"NEW", "CHANGED"}:
                continue
            is_paid = _is_truthy_timestamp(record.gc_date)
            is_doc_ready = _is_truthy_timestamp(record.sjc_date)
            paid_label = "Y" if is_paid else "N"
            doc_ready_label = "Y" if is_doc_ready else "N"
            severity = _determine_severity(
                alert_type=alert_type,
                status=record.status,
                is_paid=is_paid,
                is_doc_ready=is_doc_ready,
            )
            rows.append(
                {
                    "source_type": record.source_type,
                    "source_id": record.source_id,
                    "alert_type": alert_type,
                    "severity": severity,
                    "title": f"{alert_type} enrolment status",
                    "message": (
                        f"{record.source_id} "
                        f"status={record.status or '-'} "
                        f"paid={paid_label} "
                        f"doc_ready={doc_ready_label}"
                    ),
                    "detail": {
                        "source_type": record.source_type,
                        "source_id": record.source_id,
                        "category_id": record.category_id,
                        "course_id": record.course_id,
                        "term_id": record.term_id,
                        "user_id": record.user_id,
                        "status": record.status,
                        "status_msg": record.status_msg,
                        "code_name": record.code_name,
                        "is_paid": is_paid,
                        "is_doc_ready": is_doc_ready,
                        "gc_date": record.gc_date,
                        "sjc_date": record.sjc_date,
                        "update_time": record.update_time,
                        "payload_hash": record.payload_hash,
                    },
                    "review_status": "AUTO",
                    "resolved": False,
                }
            )
        return rows

    def _count_business_diff(
        self,
        records: list[SourceRecordInput],
        diff: dict[tuple[str, str], str],
    ) -> dict[str, int]:
        counts = {"NEW": 0, "CHANGED": 0}
        for record in records:
            if record.source_type != "enrolment_status":
                continue
            key = (record.source_type, record.source_id)
            value = diff.get(key)
            if value in counts:
                counts[value] += 1
        return counts

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

    async def _insert_alerts(self, rows: list[dict[str, Any]]) -> None:
        for chunk in _chunks(rows, 500):
            response = await self._client.post(
                "/alert",
                json=chunk,
                headers={"Prefer": "return=minimal"},
            )
            response.raise_for_status()

    async def _filter_alert_rows_by_cooldown(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._alert_cooldown_minutes <= 0 or not rows:
            return rows
        since = _utc_after(minutes=-self._alert_cooldown_minutes)
        filtered: list[dict[str, Any]] = []
        for row in rows:
            source_type = row.get("source_type")
            source_id = row.get("source_id")
            alert_type = row.get("alert_type")
            if not isinstance(source_type, str) or not isinstance(source_id, str) or not isinstance(alert_type, str):
                continue
            exists = await self._has_recent_alert(
                source_type=source_type,
                source_id=source_id,
                alert_type=alert_type,
                since=since,
            )
            if not exists:
                filtered.append(row)
        return filtered

    async def _has_recent_alert(
        self,
        source_type: str,
        source_id: str,
        alert_type: str,
        since: str,
    ) -> bool:
        query = (
            "/alert?"
            "select=id&"
            f"source_type=eq.{_encode_eq_value(source_type)}&"
            f"source_id=eq.{_encode_eq_value(source_id)}&"
            f"alert_type=eq.{_encode_eq_value(alert_type)}&"
            f"created_at=gte.{_encode_eq_value(since)}&"
            "order=created_at.desc&"
            "limit=1"
        )
        response = await self._client.get(query)
        response.raise_for_status()
        rows = response.json()
        return isinstance(rows, list) and bool(rows)

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


def _chunks_text(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _build_in_filter(values: list[str]) -> str:
    quoted = []
    for value in values:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        quoted.append(f'"{escaped}"')
    return f"({','.join(quoted)})"


def _is_truthy_timestamp(value: str | None) -> bool:
    if value is None:
        return False
    text = value.strip()
    if not text:
        return False
    return text.lower() != "empty"


def _determine_severity(alert_type: str, status: str | None, is_paid: bool, is_doc_ready: bool) -> str:
    if alert_type == "CHANGED" and (is_paid or is_doc_ready):
        return "high"
    if alert_type == "NEW" and (is_paid or is_doc_ready):
        return "medium"
    if status == "DS":
        return "low"
    return "medium"


def _encode_eq_value(value: str) -> str:
    return quote(value, safe="")


def _utc_after(*, seconds: int = 0, minutes: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds, minutes=minutes)).isoformat()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
