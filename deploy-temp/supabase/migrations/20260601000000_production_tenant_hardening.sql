-- Production tenant isolation hardening. The backend uses a service-role client,
-- so repository tenant filters remain mandatory even with these RLS policies.

ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS tenant_id TEXT;

ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE public.users
  ADD CONSTRAINT users_role_check
  CHECK (role IN ('admin', 'tenant_admin', 'project_admin', 'member', 'viewer'));

UPDATE public.jobs j
SET tenant_id = d.tenant_id
FROM public.documents d
WHERE j.document_id = d.id
  AND j.tenant_id IS NULL;

ALTER TABLE public.jobs ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.jobs
  DROP CONSTRAINT IF EXISTS jobs_tenant_id_fkey,
  ADD CONSTRAINT jobs_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_jobs_tenant_document
  ON public.jobs (tenant_id, document_id);

CREATE TABLE IF NOT EXISTS public.upload_attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_upload_attempts_tenant_time
  ON public.upload_attempts (tenant_id, attempted_at);

ALTER TABLE public.upload_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.upload_attempts FROM anon, authenticated;

CREATE OR REPLACE FUNCTION public.consume_tenant_upload_rate_limit(
  p_tenant_id TEXT,
  p_limit INTEGER,
  p_window_seconds INTEGER
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  attempt_count INTEGER;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext(p_tenant_id));
  DELETE FROM public.upload_attempts
  WHERE attempted_at < now() - make_interval(secs => greatest(1, p_window_seconds));

  SELECT count(*) INTO attempt_count
  FROM public.upload_attempts
  WHERE tenant_id = p_tenant_id;

  IF attempt_count >= greatest(1, p_limit) THEN
    RETURN false;
  END IF;

  INSERT INTO public.upload_attempts (tenant_id) VALUES (p_tenant_id);
  RETURN true;
END;
$$;

REVOKE ALL ON FUNCTION public.consume_tenant_upload_rate_limit(TEXT, INTEGER, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.consume_tenant_upload_rate_limit(TEXT, INTEGER, INTEGER) TO service_role;

ALTER TABLE public.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predicted_inspections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_chat_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_auth_sessions ON public.auth_sessions;
CREATE POLICY tenant_isolation_auth_sessions
  ON public.auth_sessions FOR ALL TO authenticated
  USING (tenant_id = (auth.jwt() ->> 'tenant_id'))
  WITH CHECK (tenant_id = (auth.jwt() ->> 'tenant_id'));

DROP POLICY IF EXISTS tenant_isolation_jobs ON public.jobs;
CREATE POLICY tenant_isolation_jobs
  ON public.jobs FOR ALL TO authenticated
  USING (tenant_id = (auth.jwt() ->> 'tenant_id'))
  WITH CHECK (tenant_id = (auth.jwt() ->> 'tenant_id'));

ALTER VIEW public.analytics_report_summary_v SET (security_invoker = true);
ALTER VIEW public.analytics_company_metrics_v SET (security_invoker = true);
ALTER VIEW public.analytics_project_metrics_v SET (security_invoker = true);
ALTER VIEW public.analytics_top_failure_drivers_v SET (security_invoker = true);
ALTER VIEW public.analytics_upcoming_risk_v SET (security_invoker = true);

REVOKE ALL ON
  public.analytics_report_summary_v,
  public.analytics_company_metrics_v,
  public.analytics_project_metrics_v,
  public.analytics_top_failure_drivers_v,
  public.analytics_upcoming_risk_v
FROM anon;

GRANT SELECT ON
  public.analytics_report_summary_v,
  public.analytics_company_metrics_v,
  public.analytics_project_metrics_v,
  public.analytics_top_failure_drivers_v,
  public.analytics_upcoming_risk_v
TO authenticated;

UPDATE storage.buckets
SET public = false
WHERE id = 'inspection-reports';

DROP POLICY IF EXISTS tenant_storage_select ON storage.objects;
CREATE POLICY tenant_storage_select
  ON storage.objects FOR SELECT TO authenticated
  USING (
    bucket_id = 'inspection-reports'
    AND (storage.foldername(name))[1] = (auth.jwt() ->> 'tenant_id')
  );

DROP POLICY IF EXISTS tenant_storage_insert ON storage.objects;
CREATE POLICY tenant_storage_insert
  ON storage.objects FOR INSERT TO authenticated
  WITH CHECK (
    bucket_id = 'inspection-reports'
    AND (storage.foldername(name))[1] = (auth.jwt() ->> 'tenant_id')
  );

DROP POLICY IF EXISTS tenant_storage_update ON storage.objects;
CREATE POLICY tenant_storage_update
  ON storage.objects FOR UPDATE TO authenticated
  USING (
    bucket_id = 'inspection-reports'
    AND (storage.foldername(name))[1] = (auth.jwt() ->> 'tenant_id')
  )
  WITH CHECK (
    bucket_id = 'inspection-reports'
    AND (storage.foldername(name))[1] = (auth.jwt() ->> 'tenant_id')
  );

DROP POLICY IF EXISTS tenant_storage_delete ON storage.objects;
CREATE POLICY tenant_storage_delete
  ON storage.objects FOR DELETE TO authenticated
  USING (
    bucket_id = 'inspection-reports'
    AND (storage.foldername(name))[1] = (auth.jwt() ->> 'tenant_id')
  );
