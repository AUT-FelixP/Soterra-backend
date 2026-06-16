CREATE TABLE IF NOT EXISTS public.agent_chat_sessions (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  title TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS public.agent_chat_messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES public.agent_chat_sessions(id) ON DELETE CASCADE,
  tenant_id TEXT NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
  content TEXT NOT NULL,
  tool_name TEXT NULL,
  tool_payload_json JSONB NULL,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_tenant_user_updated
  ON public.agent_chat_sessions (tenant_id, user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_messages_session_created
  ON public.agent_chat_messages (tenant_id, session_id, created_at ASC);

ALTER TABLE public.agent_chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_chat_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agent_chat_sessions_user_tenant_select ON public.agent_chat_sessions;
CREATE POLICY agent_chat_sessions_user_tenant_select
  ON public.agent_chat_sessions
  FOR SELECT
  USING (tenant_id = auth.jwt() ->> 'tenant_id' AND user_id = auth.uid()::text);

DROP POLICY IF EXISTS agent_chat_sessions_user_tenant_insert ON public.agent_chat_sessions;
CREATE POLICY agent_chat_sessions_user_tenant_insert
  ON public.agent_chat_sessions
  FOR INSERT
  WITH CHECK (tenant_id = auth.jwt() ->> 'tenant_id' AND user_id = auth.uid()::text);

DROP POLICY IF EXISTS agent_chat_sessions_user_tenant_update ON public.agent_chat_sessions;
CREATE POLICY agent_chat_sessions_user_tenant_update
  ON public.agent_chat_sessions
  FOR UPDATE
  USING (tenant_id = auth.jwt() ->> 'tenant_id' AND user_id = auth.uid()::text)
  WITH CHECK (tenant_id = auth.jwt() ->> 'tenant_id' AND user_id = auth.uid()::text);

DROP POLICY IF EXISTS agent_chat_messages_user_tenant_select ON public.agent_chat_messages;
CREATE POLICY agent_chat_messages_user_tenant_select
  ON public.agent_chat_messages
  FOR SELECT
  USING (tenant_id = auth.jwt() ->> 'tenant_id' AND user_id = auth.uid()::text);

DROP POLICY IF EXISTS agent_chat_messages_user_tenant_insert ON public.agent_chat_messages;
CREATE POLICY agent_chat_messages_user_tenant_insert
  ON public.agent_chat_messages
  FOR INSERT
  WITH CHECK (tenant_id = auth.jwt() ->> 'tenant_id' AND user_id = auth.uid()::text);
