ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS site_name TEXT,
  ADD COLUMN IF NOT EXISTS address TEXT;

UPDATE documents d
SET
  site_name = COALESCE(d.site_name, p.site_name),
  address = COALESCE(d.address, p.address)
FROM projects p
WHERE p.id = d.project_id
  AND (d.site_name IS NULL OR d.address IS NULL);
