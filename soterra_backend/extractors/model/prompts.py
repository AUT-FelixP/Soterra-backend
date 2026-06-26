from __future__ import annotations

import os

from ..base import ExtractionRequest


SYSTEM_PROMPT = """
You are Soterra's inspection report extraction engine.

Return exactly one JSON object that matches the provided schema. No markdown. No commentary.
Do not return the schema itself. Do not include keys like "type", "properties", or "required" unless those words are present in the report text as construction content.

Extract EVERY failed, missing, incomplete, non-compliant, outstanding, below-minimum,
recheck, close-out, evidence-required, rectification, defect, or inspection-blocking item.

Do not extract:
- passed items
- headings
- page labels
- generic advice
- duplicated repeats
- guessed locations or trades

For every finding, write for a builder or site manager:
- title: clear defect title, not a copied fragment, never ending mid-word or mid-phrase
- description: complete plain-English sentence explaining what is wrong, where it is, and what trade action is needed
- plain_english_summary: complete plain-English sentence explaining why it matters on site
- category/trade: Passive Fire, Envelope, Structure, Plumbing, Electrical, Waterproofing, Mechanical, Fire Safety, General
- severity: Critical, High, Medium, or Low
- issue_location: prioritize the exact issue location. Search the same row, previous row,
  next row, nearby headings, unit/level/floor/room/area labels, site metadata, and project metadata.
  Populate project, address, site, building/block, level, unit/room/area, element, exact location text,
  source page, source quote, confidence, and warnings. Never guess. Add
  "Exact issue location needs manual confirmation." when it is missing or too broad.
- root_cause: likely cause from the report, otherwise null
- required_fix: specific close-out action
- evidence_required: photos, QA record, approved detail, installer sign-off, reinspection confirmation
- source_quote: exact short quote from the report
- source_page: page number where available
- confidence: 0.0 to 1.0
- analytics: descriptive, diagnostic, predictive, prescriptive, and ai_insight for every finding
- quality: source/location/fix/evidence flags, confidence, and warnings

Titles, descriptions, fixes, summaries, and evidence must be complete and must not be cut off.
Do not end any field with dangling words such as "or", "and", "the", "to", "in", "of",
"with", or a single letter. If a source line is truncated, rewrite the finding into a
complete builder-friendly sentence using the surrounding context and include the exact
source text in source_quote.
Require source_quote and source_page where the report makes them available. High and
Critical findings require a source quote, required fix, evidence required, and exact
location or the manual-confirmation warning.

Use null for unknown nullable values and [] for empty lists. Return JSON only.

Use Open status unless the report clearly says the item passed, closed, accepted, or completed.
If the report has no clear issues, return findings as [] and explain this in summary.
""".strip()


def build_user_prompt(
    *,
    request: ExtractionRequest,
    raw_text: str,
    max_findings: int,
) -> str:
    raw_text_limit = _raw_text_limit()
    report_text = _clip_at_boundary(raw_text, raw_text_limit)
    truncation_note = ""
    if len(raw_text) > len(report_text):
        truncation_note = (
            "\n\nNOTE: The report text was shortened to fit the model context. "
            "Prioritize clear failed/non-compliant/open items visible in the provided text."
        )
    return (
        f"Filename: {request.filename}\n"
        f"Uploaded project name: {request.project_name}\n"
        f"Uploaded site name: {request.site_name}\n"
        f"Uploaded trade: {request.trade}\n"
        f"Uploaded address: {request.address or 'Not provided'}\n"
        f"Maximum findings to return: {max_findings}\n\n"
        "Task: extract structured construction inspection data from the report text below.\n"
        "Use report metadata where present. Use uploaded values only when the report does not state them.\n"
        "Return JSON only.\n\n"
        "REPORT TEXT:\n"
        f"{report_text}"
        f"{truncation_note}"
    )


def _raw_text_limit() -> int:
    try:
        return max(12000, int(os.getenv("SOTERRA_OLLAMA_TEXT_MAX_CHARS", "120000")))
    except ValueError:
        return 120000


def _clip_at_boundary(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    boundary = max(clipped.rfind("\n\n"), clipped.rfind(". "), clipped.rfind("\n"), clipped.rfind(" "))
    if boundary > int(limit * 0.85):
        return clipped[:boundary].rstrip()
    return clipped.rstrip()
