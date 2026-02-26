# Step 2 - 실패 알림 연결(run_log FAILED / 409 / 5xx)

이번 단계에서 반영된 목표:
- sync 실패 시 `run_log.status = FAILED` 기록
- 실패 원인 분류(409/5xx/timeout/lock conflict 등)
- 실패 알림을 `alert` 테이블(`alert_type = FAILED`)로 자동 생성
- 반복 실패 알림은 기존 쿨다운(`ALERT_COOLDOWN_MINUTES`)으로 중복 억제

## 1) 실패 분류 규칙

- `LOCK_CONFLICT`: `job_lock active` (API 응답 409 케이스)
- `HTTP_409`: upstream HTTP 409
- `HTTP_5XX`: upstream HTTP 5xx
- `HTTP_4XX`: upstream HTTP 4xx(409 제외)
- `TIMEOUT`: timeout/ timed out 문자열 포함
- `UNKNOWN`: 위 조건 외

## 2) severity 규칙

- `HTTP_5XX`, `TIMEOUT` -> `high`
- `HTTP_409`, `HTTP_4XX` -> `medium`
- `LOCK_CONFLICT` -> `low`
- `UNKNOWN` -> `medium`

## 3) 저장 포맷

`alert` row:
- `source_type`: `run_log`
- `source_id`: `<job_name>:<error_group>` (예: `enrolment_sync:HTTP_409`)
- `alert_type`: `FAILED`
- `detail.error_group`, `detail.http_status_code`, `detail.run_id`, `detail.error_message` 포함

`run_log` row:
- `status`: `FAILED`
- `error_message`: 실패 원문
- `created_alerts`: 실패 알림 생성분 포함

## 4) 운영 점검 SQL

### 4-1. 최근 실패 실행

```sql
select
  id, trigger_type, status, created_alerts, error_message, started_at, finished_at
from run_log
where status = 'FAILED'
order by id desc
limit 30;
```

### 4-2. 실패 알림 목록

```sql
select
  id, source_id, alert_type, severity,
  detail->>'error_group' as error_group,
  detail->>'http_status_code' as http_status_code,
  detail->>'run_id' as run_id,
  created_at
from alert
where alert_type = 'FAILED'
order by id desc
limit 50;
```

### 4-3. 실패 그룹별 집계(24시간)

```sql
select
  detail->>'error_group' as error_group,
  severity,
  count(*) as cnt
from alert
where alert_type = 'FAILED'
  and created_at >= now() - interval '24 hours'
group by 1, 2
order by 3 desc, 1;
```

### 4-4. run_log와 실패 알림 교차 확인

```sql
select
  r.id as run_id,
  r.status,
  r.error_message,
  a.id as alert_id,
  a.severity,
  a.detail->>'error_group' as error_group,
  a.created_at as alert_created_at
from run_log r
left join alert a
  on a.alert_type = 'FAILED'
 and a.detail->>'run_id' = r.id::text
where r.status = 'FAILED'
order by r.id desc
limit 30;
```

## 5) 빠른 검증 시나리오

1. 정상 1회 실행 -> `run_log SUCCESS` 확인.
2. 의도적으로 실패 유도(예: 잘못된 KVCA 인증값, 또는 lock 충돌 상황) 후 실행.
3. `run_log FAILED` + `alert(FAILED)` 생성 확인.
4. 같은 실패를 짧은 시간 내 반복 실행해서 쿨다운으로 중복 억제되는지 확인.
