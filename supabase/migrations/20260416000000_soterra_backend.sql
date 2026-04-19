create table if not exists public.projects (
  id text primary key,
  slug text not null unique,
  name text not null,
  site_name text not null,
  address text,
  created_at timestamptz not null default now()
);

comment on table public.projects is
'Project master table. Each extracted report rolls up under one project row so the frontend can query project-level metrics.';

create table if not exists public.documents (
  id text primary key,
  project_id text not null references public.projects(id) on delete cascade,
  file_hash text not null unique,
  file_tag text not null unique,
  source_filename text not null,
  storage_path text not null,
  download_url text,
  inspection_type text not null,
  trade text not null,
  inspector text not null,
  report_date date not null,
  status text not null check (status in ('Reviewing', 'Completed', 'In progress')),
  summary text not null,
  units_json jsonb not null default '[]'::jsonb,
  uploaded_at timestamptz not null default now()
);

comment on table public.documents is
'Transactional report table. One row per uploaded inspection report after extraction.';

create table if not exists public.jobs (
  id text primary key,
  document_id text not null references public.documents(id) on delete cascade,
  status text not null check (status in ('pending', 'running', 'completed', 'failed')),
  extractor text not null,
  error_message text,
  raw_text_excerpt text,
  raw_payload_json jsonb,
  started_at timestamptz,
  completed_at timestamptz
);

comment on table public.jobs is
'Extraction job ledger. This keeps the worker history separate from the report facts.';

create table if not exists public.findings (
  id text primary key,
  document_id text not null references public.documents(id) on delete cascade,
  project_id text not null references public.projects(id) on delete cascade,
  title text not null,
  description text not null,
  category text not null,
  trade text not null,
  severity text not null check (severity in ('Low', 'Medium', 'High', 'Critical')),
  status text not null check (status in ('Open', 'Ready', 'Closed')),
  location text,
  unit_label text,
  recurrence_risk integer not null default 30 check (recurrence_risk between 0 and 100),
  reinspections integer not null default 0 check (reinspections >= 0),
  last_sent_to text,
  created_at timestamptz not null default now(),
  closed_at timestamptz
);

comment on table public.findings is
'Transactional defect register extracted from uploaded reports. The tracker page and report detail page both read from here.';

create table if not exists public.predicted_inspections (
  id text primary key,
  project_id text not null references public.projects(id) on delete cascade,
  inspection_type text not null,
  site_name text not null,
  expected_date date not null,
  risk_level text not null check (risk_level in ('Low', 'Medium', 'High')),
  source text not null,
  created_at timestamptz not null default now()
);

comment on table public.predicted_inspections is
'Forward-looking inspection prompts generated from extracted defects. The risk pages read from this table.';

create index if not exists idx_documents_project_date
  on public.documents (project_id, report_date desc);

create index if not exists idx_documents_file_hash
  on public.documents (file_hash);

create index if not exists idx_findings_document
  on public.findings (document_id);

create index if not exists idx_findings_project_status
  on public.findings (project_id, status);

create index if not exists idx_findings_trade_severity
  on public.findings (trade, severity);

create index if not exists idx_predicted_inspections_date
  on public.predicted_inspections (expected_date);

create or replace view public.analytics_report_summary_v as
select
  d.id as report_id,
  p.slug as project_slug,
  p.name as project_name,
  p.site_name,
  d.report_date,
  d.inspection_type,
  d.trade,
  d.status as report_status,
  count(f.id) as finding_count,
  count(*) filter (where f.status = 'Open') as open_findings,
  count(*) filter (where f.severity in ('High', 'Critical')) as severe_findings,
  max(
    case f.severity
      when 'Critical' then 4
      when 'High' then 3
      when 'Medium' then 2
      else 1
    end
  ) as highest_severity_rank
from public.documents d
join public.projects p on p.id = d.project_id
left join public.findings f on f.document_id = d.id
group by d.id, p.slug, p.name, p.site_name, d.report_date, d.inspection_type, d.trade, d.status;

comment on view public.analytics_report_summary_v is
'One row per report with issue counts and severity rollups. This view powers the reports page and the high-level dashboard cards.';

create or replace view public.analytics_company_metrics_v as
select
  count(distinct d.id) as total_reports,
  count(f.id) as total_findings,
  count(*) filter (where f.severity in ('High', 'Critical')) as severe_findings,
  round(
    100.0 * count(*) filter (where f.severity in ('High', 'Critical')) / nullif(count(f.id), 0),
    0
  ) as failure_rate,
  round(count(f.id)::numeric / nullif(count(distinct d.id), 0), 1) as findings_per_report,
  count(*) filter (where f.status = 'Open') as open_findings,
  round(
    100.0 * count(*) filter (where f.reinspections > 0) / nullif(count(f.id), 0),
    0
  ) as reinspection_rate
from public.documents d
left join public.findings f on f.document_id = d.id;

comment on view public.analytics_company_metrics_v is
'Single-row company summary used by dashboard cards and executive metrics.';

create or replace view public.analytics_project_metrics_v as
with document_counts as (
  select
    project_id,
    count(*) as total_reports
  from public.documents
  group by project_id
),
finding_counts as (
  select
    project_id,
    count(*) as total_findings,
    count(*) filter (where severity in ('High', 'Critical')) as severe_findings,
    count(*) filter (where reinspections > 0) as reinspection_findings
  from public.findings
  group by project_id
)
select
  p.slug as project_slug,
  p.name as project_name,
  p.site_name,
  coalesce(dc.total_reports, 0) as total_reports,
  coalesce(fc.total_findings, 0) as total_findings,
  round(
    100.0 * coalesce(fc.severe_findings, 0) / nullif(coalesce(fc.total_findings, 0), 0),
    0
  ) as failure_rate,
  round(coalesce(fc.total_findings, 0)::numeric / nullif(coalesce(dc.total_reports, 0), 0), 1) as findings_per_report,
  round(
    100.0 * coalesce(fc.reinspection_findings, 0) / nullif(coalesce(fc.total_findings, 0), 0),
    0
  ) as reinspection_rate
from public.projects p
left join document_counts dc on dc.project_id = p.id
left join finding_counts fc on fc.project_id = p.id;

comment on view public.analytics_project_metrics_v is
'Project-level summary for company and project pages.';

create or replace view public.analytics_top_failure_drivers_v as
select
  p.slug as project_slug,
  d.inspection_type,
  f.trade,
  f.title,
  count(*) as fail_count,
  round(
    100.0 * count(*) / nullif(sum(count(*)) over (partition by p.slug, d.inspection_type), 0),
    0
  ) as failure_share,
  round(avg(f.recurrence_risk), 0) as average_recurrence_risk,
  round(
    100.0 * count(*) filter (where f.reinspections > 0) / nullif(count(*), 0),
    0
  ) as reinspection_rate
from public.findings f
join public.projects p on p.id = f.project_id
join public.documents d on d.id = f.document_id
group by p.slug, d.inspection_type, f.trade, f.title;

comment on view public.analytics_top_failure_drivers_v is
'Defect title aggregation used by performance, insights, and risk pages.';

create or replace view public.analytics_upcoming_risk_v as
select
  pi.id as predicted_inspection_id,
  p.slug as project_slug,
  p.name as project_name,
  pi.inspection_type,
  pi.site_name,
  pi.expected_date,
  pi.risk_level,
  pi.source
from public.predicted_inspections pi
join public.projects p on p.id = pi.project_id;

comment on view public.analytics_upcoming_risk_v is
'Future inspection prompts for the risk pages.';
