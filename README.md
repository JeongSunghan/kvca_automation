# KVCA Automation

KVCA Automation is an operations automation project for monitoring admin-page records, detecting changes, routing review-required items, and delivering updates to Google Sheets and Kakao AlimTalk.

## Project Goals

- Collect data from 3 admin tabs (`registration`, `payment`, `invoice`) on schedule.
- Detect new and changed records with snapshot-based diffing.
- Separate records by confidence (`AUTO`, `AMBIGUOUS`, `NEEDS_REVIEW`).
- Keep the database as the single source of truth.
- Fan out updates reliably through outbox workers (Sheets and Kakao).

## System Overview

- `Next.js (Vercel)`: dashboard, review queue, run logs, manual trigger.
- `Supabase`: authentication, PostgreSQL, row-level security.
- `Worker (Cloud Run / FastAPI)`: fetch, normalize, diff, alerts, outbox processing.
- `Cloud Scheduler`: `daily_refresh` at 07:00 KST and `poll` during 07:00-21:00 KST.

## Status Model

- `AUTO`: safe for automatic business-field updates.
- `AMBIGUOUS`: visible for manual decision; business fields are not updated.
- `NEEDS_REVIEW`: blocked for manual review; business fields are not updated.
- `FAILED`: execution failed and requires investigation/retry.

## Repository Layout

```text
kvca-automation/
  README.md
  .gitignore
  docs/
    ARCHITECTURE.md
    DATA_CONTRACT.md
    DECISIONS.md
    RUNBOOK.md
    TASKS.md
```

## Core Operating Schedule (KST)

- `07:00`: daily refresh.
- `07:00-21:00`: polling every 10-15 minutes.
- Manual trigger: available from dashboard when not locked.

## Documents

- Architecture: `docs/ARCHITECTURE.md`
- Data Contract: `docs/DATA_CONTRACT.md`
- Decisions: `docs/DECISIONS.md`
- Runbook: `docs/RUNBOOK.md`
- Delivery Plan: `docs/TASKS.md`
