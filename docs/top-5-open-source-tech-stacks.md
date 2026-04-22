# Top 5 Open-Source Tech Stacks

This compares the best non-paid stacks for extracting structured inspection data and redacting PII from the current sample corpus.

## Benchmark corpus

Observed from the attached test data:

| Document | Pages | Size | Observation |
|---|---:|---:|---|
| `Council Inspection - Kauri Apartments 09-04-24.pdf` | 9 | 0.75 MB | scanned / image-first |
| `Fire Inspection - 07 Kauri Apartments.pdf` | 10 | 3.86 MB | scanned / image-first |
| `Services Inspection – Kauri Apartments – 09 April 2024.pdf` | 22 | 2.10 MB | scanned / image-first |

Total benchmark corpus: `3 PDFs`, `41 pages`

Important observed constraint:

- direct text extraction from the sample PDFs returned only page markers and almost no usable text
- therefore every viable free stack must include OCR

## Top 5 stacks to test

| Rank | Stack | OCR | Structure extraction | PII redaction | Main strength | Main weakness |
|---|---|---|---|---|---|---|
| 1 | `OCRmyPDF + Tesseract + pdfplumber + PyMuPDF + Presidio + Pydantic` | `OCRmyPDF/Tesseract` | `pdfplumber`, `PyMuPDF`, rules | `Presidio` | best first benchmark baseline, transparent and debuggable | more custom parsing work |
| 2 | `OCRmyPDF + Tesseract + Unstructured + Presidio + Pydantic` | `OCRmyPDF/Tesseract` | `Unstructured` element parsing | `Presidio` | better chunking and section segmentation | less deterministic than rule-first parsing |
| 3 | `docTR + PyMuPDF + Presidio + Pydantic` | `docTR` | custom rules | `Presidio` | stronger model-based OCR on clean scans | more setup, often benefits from GPU |
| 4 | `Docling + Presidio + Pydantic` | internal OCR/parse flow | `Docling` | `Presidio` | promising for structured document understanding | newer stack, less predictable ops |
| 5 | `PaddleOCR + LayoutParser + Presidio + Pydantic` | `PaddleOCR` | `LayoutParser`, custom rules | `Presidio` | strong layout-aware experimentation | highest implementation complexity |

## Recommended order to benchmark

1. `OCRmyPDF + Tesseract + pdfplumber + PyMuPDF + Presidio`
2. `OCRmyPDF + Tesseract + Unstructured + Presidio`
3. `docTR + PyMuPDF + Presidio`
4. `Docling + Presidio`
5. `PaddleOCR + LayoutParser + Presidio`

## Why these 5

These five cover the main open-source design patterns:

- OCR-first with deterministic parsing
- OCR-first with semantic document chunking
- neural OCR with custom parsing
- document-understanding-first
- layout-aware OCR with advanced region analysis

That gives a realistic evaluation spread without depending on paid APIs.

