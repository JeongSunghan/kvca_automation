# KVCA Automation - Delivery Tasks (Final)

## M0 - Source/API Baseline

- [ ] Document API specs for 3 admin tabs (endpoint, auth, params, stable unique key).
- [ ] Secure at least 30 sample records (JSON/CSV) for validation.

## M1 - Supabase + Next.js Foundation

- [ ] Create Supabase project (Auth + DB).
- [ ] Create tables:
  - `source_record`
  - `snapshot`
  - `alert`
  - `run_log`
  - `job_lock`
  - `sheet_outbox`
  - `notification_outbox`
- [ ] Apply minimum RLS policy set (`ADMIN`, `STAFF`).
- [ ] Implement basic Next.js login and dashboard screens.

## M2 - Worker Collection

- [ ] Create FastAPI worker project with Dockerfile.
- [ ] Collect `registration`, `payment`, `invoice` and upsert into `source_record`.
- [ ] Record run logs and implement retry on failure (minimum 2 retries).

## M3 - Diff/Alert + Review UI

- [ ] Generate snapshot-hash based diff.
- [ ] Implement alert rules (`NEW`, `CHANGED`, `AMBIGUOUS`, `NEEDS_REVIEW`, `FAILED`).
- [ ] Implement `/review` actions (approve/hold) and audit trail.

## M4 - Google Sheets Sync

- [ ] Queue to `sheet_outbox` and sync to Sheets with idempotency.
- [ ] Implement retry and alerting for `SHEET_FAILED`.

## M5 - Kakao AlimTalk Notifications

- [ ] Queue to `notification_outbox` and send AlimTalk messages.
- [ ] Prepare 3 templates:
  - Morning summary
  - Immediate critical alert
  - Optional end-of-day summary
- [ ] Implement retry and alerting for `NOTI_FAILED`.

## M6 - Deployment and Operations

- [ ] Deploy worker to Cloud Run.
- [ ] Configure Scheduler:
  - `07:00` daily refresh
  - `07:00-21:00` poll every 10-15 minutes
  - manual endpoint trigger
- [ ] Finalize operating docs (`RUNBOOK`, `DATA_CONTRACT`, `ARCHITECTURE`).
