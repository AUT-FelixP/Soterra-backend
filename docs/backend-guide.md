# Soterra Backend Guide

## What was added

The repo now has a Python backend package under `backend/soterra_backend` and the existing Next.js API routes act as thin proxies to it. The UI still talks to the same `/api/...` paths, but the real data source is the Python service instead of the old mock files.

## High-level flow

1. The frontend uploads a PDF to `POST /api/reports`.
2. The Next.js route proxies that request to the Python backend.
3. The Python backend stores the file, creates a placeholder document + job row, extracts the report data, writes findings to the database, and returns the finished report payload.
4. Dashboard, tracker, risk, and report detail routes read from the Python backend so the frontend cards and tables come from persisted extraction results.

## Python package layout

- `backend/soterra_backend/config.py`
  Loads environment variables and selects the repository, storage, and extractor plugins.
- `backend/soterra_backend/extractors/`
  Contains the pluggable extraction implementations.
- `backend/soterra_backend/text_extraction.py`
  Shared PDF helpers used by model-backed extractors.
- `backend/soterra_backend/demo_extractions.py`
  Contains curated fallback profiles for the three Kauri Apartments benchmark documents.
- `backend/soterra_backend/extractors/model/document_text.py`
  Contains the document parsing stage. It sends rendered PDF pages to a hosted Hugging Face vision model, with package/embedded text fallback if the hosted call fails.
- `backend/soterra_backend/storage.py`
  Abstracts file storage. Local mode writes to `artifacts/backend/storage`; production mode writes to Supabase Storage.
- `backend/soterra_backend/repository.py`
  Abstracts persistence. Local mode uses SQLite for a runnable demo; production mode targets Supabase tables.
- `backend/soterra_backend/analytics.py`
  Builds the frontend response shapes from the stored report and finding rows.
- `backend/soterra_backend/service.py`
  Orchestrates upload -> extract -> persist and delegates extraction to the configured plugin.
- `backend/soterra_backend/api.py`
  Exposes the FastAPI routes that the Next.js proxy calls.

## Pluggable extraction design

The backend now has two extraction layers:

- `package`
  A local Python extraction package that uses `docTR + rules + Pydantic + Presidio`.
- `model`
  The default production path. A hosted SmolVLM model turns PDF page images into ordered text, then hosted SmolLM2 turns that text into the `ExtractionResult` schema. Package OCR is used only after the hosted model path fails quality/error checks.

The current extractor options are:

- `SOTERRA_EXTRACTOR_MODE=model`
  Uses `SOTERRA_DOCUMENT_PARSE_*` for document parsing and `SOTERRA_EXTRACTION_*` for structured JSON extraction. Multiple structured extraction models can be configured with `SOTERRA_EXTRACTION_MODELS_JSON`.
- `SOTERRA_EXTRACTOR_MODE=package`
  Development/fallback mode using `SOTERRA_PACKAGE_EXTRACTOR=doctr_rules_presidio`
- `SOTERRA_EXTRACTOR_MODE=demo`
  Uses deterministic demo profiles for fixture-style testing

Why this structure is useful:

- We can run a real local extractor without paying for tokens.
- We can keep the service flow unchanged when we move to a paid model later.
- The rest of the app only sees `ExtractionResult`, so the UI and database logic stay stable.

## Package extractor

The demo extractor lives in `backend/soterra_backend/extractors/package_doctr.py`.

It works like this:

1. Render the PDF into page images.
2. Run `docTR` OCR over the pages.
3. Apply explicit rules to detect project name, inspection type, date, units, and issue lines.
4. Validate the result with the `ExtractionResult` Pydantic schema.
5. Redact obvious PII like email addresses and street addresses with Presidio before storing raw text excerpts.
6. If the generic rules do not produce a usable result, fall back to the curated demo profiles for the known benchmark PDFs.

This is the recommended demo path because it is:

- free to run locally
- easy to understand
- maintainable without fine-tuning infrastructure
- replaceable later

## Model extraction

The model extractor lives under `backend/soterra_backend/extractors/model/`.

It works like this:

1. Render PDF pages to images.
2. Send each page image to hosted Hugging Face document parsing.
3. Pass the parsed document text/layout into the configured hosted structured extraction model.
4. Validate the JSON back into `ExtractionResult`, score quality, and fall back to the package extractor when quality is too low.

Recommended default model roles:

- Document parsing: `SOTERRA_DOCUMENT_PARSE_MODEL_ID=HuggingFaceTB/SmolVLM-256M-Instruct`
- Structured extraction and agent reasoning: `SOTERRA_EXTRACTION_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct` and `SOTERRA_AGENT_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct`
- All model calls use `huggingface` through `huggingface_hub.InferenceClient` with `HF_TOKEN`.

## Database design

### Transactional tables

- `projects`
  One row per project/site grouping.
- `documents`
  One row per uploaded report after extraction.
- `jobs`
  Tracks each extraction run separately from the report data.
- `findings`
  One row per extracted issue or defect.
- `predicted_inspections`
  Stores likely next inspections or rechecks derived from extracted defects.

### Analytics views

- `analytics_report_summary_v`
  Pre-aggregated report-level counts.
- `analytics_company_metrics_v`
  One-row company totals for dashboard cards.
- `analytics_project_metrics_v`
  Per-project metrics for company and project pages.
- `analytics_top_failure_drivers_v`
  Groups repeated defects into performance drivers.
- `analytics_upcoming_risk_v`
  A read model for upcoming inspection risk.

The SQL migration for the production schema is in:

- [20260416000000_soterra_backend.sql](/C:/repos/Soterra-_Client/supabase/migrations/20260416000000_soterra_backend.sql)

## Local demo mode

Local mode exists so the product can run in this workspace even without external credentials.

It uses:

- SQLite for persistence
- Local filesystem storage for PDFs
- The local `docTR + rules + Pydantic + Presidio` package extractor
- Curated extraction fallback profiles for the three provided Kauri Apartments documents

The local database file is:

- [soterra-demo.sqlite3](/C:/repos/Soterra-_Client/artifacts/backend/soterra-demo.sqlite3)

## Production mode

Set these environment variables:

- `BACKEND_BASE_URL`
- `HF_TOKEN`
- `SOTERRA_AGENT_ENABLED=true`
- `SOTERRA_AGENT_PROVIDER=huggingface`
- `SOTERRA_AGENT_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct`
- `SOTERRA_DOCUMENT_PARSE_PROVIDER=huggingface`
- `SOTERRA_DOCUMENT_PARSE_MODEL_ID=HuggingFaceTB/SmolVLM-256M-Instruct`
- `SOTERRA_EXTRACTION_PROVIDER=huggingface`
- `SOTERRA_EXTRACTION_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`
- `SOTERRA_REPOSITORY_MODE=supabase`
- `SOTERRA_STORAGE_MODE=supabase`
- `SOTERRA_EXTRACTOR_MODE=model`

Use [.env.example](/C:/repos/Soterra-_Client/.env.example) as the template.

## Local run steps

1. Install Python dependencies:
   `.\.venv\Scripts\python.exe -m pip install -e .`
2. Start the Python backend:
   `.\.venv\Scripts\python.exe -m uvicorn soterra_backend.api:app --host 127.0.0.1 --port 8001`
3. Start the frontend:
   `npm run dev`
4. Open the app and upload the PDFs through the Reports page.

## Vercel deployment

### Frontend

Deploy the Next.js app as the frontend project. Set `BACKEND_BASE_URL` to the public URL of the Python backend service.

### Backend

Deploy the Python package as a separate Vercel Python project or as a Vercel Service if that feature is available on the account. The FastAPI entrypoint is exposed through the `app` script in `pyproject.toml`.

Recommended backend environment variables on Vercel:

- `HF_TOKEN`
- `SOTERRA_AGENT_ENABLED=true`
- `SOTERRA_AGENT_PROVIDER=huggingface`
- `SOTERRA_AGENT_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct`
- `SOTERRA_DOCUMENT_PARSE_PROVIDER=huggingface`
- `SOTERRA_DOCUMENT_PARSE_MODEL_ID=HuggingFaceTB/SmolVLM-256M-Instruct`
- `SOTERRA_EXTRACTION_PROVIDER=huggingface`
- `SOTERRA_EXTRACTION_MODEL_ID=HuggingFaceTB/SmolLM2-1.7B-Instruct`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`
- `SOTERRA_REPOSITORY_MODE=supabase`
- `SOTERRA_STORAGE_MODE=supabase`
- `SOTERRA_EXTRACTOR_MODE=model`

### Supabase

1. Create a Supabase project.
2. Run the migration in `supabase/migrations/20260416000000_soterra_backend.sql`.
3. Create a private storage bucket named `inspection-reports`.
4. Add the project URL and service role key to the backend environment.

## What was tested here

I ran the backend in-process with FastAPI's test client and verified the main data routes against SQLite-backed extraction results.

The next step after that is browser-level verification against the real Next.js UI.

## Repeatable QA checks (routes + DB + uploads)

These are the commands I used to validate the scenarios you listed (duplicate upload prevention, extraction, DB writes, analytics, and frontend rendering).

### 1) Run backend route + DB integration tests

Runs an end-to-end upload (PDF multipart) into the FastAPI app, validates dedupe by `file_hash`, asserts extracted findings exist in `documents/findings`, and checks the SQLite analytics views.

`.\.venv\Scripts\python.exe -m unittest discover -s backend\soterra_backend\tests -p "test_*.py" -v`

### 2) Run frontend E2E upload test (Playwright)

Starts:
- FastAPI backend on `http://127.0.0.1:8001` (demo extractor, model extraction disabled)
- Next.js dev server on `http://127.0.0.1:3000`

Then:
- sets the auth cookie
- uploads a fixture PDF via the UI
- uploads the same PDF again and asserts it is treated as a duplicate
- opens the report detail page and checks the extracted issue register renders

`npx playwright test -c playwright.config.ts`

### 3) Clear DB before manual validation

The automated tests use their own scratch DBs, but for manual testing you typically want the local demo DB to be empty first.

Reset the local demo DB (and Playwright scratch DB) with:

`powershell -ExecutionPolicy Bypass -File .\scripts\reset-backend-demo-data.ps1`

Optional: also delete stored uploaded report files under `artifacts\backend\storage\rpt-*`:

`powershell -ExecutionPolicy Bypass -File .\scripts\reset-backend-demo-data.ps1 -AlsoDeleteStoredFiles`
