from __future__ import annotations

from fastapi import HTTPException

from ..analytics import (
    build_company_page,
    build_dashboard_insights_preview,
    build_dashboard_live_tracker,
    build_dashboard_overview,
    build_dashboard_risk,
    build_dashboard_top_failures,
    build_dashboard_upcoming_risk,
    build_insights_page,
    build_inspection_risk_page,
    build_legacy_insights_summary,
    build_performance_page,
    build_project_page,
)
from ..repositories.base import RepositoryBackend


class DashboardService:
    def __init__(self, repository: RepositoryBackend) -> None:
        self.repository = repository

    def overview(self, *, tenant_id: str) -> dict:
        return build_dashboard_overview(self.repository.load_snapshot(tenant_id))

    def company(self, *, tenant_id: str) -> dict:
        return build_company_page(self.repository.load_snapshot(tenant_id))

    def performance(self, *, tenant_id: str, inspection_type: str) -> dict:
        return build_performance_page(self.repository.load_snapshot(tenant_id), inspection_type)

    def insights(self, *, tenant_id: str, inspection_type: str) -> dict:
        return build_insights_page(self.repository.load_snapshot(tenant_id), inspection_type)

    def legacy_insights_summary(self, *, tenant_id: str) -> dict:
        return build_legacy_insights_summary(self.repository.load_snapshot(tenant_id))

    def project(self, *, tenant_id: str, slug: str) -> dict:
        payload = build_project_page(self.repository.load_snapshot(tenant_id), slug)
        if not payload:
            raise HTTPException(status_code=404, detail="Project not found")
        return payload

    def risk(self, *, tenant_id: str, site: str, window: str, inspection_id: str | None) -> dict:
        return build_dashboard_risk(self.repository.load_snapshot(tenant_id), site, window, inspection_id)

    def live_tracker(self, *, tenant_id: str) -> dict:
        return build_dashboard_live_tracker(self.repository.load_snapshot(tenant_id))

    def top_failures(self, *, tenant_id: str, inspection_type: str | None) -> dict:
        return build_dashboard_top_failures(self.repository.load_snapshot(tenant_id), inspection_type)

    def upcoming_risk(self, *, tenant_id: str) -> dict:
        return build_dashboard_upcoming_risk(self.repository.load_snapshot(tenant_id))

    def insights_preview(self, *, tenant_id: str) -> dict:
        return build_dashboard_insights_preview(self.repository.load_snapshot(tenant_id))

    def inspection_risk(
        self,
        *,
        tenant_id: str,
        site: str | None,
        date_range: str | None,
        inspection_type: str | None,
    ) -> dict:
        return build_inspection_risk_page(self.repository.load_snapshot(tenant_id), site, date_range, inspection_type)
