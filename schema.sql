-- Run once in the Supabase SQL editor.

create table if not exists prices (
    code        text          not null,
    date        date          not null,
    close       numeric(14,4) not null,
    adj_close   numeric(14,4),
    change_pct  numeric(8,4)  not null,  -- exchange-reported daily change, in percent (-0.85 = -0.85%)
    volume      bigint,
    value       numeric(18,4),
    open        numeric(14,4),
    high        numeric(14,4),
    low         numeric(14,4),
    primary key (code, date)
);

create index if not exists prices_date_idx on prices (date);

create table if not exists upload_log (
    id            bigserial primary key,
    uploaded_at   timestamptz not null default now(),
    filenames     text        not null,
    rows_in_file  integer     not null,
    rows_new      integer     not null,
    rows_updated  integer     not null,
    date_min      date        not null,
    date_max      date        not null
);

-- The app connects with the database connection string (server side), which bypasses RLS.
-- Enabling RLS with no policies blocks everyone using the public Supabase API keys.
alter table prices     enable row level security;
alter table upload_log enable row level security;
