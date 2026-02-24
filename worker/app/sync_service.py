from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

from .kvca_client import KVCAClient
from .redaction import redact_sensitive
from .storage import PersistResult, SourceRecordInput, Storage


@dataclass
class SyncSummary:
    categories_processed: int = 0
    courses_processed: int = 0
    status_rows_processed: int = 0
    details_processed: int = 0
    source_records_upserted: int = 0
    new_records: int = 0
    changed_records: int = 0
    created_alerts: int = 0
    failed_detail_calls: int = 0
    failed_course_calls: int = 0
    lock_acquired: bool = False
    started_at: str = ""
    finished_at: str = ""


class EnrolmentSyncService:
    def __init__(self, client: KVCAClient, storage: Storage) -> None:
        self._client = client
        self._storage = storage

    async def sync(
        self,
        category_id: int | None,
        max_categories: int | None,
        max_users_per_course: int | None,
        lock_ttl_seconds: int,
    ) -> SyncSummary:
        summary = SyncSummary(started_at=_utc_now())
        job_name = "enrolment_sync"
        lock_acquired = await self._storage.acquire_job_lock(job_name=job_name, ttl_seconds=lock_ttl_seconds)
        summary.lock_acquired = lock_acquired
        if not lock_acquired:
            raise RuntimeError("Job is already running (job_lock active).")

        run_id = await self._storage.start_run(job_name=job_name, trigger_type="MANUAL")

        try:
            categories = await self._resolve_categories(category_id, max_categories)
            summary.categories_processed = len(categories)

            source_records: list[SourceRecordInput] = []
            for term_id in categories:
                try:
                    courses = await self._client.fetch_courses_by_category(term_id)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 409:
                        summary.failed_course_calls += 1
                        continue
                    raise
                summary.courses_processed += len(courses)

                for course in courses:
                    course_id = _to_int(course.get("courseid") or course.get("id"))
                    if course_id is None:
                        continue

                    rows = await self._client.fetch_class_status_all(course_id)
                    if max_users_per_course is not None:
                        rows = rows[:max_users_per_course]

                    summary.status_rows_processed += len(rows)
                    for row in rows:
                        row_record = self._build_status_record(term_id=term_id, course_id=course_id, row=row)
                        if row_record is None:
                            continue
                        source_records.append(row_record)

                        user_id = row_record.user_id
                        if not user_id:
                            continue
                        detail = await self._safe_fetch_detail(summary, term_id=term_id, user_id=user_id)
                        if not detail:
                            continue
                        detail_record = self._build_detail_record(
                            term_id=term_id,
                            course_id=course_id,
                            user_id=user_id,
                            detail=detail,
                        )
                        source_records.append(detail_record)
                        summary.details_processed += 1

            persist_result: PersistResult = await self._storage.upsert_source_records(source_records)
            summary.source_records_upserted = persist_result.upserted_count
            summary.new_records = persist_result.new_count
            summary.changed_records = persist_result.changed_count
            summary.created_alerts = persist_result.alert_count
            summary.finished_at = _utc_now()
            await self._storage.finish_run(
                run_id=run_id,
                success=True,
                summary=summary_to_dict(summary),
                error_message=None,
            )
            return summary
        except Exception as exc:
            summary.finished_at = _utc_now()
            await self._storage.finish_run(
                run_id=run_id,
                success=False,
                summary=summary_to_dict(summary),
                error_message=str(exc),
            )
            raise
        finally:
            await self._storage.release_job_lock(job_name=job_name)

    async def _resolve_categories(self, category_id: int | None, max_categories: int | None) -> list[int]:
        if category_id is not None:
            return [category_id]
        categories = await self._client.fetch_categories()
        ids: list[int] = []
        for item in categories:
            value = _to_int(item.get("id"))
            if value is not None:
                ids.append(value)
        if max_categories is not None:
            ids = ids[:max_categories]
        return ids

    async def _safe_fetch_detail(self, summary: SyncSummary, term_id: int, user_id: str) -> dict[str, Any]:
        try:
            return await self._client.fetch_enrolment_user_info(term_id=term_id, user_id=user_id)
        except Exception:
            summary.failed_detail_calls += 1
            return {}

    def _build_status_record(
        self,
        term_id: int,
        course_id: int,
        row: dict[str, Any],
    ) -> SourceRecordInput | None:
        user = row.get("user", {})
        class_status = row.get("classStatus", {})
        if not isinstance(user, dict) or not isinstance(class_status, dict):
            return None
        user_id = _to_str(user.get("userId") or user.get("email"))
        if not user_id:
            return None
        source_id = f"{term_id}:{user_id}"
        payload = redact_sensitive(row)
        return SourceRecordInput(
            source_type="enrolment_status",
            source_id=source_id,
            category_id=term_id,
            course_id=course_id,
            term_id=term_id,
            user_id=user_id,
            user_name=_to_str(user.get("userName")),
            company_name=_to_str(user.get("companyName")),
            dept_name=_to_str(user.get("deptName")),
            job_position=_to_str(user.get("jobPosition")),
            status=_to_str(class_status.get("status")),
            status_msg=_to_str(class_status.get("statusmsg")),
            code_name=_to_str(class_status.get("codename")),
            ds_date=_parse_kvca_datetime(class_status.get("ds_date")),
            gc_date=_parse_kvca_datetime(class_status.get("gc_date")),
            sjc_date=_parse_kvca_datetime(class_status.get("sjc_date")),
            update_time=_parse_kvca_datetime(class_status.get("update_time")),
            payload=payload,
            payload_hash=_hash_payload(payload),
        )

    def _build_detail_record(
        self,
        term_id: int,
        course_id: int,
        user_id: str,
        detail: dict[str, Any],
    ) -> SourceRecordInput:
        payload = redact_sensitive(detail)
        source_id = f"{term_id}:{user_id}"
        return SourceRecordInput(
            source_type="enrolment_user_detail",
            source_id=source_id,
            category_id=term_id,
            course_id=course_id,
            term_id=term_id,
            user_id=user_id,
            user_name=_to_str(payload.get("userName")),
            company_name=_to_str(payload.get("companyName")),
            dept_name=_to_str(payload.get("deptName")),
            job_position=_to_str(payload.get("jobPosition")),
            status=None,
            status_msg=None,
            code_name=None,
            ds_date=None,
            gc_date=None,
            sjc_date=None,
            update_time=None,
            payload=payload,
            payload_hash=_hash_payload(payload),
        )


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_kvca_datetime(value: Any) -> str | None:
    text = _to_str(value)
    if not text or text.lower() == "empty":
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            naive = datetime.strptime(text, fmt)
            kst = naive.replace(tzinfo=timezone(timedelta(hours=9)))
            return kst.isoformat()
        except ValueError:
            continue
    return None


def summary_to_dict(summary: SyncSummary) -> dict[str, Any]:
    return asdict(summary)
