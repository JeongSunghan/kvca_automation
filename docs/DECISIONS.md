# Architecture Decisions

## Confirmed Decisions

- `Supabase` only for DB and Auth (Firebase removed).
- Alerts and review state live in DB as SSOT.
- Kakao and Google Sheets consume data via outbox workers.
- Kakao channel uses AlimTalk template messages.
- Scheduling is cloud-native (`Cloud Scheduler + Cloud Run Worker`), no desktop runtime.
- Data correctness gates all downstream actions with:
  - `AUTO`
  - `AMBIGUOUS`
  - `NEEDS_REVIEW`

## Schedule Decision (KST)

- `daily_refresh`: 07:00
- `poll`: every 10-15 minutes between 07:00 and 21:00
- `manual trigger`: allowed anytime with lock enforcement

## Why This Structure

- Minimizes coupling between UI and integrations.
- Keeps failure handling explicit with retryable queues.
- Supports operator control without blocking automation.
