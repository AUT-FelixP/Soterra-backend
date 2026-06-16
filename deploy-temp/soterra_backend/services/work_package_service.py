from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from ..extraction_quality import evidence_required, recommended_fix

_SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def build_work_packages(findings: Iterable[dict], *, limit: int | None = None) -> list[dict]:
    grouped: dict[tuple[str, str, str, str, str], list[dict]] = defaultdict(list)
    for finding in findings:
        if finding.get("status") == "Closed":
            continue
        grouped[_group_key(finding)].append(finding)

    packages = [_package(items) for items in grouped.values()]
    packages.sort(key=lambda item: (_SEVERITY_RANK.get(item["severity"], 9), -item["issue_count"], item["group_title"]))
    return packages[:limit] if limit is not None else packages


def build_todays_fix_list(findings: Iterable[dict], *, limit: int = 5) -> dict:
    packages = build_work_packages(findings, limit=limit)
    return {
        "title": "Today's Fix List",
        "items": [
            {
                "priority": index,
                "trade": item["trade"],
                "location": ", ".join(item["locations"]) or "Confirm exact area on site",
                "task": item["recommended_action"],
                "evidence_required": item["evidence_required"],
                "related_issue_count": item["issue_count"],
                "issue_ids": item["issue_ids"],
            }
            for index, item in enumerate(packages, start=1)
        ],
    }


def build_chat_cards(findings: Iterable[dict], *, mode: str = "summary_mode") -> dict:
    active = [item for item in findings if item.get("status") != "Closed"]
    packages = build_work_packages(active, limit=3)
    return {
        "mode": mode,
        "cards": [
            {
                "type": "ProjectSummaryCard",
                "open_issue_count": len(active),
                "high_priority_count": len([item for item in active if item.get("severity") in {"Critical", "High"}]),
                "project_count": len({item.get("project_name") for item in active if item.get("project_name")}),
            },
            *[{"type": "WorkPackageCard", **item} for item in packages],
            {"type": "FixPlanCard", **build_todays_fix_list(active, limit=3)},
            {
                "type": "EvidenceChecklistCard",
                "items": sorted({evidence for item in packages for evidence in item["evidence_required"]}),
            },
            {
                "type": "SourceReferenceCard",
                "items": sorted(
                    {
                        str(item.get("source_document") or item.get("source_report"))
                        for item in active
                        if item.get("source_document") or item.get("source_report")
                    }
                )[:5],
            },
        ],
        "actions": ["Show all issues", "Show by trade", "Show by location", "Create today's fix list", "Show evidence needed"],
    }


def _group_key(finding: dict) -> tuple[str, str, str, str, str]:
    return (
        str(finding.get("project_name") or "Unknown project"),
        str(finding.get("trade") or finding.get("category") or "General"),
        str(finding.get("severity") or "Medium"),
        str(finding.get("inspection_type") or "General inspection"),
        str(finding.get("root_cause") or finding.get("category") or "General defect"),
    )


def _package(items: list[dict]) -> dict:
    first = items[0]
    severity = min((str(item.get("severity") or "Medium") for item in items), key=lambda value: _SEVERITY_RANK.get(value, 9))
    locations = sorted({_location(item) for item in items if _location(item)})
    titles = [str(item.get("issue_title") or item.get("title") or "Recorded defect") for item in items]
    root_cause = str(first.get("root_cause") or first.get("category") or "General defect")
    trade = str(first.get("trade") or first.get("category") or "General")
    evidence = sorted({value for item in items for value in (item.get("evidence_required") or evidence_required(str(item.get("title") or "")))})
    return {
        "group_title": f"{trade} - {root_cause}",
        "project_name": first.get("project_name"),
        "trade": trade,
        "severity": severity,
        "inspection_type": first.get("inspection_type"),
        "issue_count": len(items),
        "locations": locations,
        "summary": "; ".join(titles[:4]),
        "recommended_action": recommended_fix(" ".join(titles)),
        "evidence_required": evidence,
        "issue_ids": [item.get("id") or item.get("issue_id") for item in items],
    }


def _location(item: dict) -> str:
    return str(item.get("unit_or_area") or item.get("location") or item.get("level") or "").strip()
