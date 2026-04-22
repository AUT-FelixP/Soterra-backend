# Report Extraction Targets

This document maps the current client contracts to the document fields a backend extraction pipeline should populate.

## 1. Fields that can be extracted directly from inspection reports

### Report-level fields

These align with the current report list and report detail views.

| Field | Type | Current client usage |
|---|---|---|
| `project_name` | string | reports list, report detail |
| `site_name` | string | reports list, report detail, tracker filters |
| `inspection_date` | date | reports list (`createdAt`) |
| `inspection_type` | enum/string | performance, risk, tracker grouping |
| `trade` | string | report detail |
| `inspector_name` | string | report detail |
| `report_status` | enum | reports list/detail |
| `source_file_name` | string | ingestion traceability |
| `document_type` | enum | routing and parser selection |
| `pages` | integer | ingestion traceability |

### Finding-level fields

These are the most important rows for dashboard metrics and the live tracker.

| Field | Type | Current client usage |
|---|---|---|
| `finding_title` | string | report detail issues, top failures |
| `finding_description` | string | tracker detail, issue register |
| `severity` | enum (`Low`, `Medium`, `High`, `Critical`) | reports detail, issue rollups |
| `status` | enum (`Open`, `Ready`, `Closed`) | tracker |
| `date_identified` | date | tracker |
| `location_ref` | string | optional, useful for project detail |
| `trade` | string | top failure grouping |
| `category` | string | root causes, repeated patterns |
| `evidence_text` | string | human QA and auditability |
| `page_number` | integer | review/redaction |
| `bbox` | json | image/PDF redaction, UI evidence links |

### Contact and PII-bearing fields

These should be extracted into a secure raw layer, then conditionally redacted for UI use.

| Field | Type | Notes |
|---|---|---|
| `inspector_name` | string | PII |
| `inspector_email` | string | PII |
| `subcontractor_name` | string | potentially sensitive |
| `subcontractor_email` | string | PII |
| `consultant_name` | string | potentially sensitive |
| `consultant_email` | string | PII |
| `phone_numbers` | string[] | PII |
| `street_addresses` | string[] | PII |

## 2. Dashboard metrics derivable from extracted rows

These can be computed from normalized report and finding tables.

| Metric | Derivation |
|---|---|
| `total_inspections` | count of reports |
| `total_issues_found` | count of findings |
| `failure_rate` | failed inspections / total inspections |
| `reinspection_rate` | findings or inspections requiring reinspection / total |
| `issues_per_inspection` | findings / reports |
| `open_issues` | findings where `status = Open` |
| `ready_for_inspection` | findings where `status = Ready` |
| `closed_last_7_days` | findings closed in last 7 days |
| `top_failure_drivers` | grouped findings by normalized issue/category |
| `failure_distribution` | grouped findings by issue/category |
| `recurring_risk` | repeated issue frequency across reports/projects |
| `highest_severity_per_report` | max severity per report |

## 3. Dashboard fields that are not document-extract-only

These need workflow, schedule, or historical state beyond one uploaded PDF.

| Field | Why it cannot come only from a PDF |
|---|---|
| `daysAway` / upcoming inspection dates | requires program/schedule data |
| `riskLevel` for upcoming inspections | requires historical model plus program data |
| inspection matrix / lot progress | requires project planning state |
| `daysOpen` | requires issue lifecycle timestamps |
| `lastSentTo` | operational messaging metadata |
| close-out and recurrence effects | requires historical issue closure behavior |

## 4. Recommended normalized tables

Minimum backend tables:

1. `documents`
2. `document_pages`
3. `reports`
4. `report_findings`
5. `pii_entities`
6. `jobs`
7. `extraction_runs`

## 5. Proposed extraction payload

The worker should produce one validated payload per document:

```json
{
  "document": {
    "source_file_name": "Council Inspection - Kauri Apartments 09-04-24.pdf",
    "document_type": "council_inspection",
    "project_name": "Kauri Apartments",
    "site_name": "Kauri Apartments",
    "inspection_date": "2024-04-09",
    "inspection_type": "Council",
    "trade": "General",
    "inspector_name": "REDACTABLE",
    "report_status": "Completed",
    "pages": 9
  },
  "findings": [
    {
      "finding_title": "Example finding",
      "finding_description": "Example finding text from OCR/LLM extraction.",
      "severity": "High",
      "status": "Open",
      "date_identified": "2024-04-09",
      "category": "fire_stopping",
      "trade": "Fire",
      "page_number": 4,
      "bbox": {
        "x1": 0.11,
        "y1": 0.38,
        "x2": 0.74,
        "y2": 0.44
      }
    }
  ],
  "pii_entities": [
    {
      "entity_type": "PERSON",
      "text": "REDACTABLE",
      "page_number": 1
    }
  ]
}
```

