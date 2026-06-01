from __future__ import annotations

from fastapi import APIRouter, Depends

from ...schemas.issues import IssueUpdateRequest
from ...services.issue_service import IssueService
from ..dependencies import AuthContext, get_auth_context, get_issue_service, require_tenant_data_access

router = APIRouter(prefix="/issues", dependencies=[Depends(require_tenant_data_access)])


@router.get("")
def list_issues(
    context: AuthContext = Depends(get_auth_context),
    service: IssueService = Depends(get_issue_service),
) -> dict:
    return service.list_issues(tenant_id=context.tenant_id)


@router.get("/{issue_id}")
def get_issue(
    issue_id: str,
    context: AuthContext = Depends(get_auth_context),
    service: IssueService = Depends(get_issue_service),
) -> dict:
    return service.get_issue(tenant_id=context.tenant_id, issue_id=issue_id)


@router.patch("/{issue_id}")
def patch_issue(
    issue_id: str,
    payload: IssueUpdateRequest,
    context: AuthContext = Depends(get_auth_context),
    service: IssueService = Depends(get_issue_service),
) -> dict:
    return service.update_issue(
        tenant_id=context.tenant_id,
        issue_id=issue_id,
        status=payload.status,
        reinspections=payload.reinspections,
        last_sent_to=payload.lastSentTo,
    )
