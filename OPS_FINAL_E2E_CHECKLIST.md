# 최종 통합 점검 체크리스트 (Step1~Step4 한 번에)

이 문서는 구현 완료 후 한 번에 검증할 때 사용합니다.

## A. 사전 준비

1. 워커 실행 확인
   - `GET /health` -> `{"status":"ok"}`
2. 저장소 확인
   - `GET /storage` -> `{"storage":"SupabaseStorage"}`
3. `.env` 확인
   - `KVCA_BASE_URL`는 도메인 루트
   - `KVCA_ADMIN_USER_ID`, `KVCA_ADMIN_USER_PASSWORD` 유효
   - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` 유효
   - outbox 관련 값 입력 확인

## B. 실행 순서

### 1) 통합 호출 (권장)

```powershell
$body = @{
  category_id = 303
  trigger_type = "MANUAL"
  max_categories = 1
  max_users_per_course = 500
  sheet_batch_size = 50
  notification_batch_size = 50
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:18080/jobs/ops/final-check" `
  -ContentType "application/json" `
  -Body $body
```

### 2) 분리 호출 (필요시)

1. `POST /jobs/enrolment-sync`
2. `POST /jobs/outbox/dispatch`

## C. 합격 기준 (Pass Criteria)

1. `run_log` 최신 1건이 `SUCCESS`.
2. `source_record`, `snapshot`가 증가.
3. 변경이 있으면 `alert`에 `NEW` 또는 `CHANGED` 생성.
4. 실패 시 `alert_type=FAILED`가 생성되고 `detail.error_group`이 채워짐.
5. `sheet_outbox`와 `notification_outbox`가 `PENDING -> SENT` 또는 재시도 상태로 이동.
6. `enrolment_status` source_id가 `termId:courseId:userId` 패턴으로 적재됨.

## D. 실패 시 1차 분류

1. `run_log.error_message`에 `409`:
   - lock 충돌 또는 upstream 409인지 `FAILED` 알림 detail에서 확인
2. `401`:
   - `KVCA_BASE_URL`, 계정/비밀번호 확인
3. `404`:
   - Supabase 마이그레이션/테이블 존재 여부 확인
4. outbox `FAILED`:
   - webhook URL, 외부 채널 응답코드, `last_error` 확인

## E. SQL 점검

- SQL 묶음 파일:
  - `supabase/sql/ops_final_check.sql`

순서:
1. `run_log` 최근 상태
2. `alert` 타입/등급/실패그룹
3. outbox 상태/실패 상세
4. 키 패턴 점검

## F. 운영 권장 루틴

1. 스케줄러: `POST /jobs/enrolment-sync` (`trigger_type="SCHEDULER"`)
2. 별도 스케줄러: `POST /jobs/outbox/dispatch`
3. 장애 대응: `trigger_type="RETRY"`로 수동 재실행
4. 매일 1회 SQL 대시보드 확인
