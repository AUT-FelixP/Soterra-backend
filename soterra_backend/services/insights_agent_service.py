from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
import json
import logging
import os
import time
from typing import Any

from ..analytics import build_insights_page
from ..models import RepositorySnapshot
from ..repositories.base import RepositoryBackend

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
VALID_SEVERITIES = {"Low", "Medium", "High", "Critical"}
MAX_FINDINGS_FOR_AI = 60


class InsightsAgentService:
    def __init__(
        self,
        repository: RepositoryBackend,
        agent_service: Any | None = None,
        settings: Any | None = None,
    ) -> None:
        self.repository = repository
        self.agent_service = agent_service
        self.settings = settings

    def build_ai_insights(self, *, tenant_id: str, inspection_type: str = "All") -> dict:
        selected = _normalize_filter(inspection_type)
        snapshot = self._load_snapshot_with_retry(tenant_id)
        if snapshot is None:
            return _repository_unavailable_response(selected)
        findings = _filter_findings(snapshot.findings, selected)
        deterministic = build_insights_page(snapshot, _legacy_filter(selected))
        fallback = self._fallback_response(
            findings=findings,
            deterministic=deterministic,
            selected=selected,
            all_findings=snapshot.findings,
        )

        try:
            ai_payload = self._generate_with_agent(
                findings_context=_compact_findings(findings),
                deterministic=deterministic,
                selected=selected,
            )
            return _merge_ai_payload(fallback, ai_payload)
        except Exception as exc:
            logger.info("ai_insights_fallback tenant=%s reason=%s", tenant_id, type(exc).__name__)
            return fallback

    def _load_snapshot_with_retry(self, tenant_id: str) -> RepositorySnapshot | None:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return self.repository.load_snapshot(tenant_id)
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.2)
        logger.warning("ai_insights_snapshot_unavailable tenant=%s reason=%s", tenant_id, type(last_error).__name__ if last_error else "unknown")
        return None

    def _generate_with_agent(self, *, findings_context: list[dict], deterministic: dict, selected: str) -> dict:
        if not findings_context:
            raise RuntimeError("No findings available for AI insights.")
        if not self.agent_service:
            raise RuntimeError("Agent service is not configured.")
        status = self.agent_service.status() if hasattr(self.agent_service, "status") else {}
        if status and not (status.get("enabled") and status.get("configured")):
            raise RuntimeError("Agent service is disabled or not configured.")

        prompt = _build_prompt(findings_context=findings_context, deterministic=deterministic, selected=selected)

        if hasattr(self.agent_service, "generate_inspection_insights"):
            raw = self.agent_service.generate_inspection_insights(prompt=prompt)
            return _parse_json_payload(raw)

        if not hasattr(self.agent_service, "_build_model"):
            raise RuntimeError("Agent service does not expose a model client.")

        model = self.agent_service._build_model()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a construction inspection advisor. Return valid JSON only. "
                    "Use only the tenant-scoped findings provided by the user."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        raw = model(messages) if callable(model) else model.generate(messages)
        return _parse_json_payload(raw)

    def _fallback_response(
        self,
        *,
        findings: list[dict],
        deterministic: dict,
        selected: str,
        all_findings: list[dict],
    ) -> dict:
        partitions = _partition_findings_by_lifecycle(findings)
        repeated = _fallback_repeated_patterns(findings)
        root_causes = _fallback_root_causes(partitions["current"], partitions["historical"])
        high_risk = _fallback_high_risk_areas(partitions["current"])
        current_actions = _fallback_current_project_actions(partitions["current"])
        checklist = _fallback_checklist(partitions["current"])
        historical_lessons = _fallback_historical_lessons(partitions["historical"])
        options = ["All"] + sorted({item.get("inspection_type") for item in all_findings if item.get("inspection_type")})

        summary = []
        if current_actions:
            summary.append(f"{len(current_actions)} current project action(s) need attention before the next inspection.")
        if repeated:
            summary.append(f"The most repeated pattern is {repeated[0]['issue']}; make this a pre-check item before booking the next inspection.")
        if high_risk:
            summary.append(f"{high_risk[0]['area']} is currently the highest-risk area in the selected findings.")
        if root_causes:
            summary.append(f"The strongest root cause signal is {root_causes[0]['cause']}; assign ownership and evidence before inspection day.")
        if not summary:
            summary.append("No extracted findings match this filter yet. Upload more reports or choose a broader inspection type.")

        return {
            "title": "AI inspection learning",
            "description": "Practical lessons from your uploaded inspection reports to help teams prepare for future inspections.",
            "filter": {"selected": selected, "options": options},
            "generatedAt": datetime.now(tz=UTC).isoformat(),
            "dataScope": "tenant",
            "aiAvailable": False,
            "fallbackMessage": "AI-enhanced insights are unavailable right now, so these recommendations use deterministic inspection analytics.",
            "confidenceNote": "Generated from tenant-scoped extracted findings. AI guidance falls back to deterministic analytics when the model is unavailable.",
            "executiveSummary": summary[:4],
            "currentProjectActions": current_actions,
            "preInspectionChecklist": checklist,
            "historicalLessons": historical_lessons,
            "repeatedPatterns": repeated,
            "highRiskAreas": high_risk,
            "rootCauses": root_causes,
            "suggestedQuestions": _suggested_questions(selected),
            "learningInsights": [],
            "oldProjectLessons": historical_lessons,
            "suggestedAgentQuestions": _suggested_questions(selected),
        }


def _build_prompt(*, findings_context: list[dict], deterministic: dict, selected: str) -> str:
    schema = {
        "executiveSummary": ["string"],
        "currentProjectActions": [
            {"issue": "string", "location": "string", "severity": "Low|Medium|High|Critical", "trade": "string", "category": "string", "evidenceRequired": ["string"], "nextAction": "string"}
        ],
        "preInspectionChecklist": [
            {
                "item": "string",
                "reason": "string",
                "evidenceRequired": ["string"],
                "priority": "Low|Medium|High|Critical",
            }
        ],
        "historicalLessons": [
            {
                "lesson": "string",
                "seenInProjects": ["string"],
                "pattern": "string",
                "recommendationForNewProjects": "string",
            }
        ],
        "repeatedPatterns": [
            {
                "issue": "string",
                "occurrence": "string",
                "inspectionsAffected": "string",
                "occurrenceCount": "number",
                "projectCount": "number",
                "inspectionCount": "number",
                "highestSeverity": "Low|Medium|High|Critical",
                "aiRecommendation": "string",
            }
        ],
        "highRiskAreas": [{"area": "string", "riskReason": "string", "recommendedAction": "string"}],
        "rootCauses": [{"cause": "string", "scope": "current|historical|mixed", "explanation": "string", "preventionSteps": ["string"]}],
        "suggestedQuestions": ["string"],
    }
    return json.dumps(
        {
            "instructions": [
                "Act as a construction inspection advisor.",
                "Use only the provided tenant-scoped data.",
                "Do not classify active/open project findings as completed project lessons.",
                "Historical lessons must only use findings marked completed, closed, or archived.",
                "Repeated patterns must represent issues that occur more than once across findings, reports, or projects.",
                "Explain insights in plain English for builders and site managers.",
                "Focus on how to pass future inspections.",
                "Convert repeated old project issues into prevention advice for new projects.",
                "Highlight high-risk trades, locations, root causes, and evidence gaps.",
                "Avoid pretending to know building code clauses unless the source data explicitly contains them.",
                "Avoid generic advice.",
                "Return valid JSON only.",
                "Keep all recommendations grounded in the provided findings.",
            ],
            "inspectionTypeFilter": selected,
            "deterministicAnalytics": deterministic,
            "findings": findings_context,
            "requiredJsonShape": schema,
        },
        ensure_ascii=True,
    )


def _repository_unavailable_response(selected: str) -> dict:
    return {
        "title": "AI inspection learning",
        "description": "Practical lessons from your uploaded inspection reports to help teams prepare for future inspections.",
        "filter": {"selected": selected, "options": [selected] if selected != "All" else ["All"]},
        "generatedAt": datetime.now(tz=UTC).isoformat(),
        "dataScope": "tenant",
        "aiAvailable": False,
        "fallbackMessage": "AI-enhanced insights are unavailable because tenant inspection data could not be loaded.",
        "confidenceNote": "Tenant-scoped inspection data could not be loaded just now. Try refreshing; no cross-tenant data was used.",
        "executiveSummary": [
            "Inspection learning is temporarily unavailable because the tenant report snapshot could not be loaded.",
            "Refresh the page once the backend data connection recovers.",
        ],
        "currentProjectActions": [],
        "learningInsights": [],
        "preInspectionChecklist": [],
        "historicalLessons": [],
        "oldProjectLessons": [],
        "repeatedPatterns": [],
        "highRiskAreas": [],
        "rootCauses": [],
        "suggestedQuestions": _suggested_questions(selected),
        "suggestedAgentQuestions": _suggested_questions(selected),
    }


def _compact_findings(findings: list[dict]) -> list[dict]:
    ranked = sorted(
        findings,
        key=lambda item: (
            -SEVERITY_ORDER.get(str(item.get("severity") or "Low"), 1),
            -_safe_int(item.get("recurrence_risk")),
            str(item.get("title") or ""),
        ),
    )
    compact = []
    for item in ranked[: int(os.getenv("SOTERRA_AI_INSIGHTS_MAX_FINDINGS", str(MAX_FINDINGS_FOR_AI)))]:
        compact.append(
            {
                "title": item.get("title"),
                "description": item.get("description"),
                "severity": _severity(item.get("severity")),
                "trade": item.get("trade"),
                "category": item.get("category"),
                "location": item.get("location"),
                "inspection_type": item.get("inspection_type"),
                "required_fix": item.get("required_fix"),
                "evidence_required": item.get("evidence_required") or [],
                "recurrence_risk": _safe_int(item.get("recurrence_risk")),
                "project_name": item.get("project_name"),
                "project_lifecycle": _project_lifecycle(item),
            }
        )
    return compact


def _merge_ai_payload(fallback: dict, ai_payload: dict) -> dict:
    merged = {**fallback}
    for key in [
        "executiveSummary",
        "suggestedQuestions",
    ]:
        value = ai_payload.get(key)
        if isinstance(value, list) and value:
            merged[key] = value
    if "suggestedQuestions" not in merged and isinstance(ai_payload.get("suggestedAgentQuestions"), list):
        merged["suggestedQuestions"] = ai_payload["suggestedAgentQuestions"]
    merged["suggestedAgentQuestions"] = merged.get("suggestedQuestions") or merged.get("suggestedAgentQuestions") or []
    merged["aiAvailable"] = True
    merged["fallbackMessage"] = None
    merged["confidenceNote"] = "Generated from tenant-scoped extracted findings using Soterra AI, with deterministic analytics used as guardrails."
    return _coerce_response_shape(merged)


def _coerce_response_shape(payload: dict) -> dict:
    payload["currentProjectActions"] = [
        {
            "issue": str(item.get("issue") or "Current finding"),
            "location": str(item.get("location") or "Project-wide"),
            "severity": _severity(item.get("severity")),
            "trade": str(item.get("trade") or "General"),
            "category": _insight_category(item),
            "evidenceRequired": _string_list(item.get("evidenceRequired")) or ["Close-out photo"],
            "nextAction": str(item.get("nextAction") or "Assign an owner and close this before inspection."),
        }
        for item in payload.get("currentProjectActions", [])
    ][:8]
    payload["learningInsights"] = [
        {
            "title": str(item.get("title") or "Inspection lesson"),
            "explanation": str(item.get("explanation") or "This pattern appears in the selected findings."),
            "whyItMatters": str(item.get("whyItMatters") or "It can delay sign-off or trigger reinspection."),
            "howToAvoid": _string_list(item.get("howToAvoid")) or ["Check and close this item before inspection day."],
            "relatedTrades": _string_list(item.get("relatedTrades")) or ["General"],
            "relatedInspectionTypes": _string_list(item.get("relatedInspectionTypes")) or ["General"],
            "severity": _severity(item.get("severity")),
        }
        for item in payload.get("learningInsights", [])
    ][:6]
    payload["preInspectionChecklist"] = [
        {
            "item": str(item.get("item") or "Complete pre-inspection check"),
            "reason": str(item.get("reason") or "Reduces the chance of repeat findings."),
            "evidenceRequired": _string_list(item.get("evidenceRequired")) or ["Close-out photo"],
            "priority": _severity(item.get("priority")),
        }
        for item in payload.get("preInspectionChecklist", [])
    ][:8]
    payload["oldProjectLessons"] = [
        {
            "lesson": str(item.get("lesson") or "Repeat issue found in old projects"),
            "seenInProjects": _string_list(item.get("seenInProjects"))[:5],
            "pattern": str(item.get("pattern") or "Repeated inspection finding"),
            "recommendationForNewProjects": str(item.get("recommendationForNewProjects") or "Add this to pre-start and pre-inspection QA."),
        }
        for item in payload.get("oldProjectLessons", [])
    ][:6]
    payload["historicalLessons"] = [
        {
            "lesson": str(item.get("lesson") or "Completed project lesson"),
            "seenInProjects": _string_list(item.get("seenInProjects"))[:5],
            "pattern": str(item.get("pattern") or "Repeated issue from completed work"),
            "recommendationForNewProjects": str(item.get("recommendationForNewProjects") or "Add this to pre-start and pre-inspection QA."),
        }
        for item in payload.get("historicalLessons", payload.get("oldProjectLessons", []))
    ][:6]
    payload["oldProjectLessons"] = payload["historicalLessons"]
    payload["repeatedPatterns"] = [
        {
            "issue": str(item.get("issue") or "Repeated issue"),
            "occurrence": str(item.get("occurrence") or "Not specified"),
            "inspectionsAffected": str(item.get("inspectionsAffected") or "Not specified"),
            "occurrenceCount": _safe_int(item.get("occurrenceCount")),
            "projectCount": _safe_int(item.get("projectCount")),
            "inspectionCount": _safe_int(item.get("inspectionCount")),
            "relatedTrades": _string_list(item.get("relatedTrades")) or ["General"],
            "category": _insight_category(item),
            "highestSeverity": _severity(item.get("highestSeverity")),
            "aiRecommendation": str(item.get("aiRecommendation") or "Assign an owner and collect close-out evidence before reinspection."),
        }
        for item in payload.get("repeatedPatterns", [])
    ][:8]
    payload["highRiskAreas"] = [
        {
            "area": str(item.get("area") or "Project-wide"),
            "riskReason": str(item.get("riskReason") or "Findings are concentrated here."),
            "recommendedAction": str(item.get("recommendedAction") or "Inspect this area before booking the next inspection."),
        }
        for item in payload.get("highRiskAreas", [])
    ][:5]
    payload["rootCauses"] = [
        {
            "cause": str(item.get("cause") or "General"),
            "scope": str(item.get("scope") or "mixed"),
            "explanation": str(item.get("explanation") or "This category appears in the selected findings."),
            "preventionSteps": _string_list(item.get("preventionSteps")) or ["Assign ownership.", "Collect evidence.", "Verify before inspection."],
        }
        for item in payload.get("rootCauses", [])
    ][:5]
    payload["executiveSummary"] = _string_list(payload.get("executiveSummary"))[:4]
    payload["suggestedQuestions"] = _string_list(payload.get("suggestedQuestions", payload.get("suggestedAgentQuestions")))[:6]
    payload["suggestedAgentQuestions"] = payload["suggestedQuestions"]
    return payload


def _parse_json_payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    text = getattr(raw, "content", raw)
    if isinstance(text, list):
        text = "\n".join(str(part) for part in text)
    text = str(text).strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("AI insights response must be a JSON object.")
    return parsed


def _fallback_repeated_patterns(findings: list[dict]) -> list[dict]:
    grouped = _group_by_title(findings)
    output = []
    for issue, matches in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        project_count = len({item.get("project_slug") or item.get("project_name") for item in matches})
        inspection_count = len({item.get("document_id") for item in matches})
        occurrence_count = len(matches)
        # Business rule: a pattern is only repeated when it recurs across findings,
        # reports, or projects. One-off active findings stay in current actions.
        if occurrence_count < 2 and project_count < 2 and inspection_count < 2:
            continue
        output.append(
            {
                "issue": issue,
                "occurrence": f"{occurrence_count} occurrence(s)",
                "inspectionsAffected": str(inspection_count),
                "occurrenceCount": occurrence_count,
                "projectCount": project_count,
                "inspectionCount": inspection_count,
                "relatedTrades": [trade for trade, _ in Counter(item.get("trade") or "General" for item in matches).most_common(3)],
                "category": _dominant_category(matches),
                "highestSeverity": _highest_severity(matches),
                "aiRecommendation": f"Make '{issue}' a named pre-inspection check and require evidence before the next booking.",
            }
        )
    return output[:8]


def _fallback_root_causes(current_findings: list[dict], historical_findings: list[dict]) -> list[dict]:
    current_counts = Counter(_insight_category(item) for item in current_findings)
    historical_counts = Counter(_insight_category(item) for item in historical_findings)
    causes = sorted(set(current_counts) | set(historical_counts), key=lambda cause: (-(current_counts[cause] + historical_counts[cause]), cause))
    rows = []
    for cause in causes[:5]:
        if current_counts[cause] and historical_counts[cause]:
            scope = "mixed"
        elif current_counts[cause]:
            scope = "current"
        else:
            scope = "historical"
        rows.append(
            {
                "cause": cause,
                "scope": scope,
                "explanation": f"{cause} appears in {scope} inspection findings and should be managed as a specific QA hold point.",
                "preventionSteps": [
                    "Assign a responsible trade before inspection day.",
                    "Check affected locations against open findings.",
                    "Attach close-out photos or other evidence before requesting sign-off.",
                ],
            }
        )
    return rows


def _fallback_high_risk_areas(findings: list[dict]) -> list[dict]:
    counts = Counter((item.get("location") or item.get("site_name") or "Project-wide") for item in findings)
    return [
        {
            "area": str(area),
            "riskReason": f"{count} current finding(s) point to this area or location.",
            "recommendedAction": "Walk this area before inspection, close visible defects, and prepare evidence for completed fixes.",
        }
        for area, count in counts.most_common(5)
    ]


def _fallback_current_project_actions(findings: list[dict]) -> list[dict]:
    open_findings = [item for item in findings if item.get("status") != "Closed"]
    ranked = sorted(open_findings, key=lambda item: (-SEVERITY_ORDER.get(_severity(item.get("severity")), 1), str(item.get("title") or "")))
    rows = []
    for item in ranked[:8]:
        rows.append(
            {
                "issue": str(item.get("title") or "Current finding"),
                "location": str(item.get("location") or item.get("site_name") or "Project-wide"),
                "severity": _severity(item.get("severity")),
                "trade": str(item.get("trade") or "General"),
                "category": _insight_category(item),
                "evidenceRequired": _string_list(item.get("evidence_required")) or ["Close-out photo", "Trade QA confirmation"],
                "nextAction": str(item.get("required_fix") or "Assign the responsible trade, complete the fix, and attach close-out evidence."),
            }
        )
    return rows


def _fallback_checklist(findings: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in findings:
        if item.get("status") != "Closed":
            grouped[_insight_category(item)].append(item)
    rows = []
    for category, items in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))[:8]:
        evidence = []
        for item in items:
            evidence.extend(_string_list(item.get("evidence_required")))
        rows.append(
            {
                "item": f"{category}: verify {len(items)} open item(s)",
                "reason": "Generated from current open findings for the active project/report.",
                "evidenceRequired": list(dict.fromkeys(evidence))[:4] or ["Close-out photo", "Trade QA confirmation"],
                "priority": _highest_severity(items),
                "trade": _dominant_trade(items),
                "category": category,
            }
        )
    return rows


def _fallback_historical_lessons(findings: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in findings:
        grouped[_insight_category(item)].append(item)
    lessons = []
    for category, items in sorted(grouped.items(), key=lambda pair: (-len({item.get("project_name") for item in pair[1]}), pair[0]))[:6]:
        projects = sorted({str(item.get("project_name")) for item in items if item.get("project_name")})
        if not projects:
            continue
        titles = [title for title, _ in Counter(item.get("title") or "Completed project finding" for item in items).most_common(3)]
        lessons.append(
            {
                "lesson": f"{category} issues should be prevented before inspection, not fixed after failure.",
                "seenInProjects": projects[:5],
                "pattern": f"{', '.join(titles)} appeared {len(items)} time(s) across {len(projects)} completed/closed/archived project(s).",
                "recommendationForNewProjects": "Add this category to trade QA checklists and require evidence before requesting inspection.",
            }
        )
    return lessons


def _suggested_questions(selected: str) -> list[str]:
    suffix = "" if selected == "All" else f" for {selected}"
    return [
        f"What should we check before the next inspection{suffix}?",
        "Which trades need the most attention this week?",
        "What evidence should we prepare before reinspection?",
        "Which old project lessons apply to a new project?",
        "What are the highest-risk repeat failures?",
    ]


def _group_by_title(findings: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in findings:
        grouped[str(item.get("title") or "Untitled finding")].append(item)
    return grouped


def _partition_findings_by_lifecycle(findings: list[dict]) -> dict[str, list[dict]]:
    historical_lifecycles = {"completed", "closed", "archived"}
    current = [item for item in findings if _project_lifecycle(item) not in historical_lifecycles]
    current_project_keys = {_project_key(item) for item in current}
    historical = []
    for item in findings:
        # Business rule: completed-project lessons must never include the currently
        # active project/report, even if older records for the same project exist.
        if _project_lifecycle(item) in historical_lifecycles and _project_key(item) not in current_project_keys:
            historical.append(item)
    return {"current": current, "historical": historical}


def _filter_findings(findings: list[dict], inspection_type: str) -> list[dict]:
    if inspection_type in {"All", "All types", "All inspection types", "", None}:
        return findings
    return [item for item in findings if item.get("inspection_type") == inspection_type]


def _normalize_filter(value: str | None) -> str:
    value = (value or "All").strip()
    return "All" if value in {"All types", "All inspection types", ""} else value


def _legacy_filter(value: str) -> str:
    return "All inspection types" if value == "All" else value


def _highest_severity(items: list[dict]) -> str:
    highest = "Low"
    for item in items:
        severity = _severity(item.get("severity"))
        if SEVERITY_ORDER[severity] > SEVERITY_ORDER[highest]:
            highest = severity
    return highest


def _project_lifecycle(item: dict) -> str:
    value = str(item.get("project_lifecycle") or item.get("project_status") or item.get("lifecycle") or "active").strip().lower()
    return value if value in {"active", "completed", "closed", "archived"} else "active"


def _project_key(item: dict) -> str:
    return str(item.get("project_slug") or item.get("project_id") or item.get("project_name") or "unknown-project")


def _insight_category(item: dict) -> str:
    raw = str(item.get("category") or item.get("trade") or item.get("title") or "General").lower()
    if any(term in raw for term in ["envelope", "flashing", "wrap", "weather"]):
        return "Envelope / Flashings"
    if "waterproof" in raw or "membrane" in raw:
        return "Waterproofing"
    if "fire" in raw:
        return "Passive Fire"
    if "cavity" in raw or "batten" in raw:
        return "Cavity System"
    if "plumb" in raw or "drain" in raw or "pipe" in raw:
        return "Plumbing"
    return str(item.get("category") or item.get("trade") or "General")


def _dominant_category(items: list[dict]) -> str:
    return Counter(_insight_category(item) for item in items).most_common(1)[0][0] if items else "General"


def _dominant_trade(items: list[dict]) -> str:
    return Counter(str(item.get("trade") or "General") for item in items).most_common(1)[0][0] if items else "General"


def _severity(value: Any) -> str:
    return str(value) if str(value) in VALID_SEVERITIES else "Low"


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []
