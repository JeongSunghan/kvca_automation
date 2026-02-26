# Step 4 - 최종 키 설계 확정

최종 결론:
- `enrolment_status` 키는 `termId:courseId:userId`
- `enrolment_user_detail` 키는 `termId:userId`

## 왜 이렇게 확정했는지

1. `enrolment_status`는 코스별 상태가 달라질 수 있어서 `termId:userId`만 쓰면 충돌 가능성이 있음.
2. `enrolment_user_detail`은 API 자체가 `termId + userId` 단위라서 코스 키를 넣을 실익이 적음.
3. 현재 테이블 unique가 `(source_type, source_id)`이므로 source_type별로 키 정책을 달리 가져가도 안전함.

## 코드 반영 위치

- `worker/app/sync_service.py`
  - `enrolment_status` 생성 시 `source_id = f\"{term_id}:{course_id}:{user_id}\"`
  - `enrolment_user_detail`는 기존대로 `source_id = f\"{term_id}:{user_id}\"`

## 운영 영향

- 기존 `enrolment_status`의 `termId:userId` 형태 레코드는 과거 데이터로 남을 수 있음.
- 신규 sync부터는 `termId:courseId:userId` 키로 적재됨.
- 리포트/조회 SQL에서 source_id 패턴이 혼재될 수 있으므로 아래 점검 SQL로 확인 권장.

## 점검 SQL

### 1) 신규 키 패턴 아닌 status 레코드 찾기

```sql
select source_id, category_id, course_id, user_id, updated_at
from source_record
where source_type = 'enrolment_status'
  and source_id !~ '^[0-9]+:[0-9]+:.+$'
order by updated_at desc
limit 100;
```

### 2) detail 키 패턴 점검

```sql
select source_id, category_id, user_id, updated_at
from source_record
where source_type = 'enrolment_user_detail'
  and source_id !~ '^[0-9]+:.+$'
order by updated_at desc
limit 100;
```

### 3) 같은 term/user에 다중 코스 존재 여부

```sql
select
  category_id as term_id,
  user_id,
  count(distinct course_id) as course_cnt
from source_record
where source_type = 'enrolment_status'
group by 1, 2
having count(distinct course_id) > 1
order by course_cnt desc, term_id desc
limit 100;
```
