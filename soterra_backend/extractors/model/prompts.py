from __future__ import annotations

from ..base import ExtractionRequest


SYSTEM_PROMPT = """
You are Soterra's inspection report extraction engine.

Return exactly one JSON object that matches the provided schema. No markdown. No commentary.

Extract only construction issues that are clearly supported by the report text:
- failed checklist items
- defects
- missing work
- incomplete work
- non-compliant work
- required close-out work
- inspection-blocking items

Do not extract:
- passed items
- headings
- page labels
- generic advice
- duplicated repeats
- guessed locations or trades

For every finding, write for a builder or site manager:
- title: clear defect title, not a copied fragment
- description: what is wrong
- plain_english_summary: why it matters on site
- category/trade: Passive Fire, Envelope, Structure, Plumbing, Electrical, Waterproofing, Mechanical, Fire Safety, General
- severity: Critical, High, Medium, or Low
- location fields: exact report location only; otherwise null
- root_cause: likely cause from the report, otherwise null
- required_fix: specific close-out action
- evidence_required: photos, QA record, approved detail, installer sign-off, reinspection confirmation
- source_quote: exact short quote from the report
- confidence: 0.0 to 1.0

Use Open status unless the report clearly says the item passed, closed, accepted, or completed.
If the report has no clear issues, return findings as [] and explain this in summary.
""".strip()


def build_user_prompt(
    *,
    request: ExtractionRequest,
    raw_text: str,
    max_findings: int,
) -> str:
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
        f"{raw_text[:48000]}"
    )
