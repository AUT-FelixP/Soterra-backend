from __future__ import annotations

from fastapi import APIRouter, Depends

from ...services.dashboard_service import DashboardService
from ...services.insights_agent_service import InsightsAgentService
from ..dependencies import (
    AuthContext,
    get_auth_context,
    get_dashboard_service,
    get_insights_agent_service,
    require_tenant_data_access,
)

router = APIRouter(dependencies=[Depends(require_tenant_data_access)])


@router.get("/dashboard")
def dashboard(
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.overview(tenant_id=context.tenant_id)


@router.get("/dashboard/company")
def dashboard_company(
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.company(tenant_id=context.tenant_id)


@router.get("/dashboard/performance")
def dashboard_performance(
    inspectionType: str = "All types",
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.performance(tenant_id=context.tenant_id, inspection_type=inspectionType)


@router.get("/dashboard/insights")
def dashboard_insights(
    project: str = "All projects",
    site: str = "All sites",
    inspection_type: str = "All inspection types",
    trade: str = "All trades",
    severity: str = "All severities",
    status: str = "All statuses",
    date_range: str = "All time",
    inspectionType: str | None = None,
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.insights(
        tenant_id=context.tenant_id, project=project, site=site,
        inspection_type=inspectionType or inspection_type, trade=trade,
        severity=severity, status=status, date_range=date_range,
    )


@router.get("/dashboard/insights/ai")
def dashboard_ai_insights(
    inspectionType: str = "All",
    context: AuthContext = Depends(get_auth_context),
    service: InsightsAgentService = Depends(get_insights_agent_service),
) -> dict:
    return service.build_ai_insights(tenant_id=context.tenant_id, inspection_type=inspectionType)


@router.get("/insights")
def insights_summary(
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.legacy_insights_summary(tenant_id=context.tenant_id)


@router.get("/dashboard/project/{slug}")
def dashboard_project(
    slug: str,
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.project(tenant_id=context.tenant_id, slug=slug)


@router.get("/dashboard/risk")
def dashboard_risk(
    site: str = "All sites",
    window: str = "30d",
    inspectionId: str | None = None,
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.risk(tenant_id=context.tenant_id, site=site, window=window, inspection_id=inspectionId)


@router.get("/dashboard/live-tracker")
def dashboard_live_tracker(
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.live_tracker(tenant_id=context.tenant_id)


@router.get("/dashboard/top-failures")
def dashboard_top_failures(
    inspectionType: str | None = None,
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.top_failures(tenant_id=context.tenant_id, inspection_type=inspectionType)


@router.get("/dashboard/upcoming-risk")
def dashboard_upcoming_risk(
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.upcoming_risk(tenant_id=context.tenant_id)


@router.get("/dashboard/insights-preview")
def dashboard_insights_preview(
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.insights_preview(tenant_id=context.tenant_id)


@router.get("/inspection-risk")
def inspection_risk(
    site: str | None = None,
    dateRange: str | None = None,
    inspectionType: str | None = None,
    context: AuthContext = Depends(get_auth_context),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.inspection_risk(
        tenant_id=context.tenant_id,
        site=site,
        date_range=dateRange,
        inspection_type=inspectionType,
    )
