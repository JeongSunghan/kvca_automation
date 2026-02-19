# Runbook

## Daily Operations (KST)

- 07:00: `daily_refresh` runs automatically.
- 07:00-21:00: `poll` runs automatically every configured interval.
- Manual run: available from dashboard when `job_lock` is free.

## Operator Checklist

1. Dashboard Alerts: inspect high-severity alerts first.
2. Review Queue: resolve `NEEDS_REVIEW` and `AMBIGUOUS` records.
3. Run Logs: confirm latest run success and duration.

## Failure Scenarios

### 1) Fetch Failure (`FAILED`)

- Check `run_log.error`.
- Retry with manual run.
- If repeated, verify API auth, endpoint, and response schema changes.

### 2) Sheet Sync Failure (`SHEET_FAILED`)

- Check `sheet_outbox.last_error`.
- Allow automatic retry or trigger a new manual run.

### 3) Notification Failure (`NOTI_FAILED`)

- Check `notification_outbox.last_error`.
- Verify template ID, credentials, and provider availability.

## Escalation Rule

- If `poll` fails continuously for more than the agreed threshold, switch to manual-only mode and notify admin owners.
