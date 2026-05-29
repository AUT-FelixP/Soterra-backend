from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from ..agent import SoterraAgentService
from ..email_service import EmailService
from ..models import AuthSession
from ..repositories.base import RepositoryBackend
from ..services import DashboardService, IssueService, ReportUploadService
from ..storage.base import StorageBackend

DEFAULT_TENANT_ID = "ten-default"
DEFAULT_USER_ID = "usr-default-admin"


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    user_id: str
    role: str | None = None
    session: AuthSession | None = None


def get_auth_context(request: Request) -> AuthContext:
    session = getattr(request.state, "auth_session", None)
    if session:
        return AuthContext(
            tenant_id=session.user.tenant_id,
            user_id=session.user.id,
            role=session.user.role,
            session=session,
        )

    tenant_id = request.headers.get("X-Soterra-Tenant-Id") or DEFAULT_TENANT_ID
    user_id = request.headers.get("X-Soterra-User-Id") or DEFAULT_USER_ID
    return AuthContext(tenant_id=tenant_id, user_id=user_id)


def require_auth_context(request: Request) -> AuthContext:
    session = getattr(request.state, "auth_session", None)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return get_auth_context(request)


def get_repository(request: Request) -> RepositoryBackend:
    return request.app.state.repository


def get_storage(request: Request) -> StorageBackend:
    return request.app.state.storage


def get_email_service(request: Request) -> EmailService:
    return request.app.state.email_service


def get_report_service(request: Request) -> ReportUploadService:
    return request.app.state.report_service


def get_issue_service(request: Request) -> IssueService:
    return request.app.state.issue_service


def get_dashboard_service(request: Request) -> DashboardService:
    return request.app.state.dashboard_service


def get_agent_service(request: Request) -> SoterraAgentService:
    return request.app.state.agent_service
