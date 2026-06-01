from __future__ import annotations

from fastapi import APIRouter, Depends

from ...schemas.issues import IssueUpdateRequest
from ...services.issue_service import IssueService
from ..dependencies import AuthContext, get_auth_context, get_issue_service, require_tenant_data_access

router = APIRouter(prefix="/tracker", dependencies=[Depends(require_tenant_data_access)])


@router.get("")
def tracker(
    site: str | None = None,
    search: str | None = None,
    status: str | None = None,
    type: str | None = None,
    dateRange: str | None = None,
    issueId: str | None = None,
    context: AuthContext = Depends(get_auth_context),
    service: IssueService = Depends(get_issue_service),
) -> dict:
    return service.tracker(
        tenant_id=context.tenant_id,
        filters={
            "site": site,
            "search": search,
            "status": status,
            "type": type,
            "dateRange": dateRange,
            "issueId": issueId,
        },
    )


@router.get("/{issue_id}")
def tracker_issue(
    issue_id: str,
    context: AuthContext = Depends(get_auth_context),
    service: IssueService = Depends(get_issue_service),
) -> dict:
    return service.get_tracker_issue(tenant_id=context.tenant_id, issue_id=issue_id)


@router.patch("/{issue_id}")
def patch_tracker_issue(
    issue_id: str,
    payload: IssueUpdateRequest,
    context: AuthContext = Depends(get_auth_context),
    service: IssueService = Depends(get_issue_service),
) -> dict:
    return service.update_tracker_issue(
        tenant_id=context.tenant_id,
        issue_id=issue_id,
        status=payload.status,
        reinspections=payload.reinspections,
        last_sent_to=payload.lastSentTo,
    )
