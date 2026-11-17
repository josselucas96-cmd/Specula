-- ============================================================================
-- Anonymous visit counter (2026-11-17)
-- ----------------------------------------------------------------------------
-- site_visits: one row per page and per Streamlit session, written by
-- utils/visits.py. No IP address, no account: only the page, an optional
-- source tag from the link (?src=linkedin), an anonymous browser id (salted
-- hash of a site cookie) and a random session id.
-- site_visit_owners: browsers marked as the owner's (opened once with ?me=1);
-- scripts/visits_report.py leaves their visits out.
-- The anon key may insert and read these two tables only. Length checks keep
-- abusive rows small.
-- ============================================================================

create table if not exists public.site_visits (
  id       bigint generated always as identity primary key,
  ts       timestamptz not null default now(),
  page     text not null check (char_length(page) <= 40),
  src      text check (src is null or char_length(src) <= 40),
  visitor  text check (visitor is null or char_length(visitor) <= 32),
  session  text not null check (char_length(session) <= 40)
);
create index if not exists site_visits_ts_idx on public.site_visits (ts desc);

create table if not exists public.site_visit_owners (
  visitor  text primary key check (char_length(visitor) <= 32),
  added    timestamptz not null default now()
);

alter table public.site_visits       enable row level security;
alter table public.site_visit_owners enable row level security;

create policy visits_insert on public.site_visits       for insert to anon, authenticated with check (true);
create policy visits_read   on public.site_visits       for select to anon, authenticated using (true);
create policy owners_insert on public.site_visit_owners for insert to anon, authenticated with check (true);
create policy owners_read   on public.site_visit_owners for select to anon, authenticated using (true);
