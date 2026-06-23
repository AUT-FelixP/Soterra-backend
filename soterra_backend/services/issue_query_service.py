from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..repositories.base import RepositoryBackend


@dataclass(frozen=True)
class IssueQuery:
    project: str | None = None
    address: str | None = None
    site: str | None = None
    building: str | None = None
    level: str | None = None
    unit: str | None = None
    area: str | None = None
    trade: str | None = None
    severity: str | None = None
    status: str | None = None
    inspection_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    search_text: str | None = None
    min_confidence: float | None = None
    location_verified: bool | None = None


class IssueQueryService:
    """Single tenant-scoped issue discovery and analytics boundary."""

    def __init__(self, repository: RepositoryBackend) -> None:
        self.repository = repository

    def query(self, tenant_id: str, filters: IssueQuery | dict | None = None) -> dict:
        query = filters if isinstance(filters, IssueQuery) else IssueQuery(**(filters or {}))
        snapshot = self.repository.load_snapshot(tenant_id)
        items = [item for item in snapshot.findings if self._active(item) and self._matches(item, query)]
        return {"total": len(items), "items": [self._issue_card(item) for item in items], "facets": self._facets(items), "metrics": self._metrics(items)}

    def facets(self, tenant_id: str) -> dict:
        return self.query(tenant_id)["facets"]

    def location_tree(self, tenant_id: str) -> list[dict]:
        items = self.query(tenant_id)["items"]
        tree: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
        for item in items:
            tree[item["project"]][item["address"]][item["level"]].add(item["unit"])
        return [{"project": project, "addresses": [{"address": address, "levels": [{"level": level, "units": sorted(units), "issue_count": sum(1 for x in items if x["project"] == project and x["address"] == address and x["level"] == level)} for level, units in levels.items()]} for address, levels in addresses.items()]} for project, addresses in tree.items()]

    def data_quality(self, tenant_id: str) -> dict:
        snapshot = self.repository.load_snapshot(tenant_id)
        items = [item for item in snapshot.findings if self._active(item)]
        warnings = Counter(w for item in items for w in item.get("extraction_warnings", []))
        missing_location = sum(not self._exact_location(item) for item in items)
        duplicate_groups = sum(count > 1 for count in Counter((i.get("project_id"), i.get("title"), self._exact_location(i)) for i in items).values())
        completed_jobs = [job for job in snapshot.jobs if job.get("status") == "completed"]
        return {"reports_processed": len(completed_jobs), "issues_extracted": len(items), "failed_extractions": sum(job.get("status") == "failed" for job in snapshot.jobs), "average_confidence": round(sum(float(i.get("confidence") or 0) for i in items) / len(items), 3) if items else 0, "missing_location_count": missing_location, "missing_location_rate": round(missing_location / len(items), 3) if items else 0, "missing_source_quote_count": sum(not i.get("source_quote") for i in items), "low_confidence_count": sum(float(i.get("confidence") or 0) < .6 for i in items), "duplicate_issue_groups": duplicate_groups, "top_extraction_warnings": [{"warning": warning, "count": count} for warning, count in warnings.most_common(10)], "last_refresh_time": max((job.get("completed_at") or "" for job in snapshot.jobs), default=None)}

    def drillthrough(self, tenant_id: str, issue_id: str) -> dict | None:
        return next((item for item in self.query(tenant_id)["items"] if item["id"] == issue_id), None)

    @staticmethod
    def _active(item: dict) -> bool:
        return not item.get("deleted_at") and str(item.get("document_status") or "").lower() not in {"deleted", "stale"}

    def _matches(self, item: dict, q: IssueQuery) -> bool:
        pairs = ((q.project, (item.get("project_name"), item.get("project_slug"))), (q.address, (item.get("address"),)), (q.site, (item.get("site_name"),)), (q.building, (item.get("building"),)), (q.level, (item.get("level"),)), (q.unit, (item.get("unit_label"), item.get("unit_or_area"))), (q.area, (item.get("area"), item.get("unit_or_area"), item.get("location"))), (q.trade, (item.get("trade"),)), (q.severity, (item.get("severity"),)), (q.status, (item.get("status"),)), (q.inspection_type, (item.get("inspection_type"),)))
        for expected, values in pairs:
            if expected and str(expected).lower() not in {"all", "all projects", "all sites", "all trades", "all severities", "all statuses", "all inspection types"} and not any(str(expected).lower() in str(value or "").lower() for value in values):
                return False
        if q.min_confidence is not None and float(item.get("confidence") or 0) < q.min_confidence: return False
        if q.location_verified is not None and bool(self._exact_location(item)) != q.location_verified: return False
        haystack = " ".join(str(item.get(k) or "") for k in ("title", "description", "source_quote", "required_fix", "location", "unit_or_area"))
        if q.search_text and q.search_text.lower() not in haystack.lower(): return False
        created = str(item.get("created_at") or "")[:10]
        if q.date_from and created < q.date_from: return False
        if q.date_to and created > q.date_to: return False
        return True

    @staticmethod
    def _exact_location(item: dict) -> str | None:
        value = item.get("location") or item.get("unit_or_area") or item.get("unit_label")
        return str(value).strip() if value and str(value).strip().lower() not in {"site", "project", "building", "general", "various", "throughout"} else None

    def _issue_card(self, item: dict) -> dict:
        location = self._exact_location(item)
        warnings = list(item.get("extraction_warnings") or [])
        if not location and "Exact issue location needs manual confirmation." not in warnings: warnings.append("Exact issue location needs manual confirmation.")
        return {"id": item.get("id"), "title": item.get("issue_title") or item.get("title"), "exact_location": location, "project": item.get("project_name"), "project_slug": item.get("project_slug"), "address": item.get("address"), "site": item.get("site_name"), "building": item.get("building"), "level": item.get("level"), "unit": item.get("unit_label") or item.get("unit_or_area"), "area": item.get("area") or item.get("unit_or_area"), "severity": item.get("severity"), "trade": item.get("trade"), "status": item.get("status"), "inspection_type": item.get("inspection_type"), "what_happened": item.get("description"), "why_it_matters": item.get("plain_english_summary"), "what_to_do_next": item.get("required_fix"), "evidence_required": item.get("evidence_required") or [], "source_report": item.get("source_document"), "source_page": item.get("source_page"), "source_quote": item.get("source_quote"), "confidence": item.get("confidence"), "warnings": warnings}

    @staticmethod
    def _facets(items: list[dict]) -> dict:
        mapping = {"projects": "project_name", "addresses": "address", "sites": "site_name", "buildings": "building", "levels": "level", "units": "unit_label", "areas": "unit_or_area", "trades": "trade", "severities": "severity", "statuses": "status", "inspection_types": "inspection_type"}
        return {name: [{"value": value, "count": count} for value, count in Counter(str(i.get(key)) for i in items if i.get(key)).most_common()] for name, key in mapping.items()}

    @staticmethod
    def _metrics(items: list[dict]) -> dict:
        return {"total": len(items), "open": sum(i.get("status") != "Closed" for i in items), "high_priority": sum(i.get("severity") in {"High", "Critical"} for i in items), "average_confidence": round(sum(float(i.get("confidence") or 0) for i in items) / len(items), 3) if items else 0}
