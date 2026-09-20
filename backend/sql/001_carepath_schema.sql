-- Carepath persistence. Run once in the Supabase SQL editor.
--
-- Why jsonb rather than a column per field: CONTRACT.md says additive changes
-- to the schemas are fine mid-hack, and they have happened four times already.
-- A column-per-field table turns every new optional pydantic field into a
-- migration at 3am. The columns that are broken out are exactly the ones we
-- filter or sort on; everything else rides in `data`, and the pydantic model
-- stays the single definition of shape.
--
-- Row Level Security is ON with no public policy, so the anon key cannot read
-- any of this. The backend uses the service-role key, which bypasses RLS, and
-- that key never reaches the browser.

create schema if not exists carepath;

-- --------------------------------------------------------------------------
-- People and their history
-- --------------------------------------------------------------------------
create table if not exists carepath.seniors (
  id          text primary key,
  updated_at  timestamptz not null default now(),
  data        jsonb not null
);

create table if not exists carepath.checkins (
  id          text primary key,
  senior_id   text not null,
  created_at  timestamptz not null,
  data        jsonb not null
);
create index if not exists checkins_senior_created
  on carepath.checkins (senior_id, created_at desc);

create table if not exists carepath.evaluations (
  id          text primary key,
  senior_id   text not null,
  checkin_id  text,
  created_at  timestamptz not null,
  data        jsonb not null
);
create index if not exists evaluations_senior_created
  on carepath.evaluations (senior_id, created_at desc);
create index if not exists evaluations_checkin
  on carepath.evaluations (checkin_id);

-- The timeline has no natural id of its own, so it gets a synthetic one the
-- writer generates; that keeps the upsert idempotent across a replay.
create table if not exists carepath.timeline (
  id          text primary key,
  senior_id   text not null,
  at          timestamptz not null,
  data        jsonb not null
);
create index if not exists timeline_senior_at
  on carepath.timeline (senior_id, at desc);

-- --------------------------------------------------------------------------
-- The family side -- the state that must outlive a restart
-- --------------------------------------------------------------------------
create table if not exists carepath.circles (
  senior_id   text primary key,
  chat_id     text,
  updated_at  timestamptz not null default now(),
  data        jsonb not null
);
create index if not exists circles_chat on carepath.circles (chat_id);

-- An alert with an escalation clock still running is the single most important
-- row here: if this is lost on a restart, a family that never answered is
-- never escalated to, and nobody finds out.
create table if not exists carepath.alerts (
  id          text primary key,
  senior_id   text not null,
  created_at  timestamptz not null,
  resolved    boolean not null default false,
  data        jsonb not null
);
create index if not exists alerts_open
  on carepath.alerts (senior_id, created_at desc) where not resolved;

create table if not exists carepath.followups (
  id          text primary key,
  senior_id   text not null,
  due_at      timestamptz not null,
  status      text not null,
  data        jsonb not null
);
create index if not exists followups_due
  on carepath.followups (due_at) where status = 'scheduled';

create table if not exists carepath.care_plans (
  senior_id   text primary key,
  updated_at  timestamptz not null default now(),
  data        jsonb not null
);

create table if not exists carepath.teachbacks (
  id          text primary key,
  senior_id   text not null,
  at          timestamptz not null,
  data        jsonb not null
);
create index if not exists teachbacks_senior_at
  on carepath.teachbacks (senior_id, at desc);

create table if not exists carepath.reminder_jobs (
  id            text primary key,
  senior_id     text not null,
  scheduled_for timestamptz not null,
  status        text not null,
  data          jsonb not null
);
create index if not exists reminder_jobs_due
  on carepath.reminder_jobs (scheduled_for) where status = 'scheduled';

-- --------------------------------------------------------------------------
-- Lock it down
-- --------------------------------------------------------------------------
-- Every table: RLS on, no policy granted. The anon and authenticated keys can
-- therefore read nothing. Only the service-role key (backend only) gets in.
-- This is deliberate: the caregiver link is public by design, so the data
-- behind it must never be readable straight from the browser.
do $$
declare t text;
begin
  foreach t in array array[
    'seniors', 'checkins', 'evaluations', 'timeline',
    'circles', 'alerts', 'followups', 'care_plans', 'teachbacks', 'reminder_jobs'
  ] loop
    execute format('alter table carepath.%I enable row level security', t);
    execute format('revoke all on carepath.%I from anon, authenticated', t);
  end loop;
end $$;

-- PostgREST needs the schema exposed to serve it. Add "carepath" to
-- Settings > API > Exposed schemas in the Supabase dashboard, or:
grant usage on schema carepath to service_role;
grant all on all tables in schema carepath to service_role;
