create extension if not exists pgcrypto;

create table if not exists public.forensic_attempts (
    attempt_id uuid primary key default gen_random_uuid(),
    trace_id text not null,
    run_id text not null,
    platform text not null,
    engine text not null,
    profile_name text,
    campaign_id text,
    job_id text,
    session_id text,
    parent_attempt_id uuid references public.forensic_attempts (attempt_id) on delete set null,
    started_at timestamptz not null default now(),
    ended_at timestamptz,
    phase text not null default 'started',
    status text not null default 'running',
    final_verdict text,
    failure_class text,
    confidence double precision,
    metadata jsonb not null default '{}'::jsonb,
    evidence_summary jsonb not null default '{}'::jsonb
);

create table if not exists public.forensic_events (
    id bigint generated always as identity primary key,
    attempt_id uuid not null references public.forensic_attempts (attempt_id) on delete cascade,
    event_type text not null,
    phase text,
    source text,
    event_time timestamptz not null default now(),
    ordinal integer not null,
    payload jsonb not null default '{}'::jsonb
);

create table if not exists public.forensic_artifacts (
    artifact_id uuid primary key default gen_random_uuid(),
    attempt_id uuid not null references public.forensic_attempts (attempt_id) on delete cascade,
    artifact_type text not null,
    storage_bucket text not null,
    storage_path text not null unique,
    content_type text not null,
    size_bytes bigint not null,
    sha256 text not null,
    redaction_level text not null default 'light',
    captured_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.forensic_links (
    id bigint generated always as identity primary key,
    parent_attempt_id uuid not null references public.forensic_attempts (attempt_id) on delete cascade,
    child_attempt_id uuid not null references public.forensic_attempts (attempt_id) on delete cascade,
    link_type text not null,
    created_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.forensic_verdicts (
    attempt_id uuid primary key references public.forensic_attempts (attempt_id) on delete cascade,
    final_verdict text not null,
    confidence double precision not null default 0,
    winning_evidence jsonb not null default '[]'::jsonb,
    rejected_hypotheses jsonb not null default '[]'::jsonb,
    summary text not null,
    summary_payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_forensic_attempts_trace_id on public.forensic_attempts (trace_id);
create index if not exists idx_forensic_attempts_run_id on public.forensic_attempts (run_id);
create index if not exists idx_forensic_attempts_campaign_id on public.forensic_attempts (campaign_id);
create index if not exists idx_forensic_attempts_profile_name on public.forensic_attempts (profile_name);
create index if not exists idx_forensic_attempts_platform on public.forensic_attempts (platform);
create index if not exists idx_forensic_attempts_engine on public.forensic_attempts (engine);
create index if not exists idx_forensic_attempts_started_at on public.forensic_attempts (started_at desc);
create index if not exists idx_forensic_attempts_final_verdict on public.forensic_attempts (final_verdict);
create index if not exists idx_forensic_events_attempt_id on public.forensic_events (attempt_id, ordinal);
create index if not exists idx_forensic_artifacts_attempt_id on public.forensic_artifacts (attempt_id, captured_at);
create index if not exists idx_forensic_links_parent on public.forensic_links (parent_attempt_id, created_at);
create index if not exists idx_forensic_links_child on public.forensic_links (child_attempt_id, created_at);

alter table public.forensic_attempts disable row level security;
alter table public.forensic_events disable row level security;
alter table public.forensic_artifacts disable row level security;
alter table public.forensic_links disable row level security;
alter table public.forensic_verdicts disable row level security;

insert into storage.buckets (id, name, public)
values ('forensics', 'forensics', false)
on conflict (id) do nothing;
