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

- `SOTERRA_EXTRACTOR_MODE=ollama_text` for production external Ollama extraction without local AI packages, `local_ai` for the local Docling/Ollama pipeline, or `package` for the legacy package extractor.
- `SOTERRA_AGENT_ENABLED=true`
- `SOTERRA_REPOSITORY_MODE`
- `SOTERRA_STORAGE_MODE`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`

Legacy hosted model variables, only if you intentionally use `SOTERRA_EXTRACTOR_MODE=model` or `SOTERRA_AGENT_PROVIDER=huggingface`:

- `HF_TOKEN=...`
- `SOTERRA_DOCUMENT_PARSE_PROVIDER=huggingface`
- `SOTERRA_DOCUMENT_PARSE_MODEL_ID=HuggingFaceTB/SmolVLM-256M-Instruct`
- `SOTERRA_DOCUMENT_PARSE_MAX_PAGES=12`
- `SOTERRA_EXTRACTION_PROVIDER=huggingface`
- `SOTERRA_EXTRACTION_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct`
- `SOTERRA_EXTRACTION_MODELS_JSON` optional list of provider/model configs for model comparison before fallback
- `SOTERRA_AGENT_PROVIDER=huggingface`
- `SOTERRA_AGENT_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct`

Optional fallback/dev variable:
- `SOTERRA_PACKAGE_EXTRACTOR=doctr_rules_presidio` only if you intentionally install package OCR extras for fallback testing

## Azure App Service production extraction

Use this mode for Azure App Service Free or other constrained production hosts. It installs only the base package with `python -m pip install -e .`, extracts embedded PDF text with PyMuPDF and pypdf, then sends that text to the external Ollama API. It does not require Docling, PaddleOCR, Torch, torchvision, Transformers, CUDA/NVIDIA packages, local model weights, or local Ollama.

```bash
SOTERRA_EXTRACTOR_MODE=ollama_text
SOTERRA_EXTRACTION_PROVIDER=ollama
SOTERRA_EXTRACTION_MODEL_ID=gpt-oss:20b
SOTERRA_EXTRACTION_VISION_MODEL_ID=minimax-m3
SOTERRA_OLLAMA_BASE_URL=https://ollama.com
SOTERRA_OLLAMA_API_KEY=<secret>
SOTERRA_PADDLE_OCR_ENABLED=false
```

The backend needs outbound HTTPS access to `SOTERRA_OLLAMA_BASE_URL`. `SOTERRA_EXTRACTION_VISION_MODEL_ID` is used only when a PDF has no embedded text and the backend must send rendered page images to Ollama.

## Free local extraction setup

Install the local extraction extras:

```bash
python -m pip install -e ".[local-ai]"
```

### Ollama cloud mode

Use `ollama_text` for Azure production. Use this `local_ai` cloud mode only when you intentionally install the local AI extras for Docling parsing while still calling Ollama through the cloud API. Ollama's cloud API uses the same `/api/chat` contract as local Ollama, with bearer-token authentication.

```bash
SOTERRA_EXTRACTOR_MODE=local_ai
SOTERRA_DOCUMENT_PARSE_PROVIDER=docling
SOTERRA_EXTRACTION_PROVIDER=ollama
SOTERRA_EXTRACTION_MODEL_ID=gpt-oss:20b
SOTERRA_OLLAMA_BASE_URL=https://ollama.com
SOTERRA_OLLAMA_API_KEY=your_ollama_api_key
SOTERRA_PADDLE_OCR_ENABLED=false
SOTERRA_LOCAL_AI_FALLBACK_TO_PACKAGE=true
SOTERRA_AGENT_PROVIDER=ollama
SOTERRA_AGENT_MODEL_ID=gpt-oss:20b
```

You can swap `SOTERRA_EXTRACTION_MODEL_ID` and `SOTERRA_AGENT_MODEL_ID` without code changes.

### Local Ollama mode

Install Ollama from [ollama.com](https://ollama.com), then pull the default extraction model:

```bash
ollama pull qwen2.5:7b-instruct
```

Recommended local environment:

```bash
SOTERRA_EXTRACTOR_MODE=local_ai
SOTERRA_DOCUMENT_PARSE_PROVIDER=docling
SOTERRA_EXTRACTION_PROVIDER=ollama
SOTERRA_EXTRACTION_MODEL_ID=qwen2.5:7b-instruct
SOTERRA_OLLAMA_BASE_URL=http://localhost:11434
SOTERRA_PADDLE_OCR_ENABLED=false
SOTERRA_LOCAL_AI_FALLBACK_TO_PACKAGE=true
SOTERRA_AGENT_PROVIDER=ollama
SOTERRA_AGENT_MODEL_ID=qwen2.5:7b-instruct
```

For low-memory machines:

```bash
SOTERRA_EXTRACTION_MODEL_ID=qwen2.5:3b-instruct
SOTERRA_AGENT_MODEL_ID=qwen2.5:3b-instruct
```

Notes:

- Docling parses PDFs and Word documents locally, including layout and tables where available.
- PyMuPDF embedded text is used for normal PDFs when it is strong enough.
- PaddleOCR can be enabled for scanned PDFs with `SOTERRA_PADDLE_OCR_ENABLED=true`, but it may be slower.
- For local Ollama mode, Ollama must be running before uploading documents.
- For Ollama cloud mode, the backend needs outbound HTTPS access and `SOTERRA_OLLAMA_API_KEY`.
- Extraction quality depends on the selected model and parser output.
- Do not commit `.env` secrets. Keep Supabase repository/storage settings unchanged and rotate exposed service role keys outside this code change.

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
