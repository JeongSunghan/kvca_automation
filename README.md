# KVCA 자동업무

KVCA 관리자페이지 데이터를 자동으로 모으고, 변경점을 잡아내고, 필요한 건 검토로 넘기고, Google Sheets/카카오 알림까지 연결하는 프로젝트입니다.

## 이 프로젝트에서 하는 일

- 관리자 페이지 3개 탭(`registration`, `payment`, `invoice`) 데이터를 주기적으로 수집
- 스냅샷 비교로 신규/변경 건 감지
- 건별 상태 분류(`AUTO`, `AMBIGUOUS`, `NEEDS_REVIEW`)
- DB를 기준으로 두고, 시트/알림은 안전하게 후처리

## 구성 한눈에 보기

- `Next.js (Vercel)`: 대시보드, 검토 화면, 실행 로그, 수동 실행
- `Supabase`: 로그인/Auth, PostgreSQL, RLS
- `Worker (Cloud Run / FastAPI)`: 수집, 정규화, diff, 알림/시트 큐 처리
- `Cloud Scheduler`: 매일/주기 실행 스케줄

## 상태 값(간단 설명)

- `AUTO`: 자동 반영 가능
- `AMBIGUOUS`: 애매해서 사람 확인 필요
- `NEEDS_REVIEW`: 검토 완료 전까지 반영 보류
- `FAILED`: 실행 실패, 재시도/원인 확인 필요

## 폴더 구조

```text
kvca-automation/
  README.md
  .gitignore
  docs/
    ARCHITECTURE.md
    API_SPEC.md
    DATA_CONTRACT.md
    DECISIONS.md
    RUNBOOK.md
    TASKS.md
```

## 기본 운영 시간 (KST)

- `07:00`: daily refresh
- `07:00-21:00`: 10~15분 간격 poll
- 수동 실행: lock이 없을 때 대시보드에서 실행 가능

## 문서 모음

- 아키텍처: `docs/ARCHITECTURE.md`
- API 스펙(초안): `docs/API_SPEC.md`
- 데이터 계약: `docs/DATA_CONTRACT.md`
- 결정 사항: `docs/DECISIONS.md`
- 운영 가이드: `docs/RUNBOOK.md`
- 작업 계획: `docs/TASKS.md`
