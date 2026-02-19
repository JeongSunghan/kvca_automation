# 수강신청 관리 API 명세 (상태 중심) - Draft v1

## 1. 범위

이 문서는 `수강신청 관리` 화면에서 실제 상태 조회/변경에 필요한 API만 정리현황 입니다.
메뉴 로딩 같은 주변 API는 제외하고, 상태 흐름에 직접 연결되는 호출만 다룹니다.

## 2. 공통 특성

- 인증: `Authorization: Bearer <JWT>`
- 메서드: 주로 `POST`
- Content-Type: `application/json`
- 공통 래퍼(`code/message/data`) 없이 결과를 바로 반환
- 엔드포인트별 응답 루트가 다름
  - Root Array
  - Root Object
  - Dynamic-key Object

## 3. 상태 조회 플로우 (확정)

1. 카테고리(필터) 목록 조회
2. 선택된 `categoryid`로 코스 조회(`courseid` 확보)
3. `courseid`로 수강 상태 목록 조회
4. `termId + userId`로 개별 상세 조회

## 4. 엔드포인트

## 4.1 `POST /api/category/list`

- 요청 본문:

```json
{ "categoryid": "all" }
```

- 응답 형태: Root Array
- 용도: 분류/구분/과정/기수 선택 후보 조회

## 4.2 `POST /api/course/category/course`

- 요청 본문 예시:

```json
{ "categoryid": 285 }
```

- 응답 형태: Root Object (동적 키)
- 용도: 선택된 카테고리에서 `courseid` 확인

필터 매핑 특징:
- 화면 필터(`분류/구분/과정/기수`)를 개별 파라미터로 보내지 않음
- 필터 조합을 단일 `categoryid`로 치환해서 보냄
- 관측 예시:
  - 조건 A -> `categoryid: 285` -> `courseid: 447`
  - 기수만 변경 -> `categoryid: 280` (동일 `courseid: 447` 관측)

## 4.3 `POST /api/course/classStatusAll`

- 요청 본문 예시:

```json
{ "courseid": 447 }
```

- 응답 형태: Root Array
- 아이템 구조:
  - `user`
  - `classStatus`
- 용도: 수강신청 인원/상태 메인 목록

페이징 특징:
- 1페이지 -> 2페이지 이동 시 추가 API 호출 없음
- 클라이언트 페이징(초기 일괄 로드 후 프론트에서 슬라이싱)

## 4.4 `POST /api/enrolment/getEnrolmentUserInfo`

- 요청 본문 예시:

```json
{ "termId": 285, "userId": "masked@example.com" }
```

- 응답 형태: Root Object (단건)
- 관측 상태코드: `200 OK`
- 용도: 선택 사용자 상세 조회

연결 키:
- 목록 <-> 상세 연결은 `termId + userId`로 확정

## 4.5 `POST /api/course/updateClassStatus`

- 요청 본문 예시 (결제완료):

```json
{ "courseid": 455, "userid": "2728", "status": "GC" }
```

- 요청 본문 예시 (승인준비완료로 전이):

```json
{ "courseid": 455, "userid": "2728", "status": "SJC" }
```

- 관측 상태코드: `200 OK`
- 응답 형태: `user` + `classStatus` 객체
- 용도: 상태 변경(write)

## 5. 상태 전이 규칙 (확정)

관측 전이:
- `DS` (대기순번)
- `GC` (결제완료)
- `SJC` (승인준비완료)

필드 전이 특징:
- `status`, `statusmsg`, `codename`: 상태 전이에 따라 변경
- `update_time`: 상태 변경 시마다 갱신
- `ds_date`: 초기 대기 등록 시점 유지
- `sjc_date`: `SJC` 진입 시 갱신
- `gc_date`: `GC -> SJC` 롤백 이후에도 기존 값 보존
- `user` 객체는 상태 변경 과정에서 실질적으로 동일

운영 시사점:
- 단순 현재 상태 비교만으로는 부족
- 상태 코드 + 단계별 타임스탬프를 함께 diff 해야 정확함

## 6. 수집/저장 규칙 (초안)

- `source_type`
  - `enrolment_status`
  - `enrolment_user_detail`
- `source_id` 후보
  - `termId:userId`
- 추적 필드
  - `categoryid`, `courseid` 모두 저장

민감정보 처리:
- 저장 제외/마스킹 대상
  - `userPassword`
  - `juminNumber`
  - 운영에 불필요한 직접 식별 PII

## 7. 실패 처리 기준

현재 UI에서 의도적 에러 재현은 불가하여 샘플 미수집.

대체 정책:
- 재시도 대상
  - timeout
  - connection error
  - DNS/TLS error
  - HTTP `5xx`
  - HTTP `429`
- 기본 미재시도
  - 기타 HTTP `4xx`
- 응답 스키마 파싱 실패
  - `FAILED` 처리 + 스키마 점검 알림
