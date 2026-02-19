# Data Contract (Google Sheets)

## Principles

- Idempotent sync: no duplicate logical rows.
- One stable row key per source record.
- Database remains the system of record; Sheets is a projection.

## Row Key

```text
key = `${source_type}:${source_id}`
```

## MVP Columns

- `source_type`
- `source_id`
- `status`
- `amount`
- `requested_at`
- `updated_at`
- `alert_type` (latest)
- `review_status` (`AUTO` / `NEEDS_REVIEW` / `AMBIGUOUS`)
- `last_synced_at`

## Update Rules

- `AUTO`
  - Update business fields immediately.
  - Refresh `last_synced_at`.
- `AMBIGUOUS`, `NEEDS_REVIEW`
  - Do not change business fields.
  - Update `review_status` and last-seen metadata only.

## Sync Guarantees

- Upsert by stable key.
- Last-write wins by trusted event timestamp.
- Retries must remain safe to re-run.
