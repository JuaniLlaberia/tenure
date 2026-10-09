-- Tenure app schema. Safe to run more than once.
-- Run it in the Supabase SQL editor, or with psql against DATABASE_URL.
-- Row level security is on with no policies: only the secret (service) key can read or write.

create table if not exists businesses (
    business_id uuid primary key,
    telegram_chat_id bigint not null unique,
    created_at timestamptz not null default now()
);

alter table businesses add column if not exists dashboard_token text unique;
alter table businesses add column if not exists dashboard_password_hash text;

create table if not exists telegram_topics (
    team_id uuid primary key,
    business_id uuid not null references businesses on delete cascade,
    telegram_chat_id bigint not null,
    thread_id bigint not null,
    name text not null,
    unique (telegram_chat_id, thread_id)
);

create table if not exists business_profiles (
    business_id uuid primary key references businesses on delete cascade,
    name text not null,
    what_you_sell text not null,
    customers text not null,
    prices text,
    tone text,
    main_clients jsonb not null default '[]',
    links jsonb not null default '[]',
    extra jsonb not null default '{}'
);

create table if not exists teams (
    team_id uuid primary key,
    business_id uuid not null references businesses on delete cascade,
    template text not null,
    display_name text not null,
    onboarded boolean not null default false,
    created_at timestamptz not null
);
create index if not exists teams_business on teams (business_id, created_at);

create table if not exists trust (
    team_id uuid not null,
    task_type text not null,
    level text not null,
    approval_streak integer not null default 0,
    updated_at timestamptz not null,
    primary key (team_id, task_type)
);
alter table trust add column if not exists promote_after integer not null default 5;

create table if not exists tasks (
    task_id uuid primary key,
    business_id uuid not null references businesses on delete cascade,
    team_id uuid not null,
    task_type text not null,
    title text not null,
    brief text not null,
    status text not null,
    steps jsonb not null default '[]',
    current_step integer not null default 0,
    revisions integer not null default 0,
    tokens_used integer not null default 0,
    created_at timestamptz not null,
    updated_at timestamptz not null
);
create index if not exists tasks_team on tasks (team_id, created_at desc);

create table if not exists approvals (
    approval_id uuid primary key,
    business_id uuid not null references businesses on delete cascade,
    team_id uuid not null,
    task_id uuid not null,
    task_type text not null,
    preview text not null,
    planned_action jsonb,
    check_confidence double precision not null,
    status text not null default 'pending',
    edited_text text,
    reason text,
    created_at timestamptz not null,
    resolved_at timestamptz
);
create index if not exists approvals_recent on approvals (team_id, task_type, resolved_at desc);

create table if not exists lessons (
    lesson_id uuid primary key,
    business_id uuid not null references businesses on delete cascade,
    team_id uuid,
    task_type text,
    kind text not null,
    key text,
    text text not null,
    source text not null,
    source_ref text,
    active boolean not null default true,
    created_at timestamptz not null
);
create index if not exists lessons_active on lessons (business_id, active, created_at desc);

create table if not exists audit_log (
    action_id uuid primary key,
    business_id uuid not null references businesses on delete cascade,
    team_id uuid not null,
    task_id uuid not null,
    tool text not null,
    summary text not null,
    result jsonb not null,
    autonomous boolean not null,
    approval_id uuid,
    undo_until timestamptz,
    undone_at timestamptz,
    at timestamptz not null
);
create index if not exists audit_log_business on audit_log (business_id, at desc);

create table if not exists model_usage (
    usage_id uuid primary key,
    business_id uuid not null references businesses on delete cascade,
    team_id uuid,
    task_id uuid,
    model text not null,
    purpose text,
    input_tokens integer not null,
    output_tokens integer not null,
    cost double precision,
    at timestamptz not null
);
create index if not exists model_usage_business on model_usage (business_id, at desc);

create table if not exists telegram_state (
    kind text not null,
    key text not null,
    data jsonb not null,
    updated_at timestamptz not null default now(),
    primary key (kind, key)
);

alter table businesses enable row level security;
alter table telegram_topics enable row level security;
alter table business_profiles enable row level security;
alter table teams enable row level security;
alter table trust enable row level security;
alter table tasks enable row level security;
alter table approvals enable row level security;
alter table lessons enable row level security;
alter table audit_log enable row level security;
alter table model_usage enable row level security;
alter table telegram_state enable row level security;
