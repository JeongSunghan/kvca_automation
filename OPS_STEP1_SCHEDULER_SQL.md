# Step 1 - 스케줄러 호출 스펙 + 운영 점검 SQL

이 문서는 배포 직후 바로 운영에 붙이기 위한 최소 스펙입니다.

## 1) 스케줄러 호출 스펙

- Method: `POST`
- URL: `http://<worker-host>:<port>/jobs/enrolment-sync`
- Header: `Content-Type: application/json`
- Body (예시):

```json
{
  "category_id": 303,
  "trigger_type": "SCHEDULER",
  "max_categories": 1,
  "max_users_per_course": 500
}
```

필드 의미:
- `category_id` : 필수. 동기화할 대상 카테고리(기수) ID.
- `trigger_type` : `MANUAL | SCHEDULER | RETRY` (run_log 구분용).
- `max_categories` : `category_id` 지정 시 보통 `1` 유지.
- `max_users_per_course` : 과목당 최대 처리 인원 제한(운영 보호용).

## 2) PowerShell 수동 호출(스케줄러와 동일 요청)

```powershell
$body = @{
  category_id = 303
  trigger_type = "SCHEDULER"
  max_categories = 1
  max_users_per_course = 500
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:18080/jobs/enrolment-sync" `
  -ContentType "application/json" `
  -Body $body
```

성공 시 응답 예:

```json
{
  "ok": true,
  "summary": {
    "categories_processed": 1,
    "courses_processed": 1,
    "status_rows_processed": 89,
    "details_processed": 89,
    "source_records_upserted": 178,
    "new_records": 0,
    "changed_records": 1,
    "created_alerts": 1
  }
}
```

## 3) 운영 점검 SQL (Supabase SQL Editor)

### 3-1. 최근 실행 상태

```sql
select
  id, trigger_type, status,
  total_records, changed_records, created_alerts,
  error_message, started_at, finished_at, updated_at
from run_log
order by id desc
limit 20;
```

### 3-2. 실패 실행만 보기 (최신순)

```sql
select
  id, trigger_type, status, error_message, started_at, finished_at
from run_log
where status = 'FAILED'
order by id desc
limit 20;
```

### 3-3. 실패 유형 요약 (409/5xx/기타)

```sql
select
  case
    when error_message ilike '% 409 %' then 'HTTP_409'
    when error_message ilike '% 5__ %' then 'HTTP_5XX'
    when error_message ilike '%timeout%' then 'TIMEOUT'
    when error_message ilike '%job_lock active%' then 'LOCK_CONFLICT'
    else 'OTHER'
  end as error_group,
  count(*) as cnt
from run_log
where status = 'FAILED'
group by 1
order by 2 desc;
```

### 3-4. 최근 24시간 알림 타입/등급 분포

```sql
select
  alert_type,
  severity,
  count(*) as cnt
from alert
where created_at >= now() - interval '24 hours'
group by 1, 2
order by 1, 2;
```

### 3-5. 미해결 high 알림 확인

```sql
select
  id, alert_type, severity, source_id, message, created_at
from alert
where resolved = false
  and severity = 'high'
order by created_at desc
limit 50;
```

### 3-6. 특정 category 상태 샘플 확인

```sql
select
  source_id, user_id, course_id, status, status_msg, gc_date, sjc_date, update_time, updated_at
from source_record
where source_type = 'enrolment_status'
  and category_id = 303
order by updated_at desc
limit 100;
```

### 3-7. lock 잔존 확인

```sql
select job_name, locked_by, locked_at, lock_expires_at
from job_lock
order by locked_at desc;
```

## 4) 운영 루틴(권장)

1. 스케줄러로 `category_id` 명시 호출.
2. 호출 후 `run_log` 최근 1건 `SUCCESS` 확인.
3. `changed_records > 0`이면 `alert`에서 `CHANGED` 확인.
4. 실패 시 `run_log.error_message`로 원인 분류 후 재시도(`trigger_type = RETRY`).

## 5) 다음 스텝 연결

- Step 2: `FAILED/409/5xx`를 실제 알림 채널로 연결.
- Step 3: outbox(`sheet_outbox` -> `notification_outbox`) 소비 워커 구현.
- Step 4: 최종 키 설계 확정 (`termId:userId` vs `termId:courseId:userId`).
