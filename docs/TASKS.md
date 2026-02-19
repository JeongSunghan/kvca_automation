# KVCA 자동업무 - 작업 목록 (최종)

## M0 - 소스/API 기준 확정

- [ ] 관리자 3개 탭 API 스펙 정리 (엔드포인트, 인증, 파라미터, 안정 고유키)
- [ ] 검증용 샘플 데이터 최소 30건 확보 (JSON/CSV)

## M1 - Supabase + Next.js 기반 구축

- [ ] Supabase 프로젝트 생성 (Auth + DB)
- [ ] 테이블 생성
  - `source_record`
  - `snapshot`
  - `alert`
  - `run_log`
  - `job_lock`
  - `sheet_outbox`
  - `notification_outbox`
- [ ] 최소 RLS 정책 적용 (`ADMIN`, `STAFF`)
- [ ] Next.js 로그인/대시보드 기본 화면 구현

## M2 - Worker 수집

- [ ] FastAPI Worker 프로젝트 생성 + Dockerfile 구성
- [ ] `registration`, `payment`, `invoice` 수집 후 `source_record` 업서트
- [ ] 실행 로그 기록 + 실패 시 재시도(최소 2회)

## M3 - Diff/Alert + Review UI

- [ ] 스냅샷 해시 기반 diff 생성
- [ ] 알림 규칙 구현 (`NEW`, `CHANGED`, `AMBIGUOUS`, `NEEDS_REVIEW`, `FAILED`)
- [ ] `/review` 승인/보류 액션 + 감사 이력(audit) 기록

## M4 - Google Sheets 반영

- [ ] `sheet_outbox` 적재 후 Sheets 멱등 반영
- [ ] `SHEET_FAILED` 재시도/알림 처리

## M5 - 카카오 알림톡

- [ ] `notification_outbox` 적재 후 알림톡 발송
- [ ] 템플릿 3종 준비
  - 아침 요약
  - 중요 즉시 알림
  - (선택) 마감 요약
- [ ] `NOTI_FAILED` 재시도/알림 처리

## M6 - 배포 및 운영

- [ ] Worker를 Cloud Run에 배포
- [ ] Scheduler 설정
  - `07:00` daily refresh
  - `07:00-21:00` 10~15분 간격 poll
  - manual endpoint trigger
- [ ] 운영 문서 최종화 (`RUNBOOK`, `DATA_CONTRACT`, `ARCHITECTURE`)
