# Extraction Performance Report

## Scope

This report focuses on extraction performance for the current sample corpus:

- `Council Inspection - Kauri Apartments 09-04-24.pdf`
- `Fire Inspection - 07 Kauri Apartments.pdf`
- `Services Inspection – Kauri Apartments – 09 April 2024.pdf`

Total: `3 scanned PDFs`, `41 pages`

## Important limitation

This report contains:

1. `Observed findings`
   based on direct inspection of the sample files in the current environment
2. `Forecast benchmark scores`
   based on stack fit for scanned inspection PDFs and the current extraction schema

This is not yet a measured notebook benchmark run, because the local environment does not currently have the Python/OCR stack installed. So the scores below should be used for stack selection and benchmark planning, not as final measured production results.

## 1. Observed findings from the sample data

### Corpus characteristics

| Signal | Observed result | Impact |
|---|---|---|
| Page count | 41 pages total | enough for an initial benchmark |
| PDF text layer | effectively absent | OCR required |
| Document style | image-first scanned reports | OCR quality will dominate extraction quality |
| Likely structure | repeated inspection-report layouts | rule-based normalization is feasible |

### What this means

- a non-OCR parser will fail on this corpus
- the best free stack is not the smartest parser, it is the one with the most reliable OCR plus enough structure handling
- PII redaction should happen after OCR and before UI-safe persistence

## 2. Scoring model

Forecast scores are out of 10 for extraction fitness on this corpus.

| Criterion | Weight |
|---|---:|
| OCR quality on scanned pages | 30 |
| finding extraction reliability | 25 |
| report metadata extraction | 15 |
| schema conformity potential | 10 |
| support for PII redaction workflow | 10 |
| implementation/debuggability | 10 |

## 3. Top 5 stack comparison

| Stack | OCR | Metadata extraction | Finding extraction | Schema fit | PII workflow fit | Debuggability | Weighted score / 10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `OCRmyPDF + Tesseract + pdfplumber + PyMuPDF + Presidio` | 8.0 | 8.0 | 7.5 | 8.5 | 9.0 | 9.0 | 8.2 |
| `OCRmyPDF + Tesseract + Unstructured + Presidio` | 8.0 | 8.2 | 7.8 | 8.0 | 9.0 | 7.8 | 8.1 |
| `docTR + PyMuPDF + Presidio` | 7.8 | 7.2 | 7.0 | 7.8 | 8.5 | 6.8 | 7.5 |
| `Docling + Presidio` | 7.2 | 8.0 | 7.6 | 8.0 | 8.2 | 6.9 | 7.6 |
| `PaddleOCR + LayoutParser + Presidio` | 7.8 | 7.8 | 7.9 | 7.5 | 8.6 | 5.8 | 7.5 |

## 4. Interpretation

### 1. OCRmyPDF + Tesseract + pdfplumber + PyMuPDF + Presidio

Best overall first candidate.

Why it scores highest:

- strongest balance of OCR reliability and parser transparency
- easiest to inspect page text, tables, and evidence regions
- easiest to make schema-safe for your `reports`, `report_findings`, and `pii_entities` model
- easiest to convert from notebook to async worker package later

Best fit for:

- the first end-to-end notebook
- production if report layouts are fairly stable

### 2. OCRmyPDF + Tesseract + Unstructured + Presidio

Best semantic parsing challenger.

Why it is close:

- same OCR strength as the baseline
- better at chunking semi-structured sections
- useful when issue sections move around between document types

Main risk:

- more abstraction means slightly less deterministic debugging than `pdfplumber` plus explicit rules

### 3. Docling + Presidio

Best structure-first challenger.

Why it is interesting:

- promising for layout-heavy documents
- could reduce custom section parsing if the reports are consistent

Main risk:

- less operational familiarity and less predictable extraction tuning than the baseline stack

### 4. docTR + PyMuPDF + Presidio

Best neural OCR challenger.

Why it is worth testing:

- can outperform Tesseract on some clean or complex scans
- useful if the reports have inconsistent fonts or embedded page artifacts

Main risk:

- more setup complexity
- often less convenient to debug and deploy than OCRmyPDF/Tesseract

### 5. PaddleOCR + LayoutParser + Presidio

Best advanced research challenger.

Why it is worth testing:

- strong option if region detection and layout segmentation become the main problem

Main risk:

- highest engineering overhead
- not the best first stack for a business benchmark unless simpler options fail

## 5. Extraction-specific recommendation

### Recommended baseline

`OCRmyPDF + Tesseract + pdfplumber + PyMuPDF + Presidio + Pydantic`

### Recommended challenger A

`OCRmyPDF + Tesseract + Unstructured + Presidio + Pydantic`

### Recommended challenger B

`Docling + Presidio + Pydantic`

### Recommended neural OCR challenger

`docTR + PyMuPDF + Presidio + Pydantic`

### Recommended advanced layout challenger

`PaddleOCR + LayoutParser + Presidio + Pydantic`

## 6. What to measure in the actual notebook benchmark

For each stack, measure:

| Metric | How to score |
|---|---|
| `report_field_accuracy` | exact match on project, site, date, inspector, type |
| `finding_recall` | extracted findings / expected findings |
| `finding_precision` | correct findings / extracted findings |
| `severity_accuracy` | exact severity match |
| `schema_pass_rate` | valid payloads / total payloads |
| `pii_recall` | detected sensitive entities / expected sensitive entities |
| `runtime_seconds` | full extraction wall time |
| `manual_fix_rate` | rows needing manual correction |

## 7. Decision guidance

Choose the stack that gives the best combination of:

1. `finding_recall`
2. `schema_pass_rate`
3. `pii_recall`
4. `debuggability`

For your current use case, that usually means preferring a slightly simpler stack with predictable output over a more advanced stack that is harder to troubleshoot.

