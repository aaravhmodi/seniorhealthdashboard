-- Scheduled one-time patient reminders. Run after 001_carepath_schema.sql.
create table if not exists carepath.reminder_jobs (
  id            text primary key,
  senior_id     text not null,
  scheduled_for timestamptz not null,
  status        text not null,
  data          jsonb not null
);
create index if not exists reminder_jobs_due
  on carepath.reminder_jobs (scheduled_for) where status = 'scheduled';

alter table carepath.reminder_jobs enable row level security;
revoke all on carepath.reminder_jobs from anon, authenticated;
grant all on carepath.reminder_jobs to service_role;
