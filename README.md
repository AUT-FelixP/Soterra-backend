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

## Required environment variables

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `SOTERRA_MODEL_EXTRACTOR`
- `SOTERRA_PACKAGE_EXTRACTOR`
- `SOTERRA_REPOSITORY_MODE`
- `SOTERRA_STORAGE_MODE`
- `SOTERRA_EXTRACTOR_MODE`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`
