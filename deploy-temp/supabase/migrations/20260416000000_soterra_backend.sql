create table if not exists public.tenants (
  id text primary key,
  name text not null,
  slug text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists public.users (
  id text primary key,
  tenant_id text not null references public.tenants(id) on delete cascade,
  name text not null,
  email text not null unique,
  password_hash text not null,
  role text not null check (role in ('admin', 'member')),
  created_at timestamptz not null default now()
);

create unique index if not exists idx_users_email_lower_unique
  on public.users (lower(email));

create table if not exists public.auth_sessions (
  id text primary key,
  user_id text not null references public.users(id) on delete cascade,
  tenant_id text not null references public.tenants(id) on delete cascade,
  token_hash text not null unique,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  revoked_at timestamptz
);

create table if not exists public.projects (
  id text primary key,
  tenant_id text not null references public.tenants(id) on delete cascade,
  slug text not null,
  name text not null,
  site_name text not null,
  address text,
  created_at timestamptz not null default now()
);

comment on table public.projects is
'Project master table. Each extracted report rolls up under one project row so the frontend can query project-level metrics.';

create table if not exists public.documents (
  id text primary key,
  tenant_id text not null references public.tenants(id) on delete cascade,
  project_id text not null references public.projects(id) on delete cascade,
  file_hash text not null,
  file_tag text not null,
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
  tenant_id text not null references public.tenants(id) on delete cascade,
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
  tenant_id text not null references public.tenants(id) on delete cascade,
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
  on public.documents (tenant_id, project_id, report_date desc);

create index if not exists idx_auth_sessions_token_hash
  on public.auth_sessions (token_hash);

create index if not exists idx_auth_sessions_user_tenant
  on public.auth_sessions (user_id, tenant_id);

create index if not exists idx_documents_file_hash
  on public.documents (tenant_id, file_hash);

create unique index if not exists idx_projects_tenant_slug_unique
  on public.projects (tenant_id, slug);

create unique index if not exists idx_documents_tenant_file_hash_unique
  on public.documents (tenant_id, file_hash);

create unique index if not exists idx_documents_tenant_file_tag_unique
  on public.documents (tenant_id, file_tag);

create index if not exists idx_findings_document
  on public.findings (document_id);

create index if not exists idx_findings_project_status
  on public.findings (tenant_id, project_id, status);

create index if not exists idx_findings_trade_severity
  on public.findings (trade, severity);

create index if not exists idx_predicted_inspections_date
  on public.predicted_inspections (tenant_id, expected_date);

alter table public.tenants enable row level security;
alter table public.users enable row level security;
alter table public.auth_sessions enable row level security;
alter table public.projects enable row level security;
alter table public.documents enable row level security;
alter table public.jobs enable row level security;
alter table public.findings enable row level security;
alter table public.predicted_inspections enable row level security;

drop policy if exists tenant_isolation_tenants on public.tenants;
create policy tenant_isolation_tenants
  on public.tenants for all to authenticated
  using (id = (auth.jwt() ->> 'tenant_id'))
  with check (id = (auth.jwt() ->> 'tenant_id'));

drop policy if exists tenant_isolation_users on public.users;
create policy tenant_isolation_users
  on public.users for all to authenticated
  using (tenant_id = (auth.jwt() ->> 'tenant_id'))
  with check (tenant_id = (auth.jwt() ->> 'tenant_id'));

drop policy if exists tenant_isolation_projects on public.projects;
create policy tenant_isolation_projects
  on public.projects for all to authenticated
  using (tenant_id = (auth.jwt() ->> 'tenant_id'))
  with check (tenant_id = (auth.jwt() ->> 'tenant_id'));

drop policy if exists tenant_isolation_documents on public.documents;
create policy tenant_isolation_documents
  on public.documents for all to authenticated
  using (tenant_id = (auth.jwt() ->> 'tenant_id'))
  with check (tenant_id = (auth.jwt() ->> 'tenant_id'));

drop policy if exists tenant_isolation_jobs on public.jobs;
create policy tenant_isolation_jobs
  on public.jobs for all to authenticated
  using (exists (
    select 1 from public.documents d
    where d.id = jobs.document_id and d.tenant_id = (auth.jwt() ->> 'tenant_id')
  ))
  with check (exists (
    select 1 from public.documents d
    where d.id = jobs.document_id and d.tenant_id = (auth.jwt() ->> 'tenant_id')
  ));

drop policy if exists tenant_isolation_findings on public.findings;
create policy tenant_isolation_findings
  on public.findings for all to authenticated
  using (tenant_id = (auth.jwt() ->> 'tenant_id'))
  with check (tenant_id = (auth.jwt() ->> 'tenant_id'));

drop policy if exists tenant_isolation_predicted_inspections on public.predicted_inspections;
create policy tenant_isolation_predicted_inspections
  on public.predicted_inspections for all to authenticated
  using (tenant_id = (auth.jwt() ->> 'tenant_id'))
  with check (tenant_id = (auth.jwt() ->> 'tenant_id'));

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
