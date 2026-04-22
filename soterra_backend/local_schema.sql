CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
  created_at TEXT NOT NULL,
  FOREIGN KEY(tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id),
  FOREIGN KEY(tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  site_name TEXT NOT NULL,
  address TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  file_tag TEXT NOT NULL,
  source_filename TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  download_url TEXT,
  inspection_type TEXT NOT NULL,
  trade TEXT NOT NULL,
  inspector TEXT NOT NULL,
  report_date TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  units_json TEXT NOT NULL,
  uploaded_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  status TEXT NOT NULL,
  extractor TEXT NOT NULL,
  error_message TEXT,
  raw_text_excerpt TEXT,
  raw_payload_json TEXT,
  started_at TEXT,
  completed_at TEXT,
  FOREIGN KEY(document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  category TEXT NOT NULL,
  trade TEXT NOT NULL,
  severity TEXT NOT NULL,
  status TEXT NOT NULL,
  location TEXT,
  unit_label TEXT,
  recurrence_risk INTEGER NOT NULL DEFAULT 30,
  reinspections INTEGER NOT NULL DEFAULT 0,
  last_sent_to TEXT,
  created_at TEXT NOT NULL,
  closed_at TEXT,
  FOREIGN KEY(document_id) REFERENCES documents(id),
  FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS predicted_inspections (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  inspection_type TEXT NOT NULL,
  site_name TEXT NOT NULL,
  expected_date TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_documents_project_date
  ON documents(tenant_id, project_id, report_date DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower_unique
  ON users(lower(email));

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token_hash
  ON auth_sessions(token_hash);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_tenant
  ON auth_sessions(user_id, tenant_id);

CREATE INDEX IF NOT EXISTS idx_documents_file_hash
  ON documents(tenant_id, file_hash);

CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_tenant_slug_unique
  ON projects(tenant_id, slug);

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_tenant_file_hash_unique
  ON documents(tenant_id, file_hash);

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_tenant_file_tag_unique
  ON documents(tenant_id, file_tag);

CREATE INDEX IF NOT EXISTS idx_findings_document
  ON findings(document_id);

CREATE INDEX IF NOT EXISTS idx_findings_status
  ON findings(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_predicted_inspections_date
  ON predicted_inspections(tenant_id, expected_date);

CREATE VIEW IF NOT EXISTS analytics_report_summary_v AS
SELECT
  d.id AS report_id,
  p.slug AS project_slug,
  p.name AS project_name,
  p.site_name,
  d.report_date,
  d.inspection_type,
  d.trade,
  d.status AS report_status,
  COUNT(f.id) AS finding_count,
  SUM(CASE WHEN f.status = 'Open' THEN 1 ELSE 0 END) AS open_findings,
  SUM(CASE WHEN f.severity IN ('High', 'Critical') THEN 1 ELSE 0 END) AS severe_findings,
  MAX(
    CASE f.severity
      WHEN 'Critical' THEN 4
      WHEN 'High' THEN 3
      WHEN 'Medium' THEN 2
      WHEN 'Low' THEN 1
      ELSE 0
    END
  ) AS highest_severity_rank
FROM documents d
JOIN projects p ON p.id = d.project_id
LEFT JOIN findings f ON f.document_id = d.id
GROUP BY d.id, p.slug, p.name, p.site_name, d.report_date, d.inspection_type, d.trade, d.status;

CREATE VIEW IF NOT EXISTS analytics_company_metrics_v AS
SELECT
  COUNT(DISTINCT d.id) AS total_reports,
  COUNT(f.id) AS total_findings,
  SUM(CASE WHEN f.severity IN ('High', 'Critical') THEN 1 ELSE 0 END) AS severe_findings,
  ROUND(
    100.0 * SUM(CASE WHEN f.severity IN ('High', 'Critical') THEN 1 ELSE 0 END) / NULLIF(COUNT(f.id), 0),
    0
  ) AS failure_rate,
  ROUND(CAST(COUNT(f.id) AS REAL) / NULLIF(COUNT(DISTINCT d.id), 0), 1) AS findings_per_report,
  SUM(CASE WHEN f.status = 'Open' THEN 1 ELSE 0 END) AS open_findings,
  ROUND(
    100.0 * SUM(CASE WHEN f.reinspections > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(f.id), 0),
    0
  ) AS reinspection_rate
FROM documents d
LEFT JOIN findings f ON f.document_id = d.id;

CREATE VIEW IF NOT EXISTS analytics_project_metrics_v AS
WITH document_counts AS (
  SELECT
    project_id,
    COUNT(*) AS total_reports
  FROM documents
  GROUP BY project_id
),
finding_counts AS (
  SELECT
    project_id,
    COUNT(*) AS total_findings,
    SUM(CASE WHEN severity IN ('High', 'Critical') THEN 1 ELSE 0 END) AS severe_findings,
    SUM(CASE WHEN reinspections > 0 THEN 1 ELSE 0 END) AS reinspection_findings
  FROM findings
  GROUP BY project_id
)
SELECT
  p.slug AS project_slug,
  p.name AS project_name,
  p.site_name,
  COALESCE(dc.total_reports, 0) AS total_reports,
  COALESCE(fc.total_findings, 0) AS total_findings,
  ROUND(
    100.0 * COALESCE(fc.severe_findings, 0) / NULLIF(COALESCE(fc.total_findings, 0), 0),
    0
  ) AS failure_rate,
  ROUND(CAST(COALESCE(fc.total_findings, 0) AS REAL) / NULLIF(COALESCE(dc.total_reports, 0), 0), 1) AS findings_per_report,
  ROUND(
    100.0 * COALESCE(fc.reinspection_findings, 0) / NULLIF(COALESCE(fc.total_findings, 0), 0),
    0
  ) AS reinspection_rate
FROM projects p
LEFT JOIN document_counts dc ON dc.project_id = p.id
LEFT JOIN finding_counts fc ON fc.project_id = p.id;

CREATE VIEW IF NOT EXISTS analytics_top_failure_drivers_v AS
SELECT
  p.slug AS project_slug,
  d.inspection_type,
  f.trade,
  f.title,
  COUNT(*) AS fail_count,
  ROUND(
    100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY p.slug, d.inspection_type), 0),
    0
  ) AS failure_share,
  ROUND(AVG(f.recurrence_risk), 0) AS average_recurrence_risk,
  ROUND(
    100.0 * SUM(CASE WHEN f.reinspections > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
    0
  ) AS reinspection_rate
FROM findings f
JOIN projects p ON p.id = f.project_id
JOIN documents d ON d.id = f.document_id
GROUP BY p.slug, d.inspection_type, f.trade, f.title;

CREATE VIEW IF NOT EXISTS analytics_upcoming_risk_v AS
SELECT
  pi.id AS predicted_inspection_id,
  p.slug AS project_slug,
  p.name AS project_name,
  pi.inspection_type,
  pi.site_name,
  pi.expected_date,
  pi.risk_level,
  pi.source
FROM predicted_inspections pi
JOIN projects p ON p.id = pi.project_id;
