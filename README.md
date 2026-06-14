# Soterra Backend

FastAPI backend for report ingestion, extraction, analytics, and persistence.

## Local development

1. Create a virtual environment inside this folder.
2. Install the package in editable mode:
   `python -m pip install -e .`
3. Start the API:
   `python -m uvicorn soterra_backend.api:app --host 127.0.0.1 --port 8001`

## Deploy to Vercel

- Root directory: `backend`
- Framework/runtime: FastAPI / Python
- App entrypoint comes from `pyproject.toml`:
  `app = "soterra_backend.api:app"`
- `requirements.txt` installs the lightweight backend only. Models are called through hosted Hugging Face Inference Providers, so Vercel does not install Torch or Transformers.

## Required environment variables

- `SOTERRA_EXTRACTOR_MODE=model`
- `HF_TOKEN=...`
- `SOTERRA_DOCUMENT_PARSE_PROVIDER=huggingface`
- `SOTERRA_DOCUMENT_PARSE_MODEL_ID=HuggingFaceTB/SmolVLM-256M-Instruct`
- `SOTERRA_DOCUMENT_PARSE_MAX_PAGES=12`
- `SOTERRA_EXTRACTION_PROVIDER=huggingface`
- `SOTERRA_EXTRACTION_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct`
- `SOTERRA_EXTRACTION_MODELS_JSON` optional list of provider/model configs for model comparison before fallback
- `SOTERRA_AGENT_ENABLED=true`
- `SOTERRA_AGENT_PROVIDER=huggingface`
- `SOTERRA_AGENT_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct`
- `SOTERRA_REPOSITORY_MODE`
- `SOTERRA_STORAGE_MODE`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`

Optional fallback/dev variable:
- `SOTERRA_PACKAGE_EXTRACTOR=doctr_rules_presidio` only if you intentionally install package OCR extras for fallback testing

## Apply Supabase database migrations

Uploading the backend files to Git does not update the tables in Supabase. When a
new file is added under `supabase/migrations`, run that SQL against the hosted
Supabase database before starting the updated backend.

For the tenant-isolation update:

1. Open the Supabase project dashboard.
2. Open **SQL Editor** and create a new query.
3. Paste and run the contents of:
   `supabase/migrations/20260601000000_production_tenant_hardening.sql`
4. Restart the backend.

To verify the specific `jobs.tenant_id` repair, run this in the Supabase SQL
Editor:

```sql
select column_name
from information_schema.columns
where table_schema = 'public'
  and table_name = 'jobs'
  and column_name = 'tenant_id';
```

The result should contain one row named `tenant_id`.

### Troubleshooting: `column jobs.tenant_id does not exist`

This error means the Python backend is newer than the hosted Supabase schema.
The backend filters extraction jobs by `tenant_id` so one customer's jobs cannot
be returned to another customer. Apply the tenant-isolation migration above to
add the missing column, fill it for existing jobs, and enable the matching
database protections.
