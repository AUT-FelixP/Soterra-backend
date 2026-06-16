create table if not exists public.tenants (
  id text primary key,
  name text not null,
  slug text not null unique,
  created_at timestamptz not null default now()
);

insert into public.tenants (id, name, slug)
values ('ten-default', 'Default Tenant', 'default-tenant')
on conflict (id) do nothing;

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

alter table public.projects add column if not exists tenant_id text;
alter table public.documents add column if not exists tenant_id text;
alter table public.findings add column if not exists tenant_id text;
alter table public.predicted_inspections add column if not exists tenant_id text;

update public.projects set tenant_id = 'ten-default' where tenant_id is null;
update public.documents set tenant_id = 'ten-default' where tenant_id is null;
update public.findings set tenant_id = 'ten-default' where tenant_id is null;
update public.predicted_inspections set tenant_id = 'ten-default' where tenant_id is null;

alter table public.projects alter column tenant_id set not null;
alter table public.documents alter column tenant_id set not null;
alter table public.findings alter column tenant_id set not null;
alter table public.predicted_inspections alter column tenant_id set not null;

alter table public.projects drop constraint if exists projects_slug_key;
alter table public.documents
  drop constraint if exists documents_file_hash_key,
  drop constraint if exists documents_file_tag_key;

drop index if exists public.idx_documents_file_hash;
drop index if exists public.idx_documents_project_date;
drop index if exists public.idx_findings_project_status;
drop index if exists public.idx_predicted_inspections_date;

create unique index if not exists idx_projects_tenant_slug_unique
  on public.projects (tenant_id, slug);

create unique index if not exists idx_documents_tenant_file_hash_unique
  on public.documents (tenant_id, file_hash);

create unique index if not exists idx_documents_tenant_file_tag_unique
  on public.documents (tenant_id, file_tag);

create index if not exists idx_auth_sessions_token_hash
  on public.auth_sessions (token_hash);

create index if not exists idx_auth_sessions_user_tenant
  on public.auth_sessions (user_id, tenant_id);

create index if not exists idx_documents_project_date
  on public.documents (tenant_id, project_id, report_date desc);

create index if not exists idx_documents_file_hash
  on public.documents (tenant_id, file_hash);

create index if not exists idx_findings_project_status
  on public.findings (tenant_id, project_id, status);

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
