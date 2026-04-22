# Open-Source Notebook Approach

## Short answer

Yes. We can build a free, local-first proof of concept in a Python notebook that does:

1. scanned PDF OCR
2. field and finding extraction
3. PII detection and redaction
4. schema validation
5. database writes for dashboard metrics

It will not be as strong as the best paid document AI services on poor scans, but it is absolutely good enough to benchmark and likely good enough for an initial production version if the report templates stay fairly consistent.

## Recommended free stack

### OCR

- `OCRmyPDF`
- `Tesseract`

Why:

- works on scanned PDFs
- adds a searchable OCR text layer back into the PDF
- can run fully offline

### PDF/text extraction

- `pymupdf` for PDF/page handling
- `pdfplumber` for text blocks and table-like extraction
- optional `unstructured` or `docling` for document chunking/structure experiments

### PII detection and redaction

- `Microsoft Presidio`

Why:

- open source
- supports detection plus anonymization
- can be extended with regex and custom recognizers for inspection-report-specific entities

### Schema validation

- `pydantic` v2
- JSON Schema

### Storage

- `Postgres` for the real app
- optional `DuckDB` in notebook for fast local experiments

### Async package later

- `httpx`
- `asyncio`
- `tenacity`
- `asyncpg` or SQLAlchemy async

## Suggested notebook workflow

### Notebook 1: OCR benchmark

Input:

- the three Kauri PDFs

Steps:

1. run OCR on each PDF with `OCRmyPDF`
2. extract page text with `pymupdf`
3. inspect text quality manually
4. measure OCR time per document

Outputs:

- OCRed PDF
- extracted text per page
- timing metrics

### Notebook 2: extraction prototype

Input:

- OCR text from notebook 1

Steps:

1. split by page and section
2. use deterministic parsing first:
   - headers
   - dates
   - inspector names
   - issue tables
   - pass/fail phrases
3. normalize findings into the schema
4. validate with `pydantic`

Outputs:

- one JSON payload per document
- validation pass/fail results

### Notebook 3: PII redaction prototype

Steps:

1. run `Presidio` on extracted text
2. redact names, emails, phone numbers, addresses
3. keep raw values only in restricted outputs
4. create UI-safe redacted JSON

Outputs:

- raw JSON
- redacted JSON
- PII entity list

### Notebook 4: persistence and metrics

Steps:

1. write normalized rows to `Postgres` or `DuckDB`
2. compute dashboard metrics from the written data
3. confirm derived values match the current UI contract

Outputs:

- `reports`
- `report_findings`
- `pii_entities`
- aggregate metric queries

## Best open-source solution shortlist

| Option | OCR | Structure extraction | PII | Best use |
|---|---|---|---|---|
| `OCRmyPDF + Tesseract + pdfplumber + Presidio` | Strong baseline | Medium | Strong | safest first notebook path |
| `OCRmyPDF + Tesseract + unstructured + Presidio` | Strong baseline | Medium-Strong | Strong | better chunking/element parsing |
| `docTR + pymupdf + Presidio` | Strong on clean pages | Medium | Strong | model-based OCR experiments |
| `Docling + Presidio` | Medium-Strong | Strong | Strong | structure-heavy extraction experiments |

## My recommendation for the notebook phase

Start with:

`OCRmyPDF + Tesseract + pymupdf/pdfplumber + Presidio + pydantic`

Why:

- easiest to run locally
- lowest setup risk
- most transparent for debugging
- strong enough to benchmark extraction on your attached reports

## What this can and cannot do

### It can do well

- typed text OCR on scanned PDFs
- report-level field extraction
- issue/finding extraction from repeated layouts
- PII masking in extracted text
- schema-conformant JSON generation
- writes to database for dashboard metrics

### It may struggle with

- badly skewed or low-resolution scans
- handwritten notes
- highly irregular tables
- perfect visual redaction on page images without extra bbox mapping work

## Production note

A notebook is the right place to prove:

1. field coverage
2. extraction accuracy
3. redaction quality
4. schema conformance

Once the notebook performs well enough, we should convert the same logic into a Python worker package so it fits your async jobs architecture cleanly.

## Suggested benchmark decision rule

Promote the open-source path to production if it hits:

- at least 90% report-level field accuracy
- at least 85% finding recall
- at least 95% schema validation success
- at least 95% PII recall on required entity types

## Useful official references

- OCRmyPDF docs: <https://ocrmypdf.readthedocs.io/>
- Tesseract docs: <https://tesseract-ocr.github.io/>
- Presidio docs: <https://microsoft.github.io/presidio/>
- Unstructured docs: <https://docs.unstructured.io/open-source/core-functionality/partitioning>
- docTR repository: <https://github.com/mindee/doctr>

