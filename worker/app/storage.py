from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
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


@dataclass
class OutboxDispatchResult:
    picked: int = 0
    processed: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    notification_enqueued: int = 0


class Storage(Protocol):
    async def acquire_job_lock(self, job_name: str, ttl_seconds: int) -> bool: ...

    async def release_job_lock(self, job_name: str) -> None: ...

    async def start_run(self, job_name: str, trigger_type: str) -> int | None: ...

    async def finish_run(
        self,
        job_name: str,
        run_id: int | None,
        success: bool,
        summary: dict[str, Any],
        error_message: str | None = None,
    ) -> None: ...

    async def upsert_source_records(self, records: list[SourceRecordInput]) -> PersistResult: ...

    async def dispatch_sheet_outbox(self, batch_size: int | None = None) -> OutboxDispatchResult: ...

    async def dispatch_notification_outbox(self, batch_size: int | None = None) -> OutboxDispatchResult: ...

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
        job_name: str,
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

    async def dispatch_sheet_outbox(self, batch_size: int | None = None) -> OutboxDispatchResult:
        return OutboxDispatchResult()

    async def dispatch_notification_outbox(self, batch_size: int | None = None) -> OutboxDispatchResult:
        return OutboxDispatchResult()

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
        self._sheet_dispatch_batch_size = max(1, settings.sheet_dispatch_batch_size)
        self._noti_dispatch_batch_size = max(1, settings.noti_dispatch_batch_size)
        self._outbox_retry_base_seconds = max(1, settings.outbox_retry_base_seconds)
        self._outbox_retry_max_seconds = max(self._outbox_retry_base_seconds, settings.outbox_retry_max_seconds)
        self._sheet_webhook_url = settings.sheet_webhook_url
        self._kakao_webhook_url = settings.kakao_webhook_url
        self._kakao_template_code = settings.kakao_template_code
        self._kakao_default_recipient = settings.kakao_default_recipient
        self._dispatch_http = httpx.AsyncClient(timeout=timeout)

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
        job_name: str,
        run_id: int | None,
        success: bool,
        summary: dict[str, Any],
        error_message: str | None = None,
    ) -> None:
        failure_alert_count = 0
        if not success:
            failure_alert = self._build_run_failure_alert(
                job_name=job_name,
                run_id=run_id,
                error_message=error_message,
                summary=summary,
            )
            filtered = await self._filter_alert_rows_by_cooldown([failure_alert])
            if filtered:
                await self._insert_alerts(filtered)
                failure_alert_count = len(filtered)

        if run_id is None:
            return

        created_alerts = int(summary.get("created_alerts", 0)) + failure_alert_count
        payload = {
            "status": "SUCCESS" if success else "FAILED",
            "finished_at": _utc_now(),
            "total_records": int(summary.get("source_records_upserted", 0)),
            "changed_records": int(summary.get("changed_records", 0)),
            "created_alerts": created_alerts,
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

    async def dispatch_sheet_outbox(self, batch_size: int | None = None) -> OutboxDispatchResult:
        limit = max(1, batch_size or self._sheet_dispatch_batch_size)
        rows = await self._fetch_sheet_outbox_candidates(limit)
        result = OutboxDispatchResult(picked=len(rows))
        for row in rows:
            row_id = _to_int(row.get("id"))
            status = _to_str(row.get("status"))
            if row_id is None or not status:
                result.skipped += 1
                continue
            claimed = await self._claim_outbox_row("sheet_outbox", row_id=row_id, current_status=status)
            if not claimed:
                result.skipped += 1
                continue
            result.processed += 1
            try:
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                await self._deliver_sheet_payload(payload)
                enqueued = await self._enqueue_notification_from_sheet(row)
                if enqueued:
                    result.notification_enqueued += 1
                await self._mark_outbox_sent("sheet_outbox", row_id=row_id)
                result.sent += 1
            except Exception as exc:
                await self._mark_outbox_failed(
                    "sheet_outbox",
                    row_id=row_id,
                    current_retry_count=_to_int(row.get("retry_count")) or 0,
                    error_message=str(exc),
                )
                result.failed += 1
        return result

    async def dispatch_notification_outbox(self, batch_size: int | None = None) -> OutboxDispatchResult:
        limit = max(1, batch_size or self._noti_dispatch_batch_size)
        rows = await self._fetch_notification_outbox_candidates(limit)
        result = OutboxDispatchResult(picked=len(rows))
        for row in rows:
            row_id = _to_int(row.get("id"))
            status = _to_str(row.get("status"))
            if row_id is None or not status:
                result.skipped += 1
                continue
            claimed = await self._claim_outbox_row("notification_outbox", row_id=row_id, current_status=status)
            if not claimed:
                result.skipped += 1
                continue
            result.processed += 1
            try:
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                await self._deliver_notification_payload(
                    channel=_to_str(row.get("channel")) or "KAKAO_ALIMTALK",
                    template_code=_to_str(row.get("template_code")) or self._kakao_template_code,
                    recipient=_to_str(row.get("recipient")) or self._kakao_default_recipient,
                    payload=payload,
                )
                await self._mark_outbox_sent("notification_outbox", row_id=row_id)
                result.sent += 1
            except Exception as exc:
                await self._mark_outbox_failed(
                    "notification_outbox",
                    row_id=row_id,
                    current_retry_count=_to_int(row.get("retry_count")) or 0,
                    error_message=str(exc),
                )
                result.failed += 1
        return result

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
        await self._enqueue_sheet_outbox(rows)

    async def _enqueue_sheet_outbox(self, alert_rows: list[dict[str, Any]]) -> None:
        if not alert_rows:
            return
        outbox_rows: list[dict[str, Any]] = []
        for row in alert_rows:
            source_type = _to_str(row.get("source_type"))
            source_id = _to_str(row.get("source_id"))
            alert_type = _to_str(row.get("alert_type"))
            if not source_type or not source_id or not alert_type:
                continue
            row_key = _build_alert_row_key(row)
            if await self._sheet_outbox_row_exists(row_key):
                continue
            outbox_rows.append(
                {
                    "source_type": source_type,
                    "source_id": source_id,
                    "row_key": row_key,
                    "payload": {
                        "source_type": source_type,
                        "source_id": source_id,
                        "alert_type": alert_type,
                        "severity": _to_str(row.get("severity")) or "medium",
                        "title": _to_str(row.get("title")),
                        "message": _to_str(row.get("message")),
                        "detail": row.get("detail") if isinstance(row.get("detail"), dict) else {},
                    },
                }
            )
        if not outbox_rows:
            return
        for chunk in _chunks(outbox_rows, 500):
            response = await self._client.post(
                "/sheet_outbox",
                json=chunk,
                headers={"Prefer": "return=minimal"},
            )
            response.raise_for_status()

    async def _sheet_outbox_row_exists(self, row_key: str) -> bool:
        query = (
            "/sheet_outbox?"
            "select=id&"
            f"row_key=eq.{_encode_eq_value(row_key)}&"
            "limit=1"
        )
        response = await self._client.get(query)
        response.raise_for_status()
        rows = response.json()
        return isinstance(rows, list) and bool(rows)

    async def _fetch_sheet_outbox_candidates(self, batch_size: int) -> list[dict[str, Any]]:
        return await self._fetch_outbox_candidates(
            table_name="sheet_outbox",
            select_fields="id,source_type,source_id,row_key,payload,status,retry_count,next_retry_at,created_at,updated_at",
            batch_size=batch_size,
        )

    async def _fetch_notification_outbox_candidates(self, batch_size: int) -> list[dict[str, Any]]:
        return await self._fetch_outbox_candidates(
            table_name="notification_outbox",
            select_fields=(
                "id,source_type,source_id,channel,template_code,recipient,payload,"
                "status,retry_count,next_retry_at,created_at,updated_at"
            ),
            batch_size=batch_size,
        )

    async def _fetch_outbox_candidates(
        self,
        *,
        table_name: str,
        select_fields: str,
        batch_size: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        rows.extend(
            await self._query_outbox_rows(
                table_name=table_name,
                select_fields=select_fields,
                status="PENDING",
                extra_filters="order=created_at.asc",
                limit=batch_size,
            )
        )
        if len(rows) < batch_size:
            remaining = batch_size - len(rows)
            rows.extend(
                await self._query_outbox_rows(
                    table_name=table_name,
                    select_fields=select_fields,
                    status="FAILED",
                    extra_filters="next_retry_at=is.null&order=updated_at.asc",
                    limit=remaining,
                )
            )
        if len(rows) < batch_size:
            remaining = batch_size - len(rows)
            now = _utc_now()
            rows.extend(
                await self._query_outbox_rows(
                    table_name=table_name,
                    select_fields=select_fields,
                    status="FAILED",
                    extra_filters=f"next_retry_at=lte.{_encode_eq_value(now)}&order=next_retry_at.asc",
                    limit=remaining,
                )
            )
        # Deduplicate by id, keeping the first order.
        deduped: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for row in rows:
            row_id = _to_int(row.get("id"))
            if row_id is None:
                continue
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            deduped.append(row)
        return deduped[:batch_size]

    async def _query_outbox_rows(
        self,
        *,
        table_name: str,
        select_fields: str,
        status: str,
        extra_filters: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = (
            f"/{table_name}?"
            f"select={select_fields}&"
            f"status=eq.{_encode_eq_value(status)}&"
            f"{extra_filters}&"
            f"limit={limit}"
        )
        response = await self._client.get(query)
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def _claim_outbox_row(self, table_name: str, *, row_id: int, current_status: str) -> bool:
        query = (
            f"/{table_name}?"
            f"id=eq.{row_id}&"
            f"status=eq.{_encode_eq_value(current_status)}&"
            "select=id"
        )
        response = await self._client.patch(
            query,
            json={
                "status": "PROCESSING",
                "last_attempt_at": _utc_now(),
            },
            headers={"Prefer": "return=representation"},
        )
        response.raise_for_status()
        rows = response.json()
        return isinstance(rows, list) and bool(rows)

    async def _mark_outbox_sent(self, table_name: str, *, row_id: int) -> None:
        response = await self._client.patch(
            f"/{table_name}?id=eq.{row_id}",
            json={
                "status": "SENT",
                "last_error": None,
                "next_retry_at": None,
                "last_attempt_at": _utc_now(),
            },
        )
        response.raise_for_status()

    async def _mark_outbox_failed(
        self,
        table_name: str,
        *,
        row_id: int,
        current_retry_count: int,
        error_message: str,
    ) -> None:
        next_retry_count = current_retry_count + 1
        delay_seconds = min(
            self._outbox_retry_base_seconds * (2 ** max(0, current_retry_count)),
            self._outbox_retry_max_seconds,
        )
        response = await self._client.patch(
            f"/{table_name}?id=eq.{row_id}",
            json={
                "status": "FAILED",
                "retry_count": next_retry_count,
                "last_error": error_message[:1000],
                "last_attempt_at": _utc_now(),
                "next_retry_at": _utc_after(seconds=delay_seconds),
            },
        )
        response.raise_for_status()

    async def _deliver_sheet_payload(self, payload: dict[str, Any]) -> None:
        if not self._sheet_webhook_url:
            return
        response = await self._dispatch_http.post(self._sheet_webhook_url, json=payload)
        response.raise_for_status()

    async def _deliver_notification_payload(
        self,
        *,
        channel: str,
        template_code: str,
        recipient: str,
        payload: dict[str, Any],
    ) -> None:
        if not self._kakao_webhook_url:
            return
        body = {
            "channel": channel,
            "template_code": template_code,
            "recipient": recipient,
            "payload": payload,
        }
        response = await self._dispatch_http.post(self._kakao_webhook_url, json=body)
        response.raise_for_status()

    async def _enqueue_notification_from_sheet(self, sheet_row: dict[str, Any]) -> bool:
        row_key = _to_str(sheet_row.get("row_key"))
        if not row_key:
            return False
        if await self._notification_outbox_exists(row_key):
            return False
        payload = sheet_row.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        response = await self._client.post(
            "/notification_outbox",
            json={
                "source_type": "sheet_alert",
                "source_id": row_key,
                "channel": "KAKAO_ALIMTALK",
                "template_code": self._kakao_template_code,
                "recipient": self._kakao_default_recipient,
                "payload": {
                    "row_key": row_key,
                    "sheet_outbox_id": _to_int(sheet_row.get("id")),
                    "source_type": _to_str(sheet_row.get("source_type")),
                    "source_id": _to_str(sheet_row.get("source_id")),
                    "alert": payload,
                },
            },
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()
        return True

    async def _notification_outbox_exists(self, row_key: str) -> bool:
        query = (
            "/notification_outbox?"
            "select=id&"
            "source_type=eq.sheet_alert&"
            f"source_id=eq.{_encode_eq_value(row_key)}&"
            f"template_code=eq.{_encode_eq_value(self._kakao_template_code)}&"
            f"recipient=eq.{_encode_eq_value(self._kakao_default_recipient)}&"
            "limit=1"
        )
        response = await self._client.get(query)
        response.raise_for_status()
        rows = response.json()
        return isinstance(rows, list) and bool(rows)

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
        await self._dispatch_http.aclose()

    def _build_run_failure_alert(
        self,
        *,
        job_name: str,
        run_id: int | None,
        error_message: str | None,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        error_group, http_status_code = _classify_run_failure(error_message)
        severity = _determine_failure_severity(error_group)
        source_id = f"{job_name}:{error_group}"
        message = (
            f"{job_name} failed "
            f"group={error_group} "
            f"http={http_status_code if http_status_code is not None else '-'}"
        )
        return {
            "source_type": "run_log",
            "source_id": source_id,
            "alert_type": "FAILED",
            "severity": severity,
            "title": f"{job_name} run failed",
            "message": message,
            "detail": {
                "job_name": job_name,
                "run_id": run_id,
                "error_group": error_group,
                "http_status_code": http_status_code,
                "error_message": (error_message or "")[:1500],
                "summary": {
                    "categories_processed": int(summary.get("categories_processed", 0)),
                    "courses_processed": int(summary.get("courses_processed", 0)),
                    "status_rows_processed": int(summary.get("status_rows_processed", 0)),
                    "details_processed": int(summary.get("details_processed", 0)),
                    "source_records_upserted": int(summary.get("source_records_upserted", 0)),
                    "new_records": int(summary.get("new_records", 0)),
                    "changed_records": int(summary.get("changed_records", 0)),
                    "created_alerts": int(summary.get("created_alerts", 0)),
                    "failed_detail_calls": int(summary.get("failed_detail_calls", 0)),
                    "failed_course_calls": int(summary.get("failed_course_calls", 0)),
                },
            },
            "review_status": "AUTO",
            "resolved": False,
        }


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


def _extract_http_status_code(error_message: str | None) -> int | None:
    if not error_message:
        return None
    typed_match = re.search(r"(?:Client|Server) error '([45]\d{2})", error_message)
    if typed_match:
        return int(typed_match.group(1))
    fallback_match = re.search(r"\b([45]\d{2})\b", error_message)
    if fallback_match:
        return int(fallback_match.group(1))
    return None


def _classify_run_failure(error_message: str | None) -> tuple[str, int | None]:
    text = (error_message or "").lower()
    if "job_lock active" in text:
        return ("LOCK_CONFLICT", 409)
    status_code = _extract_http_status_code(error_message)
    if status_code == 409:
        return ("HTTP_409", status_code)
    if status_code is not None and 500 <= status_code <= 599:
        return ("HTTP_5XX", status_code)
    if status_code is not None and 400 <= status_code <= 499:
        return ("HTTP_4XX", status_code)
    if "timeout" in text or "timed out" in text:
        return ("TIMEOUT", None)
    return ("UNKNOWN", status_code)


def _determine_failure_severity(error_group: str) -> str:
    if error_group in {"HTTP_5XX", "TIMEOUT"}:
        return "high"
    if error_group in {"HTTP_409", "HTTP_4XX"}:
        return "medium"
    if error_group == "LOCK_CONFLICT":
        return "low"
    return "medium"


def _build_alert_row_key(alert_row: dict[str, Any]) -> str:
    source_type = _to_str(alert_row.get("source_type")) or "unknown"
    source_id = _to_str(alert_row.get("source_id")) or "unknown"
    alert_type = _to_str(alert_row.get("alert_type")) or "UNKNOWN"
    raw = json.dumps(
        {
            "source_type": source_type,
            "source_id": source_id,
            "alert_type": alert_type,
            "title": _to_str(alert_row.get("title")),
            "message": _to_str(alert_row.get("message")),
            "detail": alert_row.get("detail") if isinstance(alert_row.get("detail"), dict) else {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{source_type}:{source_id}:{alert_type}:{digest}"


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _encode_eq_value(value: str) -> str:
    return quote(value, safe="")


def _utc_after(*, seconds: int = 0, minutes: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds, minutes=minutes)).isoformat()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
