alter table public.projects
  add column if not exists project_lifecycle text not null default 'active';

alter table public.projects
  drop constraint if exists projects_project_lifecycle_check,
  add constraint projects_project_lifecycle_check
    check (project_lifecycle in ('active', 'completed', 'closed', 'archived'));
