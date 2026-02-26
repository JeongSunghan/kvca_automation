-- KVCA automation final check SQL bundle
-- Run in Supabase SQL Editor after Step1~Step4 execution.

-- 1) Recent runs
select
  id,
  job_name,
  trigger_type,
  status,
  total_records,
  changed_records,
  created_alerts,
  error_message,
  started_at,
  finished_at,
  updated_at
from run_log
order by id desc
limit 30;

-- 2) Alert type/severity distribution (24h)
select
  alert_type,
  severity,
  count(*) as cnt
from alert
where created_at >= now() - interval '24 hours'
group by 1, 2
order by 1, 2;

-- 3) Failed alert groups (24h)
select
  detail->>'error_group' as error_group,
  detail->>'http_status_code' as http_status_code,
  severity,
  count(*) as cnt
from alert
where alert_type = 'FAILED'
  and created_at >= now() - interval '24 hours'
group by 1, 2, 3
order by cnt desc, error_group;

-- 4) sheet_outbox status
select status, count(*) as cnt
from sheet_outbox
group by 1
order by 1;

-- 5) notification_outbox status
select status, count(*) as cnt
from notification_outbox
group by 1
order by 1;

-- 6) sheet_outbox failures
select
  id, source_id, row_key, status, retry_count, last_error, next_retry_at, updated_at
from sheet_outbox
where status = 'FAILED'
order by updated_at desc
limit 50;

-- 7) notification_outbox failures
select
  id, source_id, channel, template_code, recipient,
  status, retry_count, last_error, next_retry_at, updated_at
from notification_outbox
where status = 'FAILED'
order by updated_at desc
limit 50;

-- 8) Key pattern check: enrolment_status should be term:course:user
select
  source_id, category_id, course_id, user_id, updated_at
from source_record
where source_type = 'enrolment_status'
  and source_id !~ '^[0-9]+:[0-9]+:.+$'
order by updated_at desc
limit 100;

-- 9) Key pattern check: enrolment_user_detail should be term:user
select
  source_id, category_id, user_id, updated_at
from source_record
where source_type = 'enrolment_user_detail'
  and source_id !~ '^[0-9]+:.+$'
order by updated_at desc
limit 100;

-- 10) Optional: rows for specific term
-- replace 303 with target term/category
select
  source_type,
  source_id,
  category_id,
  course_id,
  user_id,
  status,
  status_msg,
  update_time,
  updated_at
from source_record
where category_id = 303
order by updated_at desc
limit 200;
