# Architecture

## Components

- `Next.js (Vercel)`: Dashboard, Review Queue, Run Logs, Manual Trigger
- `Supabase`: Auth, PostgreSQL, RLS
- `Worker (Cloud Run / FastAPI)`: crawling/API fetch, normalize, snapshot/diff, alerts, outbox processing
- `Cloud Scheduler`: `daily_refresh` (07:00 KST), `poll` (07:00-21:00 KST), optional `daily_summary` (21:00 KST)

## Data Flow

1. Trigger from Scheduler or Manual UI.
2. Worker fetches source data and upserts into `source_record`.
3. Worker writes snapshot and computes diff.
4. Worker creates alerts by rule (`NEW`, `CHANGED`, `AMBIGUOUS`, `NEEDS_REVIEW`, `FAILED`).
5. `AUTO` items are queued in `sheet_outbox` and synced to Google Sheets.
6. Alert notifications are queued in `notification_outbox` and sent through Kakao AlimTalk.
7. Next.js UI reads operational state from Supabase.

## Reliability Pattern

- Database-first write for all important state.
- Outbox pattern for external side effects (Sheets, Kakao).
- Retry with backoff on worker-side failures.
- Job lock to avoid overlapping runs.

## Non-goals (MVP)

- Complex analytics and BI reporting
- Multi-channel notification orchestration
- Advanced assignment/routing rules
