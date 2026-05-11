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

- `HF_TOKEN` for the smolagents chat agent when `SOTERRA_AGENT_ENABLED=true`
- `SOTERRA_AGENT_ENABLED=true`
- `SOTERRA_AGENT_MODEL_PROVIDER=huggingface`
- `SOTERRA_AGENT_MODEL_ID=Qwen/Qwen2.5-72B-Instruct`
- `SOTERRA_AGENT_HF_PROVIDER` optional Hugging Face Inference Provider name
- `OPENAI_API_KEY` only when using the OpenAI report extractor or OpenAI agent provider
- `OPENAI_MODEL` only when using the OpenAI report extractor or OpenAI agent provider
- `SOTERRA_MODEL_EXTRACTOR`
- `SOTERRA_PACKAGE_EXTRACTOR`
- `SOTERRA_REPOSITORY_MODE`
- `SOTERRA_STORAGE_MODE`
- `SOTERRA_EXTRACTOR_MODE`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`
