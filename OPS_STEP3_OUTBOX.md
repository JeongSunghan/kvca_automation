# Step 3 - Outbox 구현 (Sheets -> Kakao)

이번 단계 반영 범위:
- `alert` 생성 시 `sheet_outbox` 자동 적재
- `sheet_outbox` 전송 성공 시 `notification_outbox` 자동 적재
- Sheet/Kakao 각각 디스패치 잡 엔드포인트 제공
- 재시도(backoff), 쿨다운, 중복 방지(기본 row_key 기반) 적용

## 1) 처리 순서

1. 동기화 중 `alert` 생성 (`NEW`, `CHANGED`, `FAILED`)
2. 같은 alert payload를 `sheet_outbox(PENDING)`에 생성
3. `/jobs/outbox/sheet-dispatch` 실행
4. sheet 전송 성공 건을 `notification_outbox(PENDING)`에 생성
5. `/jobs/outbox/notification-dispatch` 실행
6. kakao 전송 성공 시 `SENT`

체인 호출은 `/jobs/outbox/dispatch` 하나로 가능.

## 2) 신규 API

### 2-1. Sheet 디스패치

- `POST /jobs/outbox/sheet-dispatch`
- Body:

```json
{
  "batch_size": 50
}
```

### 2-2. Notification(Kakao) 디스패치

- `POST /jobs/outbox/notification-dispatch`
- Body:

```json
{
  "batch_size": 50
}
```

### 2-3. 체인 디스패치 (Sheet -> Kakao)

- `POST /jobs/outbox/dispatch`
- Body:

```json
{
  "sheet_batch_size": 50,
  "notification_batch_size": 50
}
```

## 3) .env 항목

```dotenv
SHEET_DISPATCH_BATCH_SIZE=50
NOTI_DISPATCH_BATCH_SIZE=50
OUTBOX_RETRY_BASE_SECONDS=60
OUTBOX_RETRY_MAX_SECONDS=3600
SHEET_WEBHOOK_URL=
KAKAO_WEBHOOK_URL=
KAKAO_TEMPLATE_CODE=KVCA_ALERT
KAKAO_DEFAULT_RECIPIENT=ops
```

참고:
- `SHEET_WEBHOOK_URL`, `KAKAO_WEBHOOK_URL`이 비어있으면 외부 호출 없이 성공 처리(로컬/초기 운영용).

## 4) 운영 점검 SQL

### 4-1. sheet_outbox 상태

```sql
select status, count(*) as cnt
from sheet_outbox
group by 1
order by 1;
```

### 4-2. notification_outbox 상태

```sql
select status, count(*) as cnt
from notification_outbox
group by 1
order by 1;
```

### 4-3. sheet 실패 상세

```sql
select
  id, source_id, row_key, status, retry_count, last_error, next_retry_at, updated_at
from sheet_outbox
where status = 'FAILED'
order by updated_at desc
limit 50;
```

### 4-4. notification 실패 상세

```sql
select
  id, source_id, channel, template_code, recipient,
  status, retry_count, last_error, next_retry_at, updated_at
from notification_outbox
where status = 'FAILED'
order by updated_at desc
limit 50;
```

### 4-5. 최근 체인 전개 확인

```sql
select
  s.id as sheet_id,
  s.status as sheet_status,
  s.source_id as sheet_source_id,
  n.id as noti_id,
  n.status as noti_status,
  n.source_id as noti_source_id,
  s.updated_at as sheet_updated_at,
  n.updated_at as noti_updated_at
from sheet_outbox s
left join notification_outbox n
  on n.source_type = 'sheet_alert'
 and n.source_id = s.row_key
order by s.id desc
limit 100;
```

## 5) 빠른 실행 순서 (PowerShell)

```powershell
# 1) sync 실행
$sync = @{
  category_id = 303
  trigger_type = "MANUAL"
  max_categories = 1
  max_users_per_course = 500
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:18080/jobs/enrolment-sync" `
  -ContentType "application/json" `
  -Body $sync

# 2) outbox 체인 실행 (Sheet -> Kakao)
$outbox = @{
  sheet_batch_size = 50
  notification_batch_size = 50
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:18080/jobs/outbox/dispatch" `
  -ContentType "application/json" `
  -Body $outbox
```
