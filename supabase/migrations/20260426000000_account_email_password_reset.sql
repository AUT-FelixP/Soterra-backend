alter table public.tenants
  add column if not exists email_domain text;

update public.tenants t
set email_domain = split_part(lower(u.email), '@', 2)
from public.users u
where u.tenant_id = t.id
  and u.role = 'admin'
  and t.email_domain is null
  and split_part(lower(u.email), '@', 2) not in (
    'aol.com',
    'gmail.com',
    'googlemail.com',
    'hotmail.com',
    'icloud.com',
    'live.com',
    'mail.com',
    'me.com',
    'msn.com',
    'outlook.com',
    'proton.me',
    'protonmail.com',
    'yahoo.com',
    'ymail.com'
  );

create index if not exists idx_tenants_email_domain
  on public.tenants (email_domain);

create table if not exists public.password_reset_tokens (
  id text primary key,
  user_id text not null references public.users(id) on delete cascade,
  tenant_id text not null references public.tenants(id) on delete cascade,
  token_hash text not null unique,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  used_at timestamptz
);

create index if not exists idx_password_reset_tokens_token_hash
  on public.password_reset_tokens (token_hash);

alter table public.password_reset_tokens enable row level security;

drop policy if exists tenant_isolation_password_reset_tokens on public.password_reset_tokens;
create policy tenant_isolation_password_reset_tokens
  on public.password_reset_tokens for all to authenticated
  using (tenant_id = (auth.jwt() ->> 'tenant_id'))
  with check (tenant_id = (auth.jwt() ->> 'tenant_id'));

alter table public.documents
  drop constraint if exists documents_file_hash_key,
  drop constraint if exists documents_file_tag_key;

drop index if exists public.idx_documents_file_hash_unique;
drop index if exists public.idx_documents_file_tag_unique;
drop index if exists public.idx_documents_file_tag;

create index if not exists idx_documents_file_hash
  on public.documents (tenant_id, file_hash);

create unique index if not exists idx_documents_tenant_file_hash_unique
  on public.documents (tenant_id, file_hash);

create unique index if not exists idx_documents_tenant_file_tag_unique
  on public.documents (tenant_id, file_tag);
