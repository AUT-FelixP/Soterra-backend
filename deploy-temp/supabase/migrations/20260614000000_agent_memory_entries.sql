CREATE TABLE IF NOT EXISTS public.agent_memory_entries (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  session_id TEXT NULL REFERENCES public.agent_chat_sessions(id) ON DELETE CASCADE,
  memory_type TEXT NOT NULL CHECK (memory_type IN ('tool', 'summary')),
  content TEXT NOT NULL,
  payload_json JSONB NULL,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_tenant_user_created
  ON public.agent_memory_entries (tenant_id, user_id, created_at DESC);

ALTER TABLE public.agent_memory_entries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agent_memory_user_tenant_select ON public.agent_memory_entries;
CREATE POLICY agent_memory_user_tenant_select
  ON public.agent_memory_entries
  FOR SELECT
  USING (tenant_id = auth.jwt() ->> 'tenant_id' AND user_id = auth.uid()::text);

DROP POLICY IF EXISTS agent_memory_user_tenant_insert ON public.agent_memory_entries;
CREATE POLICY agent_memory_user_tenant_insert
  ON public.agent_memory_entries
  FOR INSERT
  WITH CHECK (tenant_id = auth.jwt() ->> 'tenant_id' AND user_id = auth.uid()::text);
