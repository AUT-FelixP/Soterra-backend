alter table public.findings
  add column if not exists project_name text,
  add column if not exists issue_title text,
  add column if not exists plain_english_summary text,
  add column if not exists level text,
  add column if not exists unit_or_area text,
  add column if not exists inspection_type text,
  add column if not exists root_cause text,
  add column if not exists required_fix text,
  add column if not exists evidence_required_json jsonb not null default '[]'::jsonb,
  add column if not exists source_document text,
  add column if not exists source_page integer check (source_page is null or source_page >= 1),
  add column if not exists source_quote text,
  add column if not exists confidence double precision not null default 0.5 check (confidence between 0 and 1),
  add column if not exists extraction_warnings_json jsonb not null default '[]'::jsonb;

alter table public.findings drop constraint if exists findings_status_check;
alter table public.findings
  add constraint findings_status_check
  check (status in ('Open', 'In Progress', 'Needs Review', 'Ready', 'Closed'));

comment on column public.findings.source_quote is
'Extracted source evidence retained for human verification. Findings without a direct quote must carry an extraction warning.';
