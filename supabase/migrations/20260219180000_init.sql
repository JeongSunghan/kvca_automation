-- KVCA automation initial schema (Supabase/Postgres)

begin;

-- 1) Types
do $$
begin
  create type public.review_status_t as enum ('AUTO', 'AMBIGUOUS', 'NEEDS_REVIEW');
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.alert_type_t as enum (
    'NEW',
    'CHANGED',
    'AMBIGUOUS',
    'NEEDS_REVIEW',
    'FAILED',
    'SHEET_FAILED',
    'NOTI_FAILED'
  );
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.run_status_t as enum ('RUNNING', 'SUCCESS', 'FAILED');
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.trigger_type_t as enum ('SCHEDULER', 'MANUAL', 'RETRY');
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.outbox_status_t as enum ('PENDING', 'PROCESSING', 'SENT', 'FAILED');
exception
  when duplicate_object then null;
end $$;

-- 2) Shared helpers
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create or replace function public.jwt_app_role()
returns text
language sql
stable
as $$
  select coalesce(auth.jwt() -> 'app_metadata' ->> 'role', '');
$$;

create or replace function public.is_admin_or_staff_or_service()
returns boolean
language sql
stable
as $$
  select auth.role() = 'service_role'
     or public.jwt_app_role() in ('ADMIN', 'STAFF');
$$;

create or replace function public.is_admin_or_service()
returns boolean
language sql
stable
as $$
  select auth.role() = 'service_role'
     or public.jwt_app_role() = 'ADMIN';
$$;

-- 3) Core tables
create table if not exists public.source_record (
  id bigint generated always as identity primary key,
  source_type text not null,
  source_id text not null,
  category_id integer,
  course_id integer,
  term_id integer,
  user_id text,
  user_name text,
  company_name text,
  dept_name text,
  job_position text,
  status text,
  status_msg text,
  code_name text,
  ds_date timestamptz,
  gc_date timestamptz,
  sjc_date timestamptz,
  update_time timestamptz,
  payload jsonb not null default '{}'::jsonb,
  payload_hash text,
  last_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_type, source_id)
);

create table if not exists public.snapshot (
  id bigint generated always as identity primary key,
  source_type text not null,
  source_id text not null,
  snapshot_hash text not null,
  payload jsonb not null,
  captured_at timestamptz not null default now()
);

create table if not exists public.alert (
  id bigint generated always as identity primary key,
  source_type text not null,
  source_id text not null,
  alert_type public.alert_type_t not null,
  severity text not null default 'medium' check (severity in ('low', 'medium', 'high')),
  title text,
  message text,
  detail jsonb not null default '{}'::jsonb,
  review_status public.review_status_t not null default 'AUTO',
  resolved boolean not null default false,
  resolved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.run_log (
  id bigint generated always as identity primary key,
  job_name text not null,
  trigger_type public.trigger_type_t not null default 'SCHEDULER',
  status public.run_status_t not null default 'RUNNING',
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  duration_ms integer,
  total_records integer not null default 0,
  changed_records integer not null default 0,
  created_alerts integer not null default 0,
  retry_count integer not null default 0,
  error_message text,
  error_detail jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.job_lock (
  job_name text primary key,
  locked_by text not null,
  locked_at timestamptz not null default now(),
  lock_expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.sheet_outbox (
  id bigint generated always as identity primary key,
  source_type text not null,
  source_id text not null,
  row_key text not null,
  payload jsonb not null default '{}'::jsonb,
  status public.outbox_status_t not null default 'PENDING',
  retry_count integer not null default 0,
  last_error text,
  last_attempt_at timestamptz,
  next_retry_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.notification_outbox (
  id bigint generated always as identity primary key,
  source_type text not null,
  source_id text not null,
  channel text not null default 'KAKAO_ALIMTALK',
  template_code text not null,
  recipient text not null,
  payload jsonb not null default '{}'::jsonb,
  status public.outbox_status_t not null default 'PENDING',
  retry_count integer not null default 0,
  last_error text,
  last_attempt_at timestamptz,
  next_retry_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.review_audit (
  id bigint generated always as identity primary key,
  alert_id bigint not null references public.alert(id) on delete cascade,
  action text not null check (action in ('APPROVE', 'HOLD', 'RESOLVE', 'REOPEN')),
  acted_by text not null,
  note text,
  created_at timestamptz not null default now()
);

-- 4) Indexes
create index if not exists idx_source_record_term_user on public.source_record (term_id, user_id);
create index if not exists idx_source_record_status on public.source_record (status);
create index if not exists idx_source_record_update_time on public.source_record (update_time desc);

create index if not exists idx_snapshot_lookup on public.snapshot (source_type, source_id, captured_at desc);
create index if not exists idx_alert_review on public.alert (review_status, resolved, created_at desc);
create index if not exists idx_run_log_job_started on public.run_log (job_name, started_at desc);
create index if not exists idx_sheet_outbox_pending on public.sheet_outbox (status, next_retry_at);
create index if not exists idx_noti_outbox_pending on public.notification_outbox (status, next_retry_at);

-- 5) updated_at triggers
drop trigger if exists trg_source_record_updated_at on public.source_record;
create trigger trg_source_record_updated_at
before update on public.source_record
for each row execute function public.set_updated_at();

drop trigger if exists trg_alert_updated_at on public.alert;
create trigger trg_alert_updated_at
before update on public.alert
for each row execute function public.set_updated_at();

drop trigger if exists trg_run_log_updated_at on public.run_log;
create trigger trg_run_log_updated_at
before update on public.run_log
for each row execute function public.set_updated_at();

drop trigger if exists trg_job_lock_updated_at on public.job_lock;
create trigger trg_job_lock_updated_at
before update on public.job_lock
for each row execute function public.set_updated_at();

drop trigger if exists trg_sheet_outbox_updated_at on public.sheet_outbox;
create trigger trg_sheet_outbox_updated_at
before update on public.sheet_outbox
for each row execute function public.set_updated_at();

drop trigger if exists trg_notification_outbox_updated_at on public.notification_outbox;
create trigger trg_notification_outbox_updated_at
before update on public.notification_outbox
for each row execute function public.set_updated_at();

-- 6) RLS
alter table public.source_record enable row level security;
alter table public.snapshot enable row level security;
alter table public.alert enable row level security;
alter table public.run_log enable row level security;
alter table public.job_lock enable row level security;
alter table public.sheet_outbox enable row level security;
alter table public.notification_outbox enable row level security;
alter table public.review_audit enable row level security;

drop policy if exists source_record_select_policy on public.source_record;
create policy source_record_select_policy
on public.source_record
for select
using (public.is_admin_or_staff_or_service());

drop policy if exists source_record_insert_policy on public.source_record;
create policy source_record_insert_policy
on public.source_record
for insert
with check (public.is_admin_or_service());

drop policy if exists source_record_update_policy on public.source_record;
create policy source_record_update_policy
on public.source_record
for update
using (public.is_admin_or_service())
with check (public.is_admin_or_service());

drop policy if exists source_record_delete_policy on public.source_record;
create policy source_record_delete_policy
on public.source_record
for delete
using (public.is_admin_or_service());

drop policy if exists snapshot_select_policy on public.snapshot;
create policy snapshot_select_policy
on public.snapshot
for select
using (public.is_admin_or_staff_or_service());

drop policy if exists snapshot_insert_policy on public.snapshot;
create policy snapshot_insert_policy
on public.snapshot
for insert
with check (public.is_admin_or_service());

drop policy if exists snapshot_update_policy on public.snapshot;
create policy snapshot_update_policy
on public.snapshot
for update
using (public.is_admin_or_service())
with check (public.is_admin_or_service());

drop policy if exists snapshot_delete_policy on public.snapshot;
create policy snapshot_delete_policy
on public.snapshot
for delete
using (public.is_admin_or_service());

drop policy if exists alert_select_policy on public.alert;
create policy alert_select_policy
on public.alert
for select
using (public.is_admin_or_staff_or_service());

drop policy if exists alert_insert_policy on public.alert;
create policy alert_insert_policy
on public.alert
for insert
with check (public.is_admin_or_service());

drop policy if exists alert_update_policy on public.alert;
create policy alert_update_policy
on public.alert
for update
using (public.is_admin_or_service())
with check (public.is_admin_or_service());

drop policy if exists alert_delete_policy on public.alert;
create policy alert_delete_policy
on public.alert
for delete
using (public.is_admin_or_service());

drop policy if exists run_log_select_policy on public.run_log;
create policy run_log_select_policy
on public.run_log
for select
using (public.is_admin_or_staff_or_service());

drop policy if exists run_log_insert_policy on public.run_log;
create policy run_log_insert_policy
on public.run_log
for insert
with check (public.is_admin_or_service());

drop policy if exists run_log_update_policy on public.run_log;
create policy run_log_update_policy
on public.run_log
for update
using (public.is_admin_or_service())
with check (public.is_admin_or_service());

drop policy if exists run_log_delete_policy on public.run_log;
create policy run_log_delete_policy
on public.run_log
for delete
using (public.is_admin_or_service());

drop policy if exists job_lock_select_policy on public.job_lock;
create policy job_lock_select_policy
on public.job_lock
for select
using (public.is_admin_or_staff_or_service());

drop policy if exists job_lock_insert_policy on public.job_lock;
create policy job_lock_insert_policy
on public.job_lock
for insert
with check (public.is_admin_or_service());

drop policy if exists job_lock_update_policy on public.job_lock;
create policy job_lock_update_policy
on public.job_lock
for update
using (public.is_admin_or_service())
with check (public.is_admin_or_service());

drop policy if exists job_lock_delete_policy on public.job_lock;
create policy job_lock_delete_policy
on public.job_lock
for delete
using (public.is_admin_or_service());

drop policy if exists sheet_outbox_select_policy on public.sheet_outbox;
create policy sheet_outbox_select_policy
on public.sheet_outbox
for select
using (public.is_admin_or_staff_or_service());

drop policy if exists sheet_outbox_insert_policy on public.sheet_outbox;
create policy sheet_outbox_insert_policy
on public.sheet_outbox
for insert
with check (public.is_admin_or_service());

drop policy if exists sheet_outbox_update_policy on public.sheet_outbox;
create policy sheet_outbox_update_policy
on public.sheet_outbox
for update
using (public.is_admin_or_service())
with check (public.is_admin_or_service());

drop policy if exists sheet_outbox_delete_policy on public.sheet_outbox;
create policy sheet_outbox_delete_policy
on public.sheet_outbox
for delete
using (public.is_admin_or_service());

drop policy if exists notification_outbox_select_policy on public.notification_outbox;
create policy notification_outbox_select_policy
on public.notification_outbox
for select
using (public.is_admin_or_staff_or_service());

drop policy if exists notification_outbox_insert_policy on public.notification_outbox;
create policy notification_outbox_insert_policy
on public.notification_outbox
for insert
with check (public.is_admin_or_service());

drop policy if exists notification_outbox_update_policy on public.notification_outbox;
create policy notification_outbox_update_policy
on public.notification_outbox
for update
using (public.is_admin_or_service())
with check (public.is_admin_or_service());

drop policy if exists notification_outbox_delete_policy on public.notification_outbox;
create policy notification_outbox_delete_policy
on public.notification_outbox
for delete
using (public.is_admin_or_service());

drop policy if exists review_audit_select_policy on public.review_audit;
create policy review_audit_select_policy
on public.review_audit
for select
using (public.is_admin_or_staff_or_service());

drop policy if exists review_audit_insert_policy on public.review_audit;
create policy review_audit_insert_policy
on public.review_audit
for insert
with check (public.is_admin_or_service());

drop policy if exists review_audit_delete_policy on public.review_audit;
create policy review_audit_delete_policy
on public.review_audit
for delete
using (public.is_admin_or_service());

commit;
