from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..agent import SoterraAgentService
from ..config import Settings
from ..email_service import EmailService
from ..repository import build_repository
from ..services import DashboardService, InsightsAgentService, IssueService
from ..services.malware import NoopMalwareScanner
from ..services.report_service import ReportIngestionService, ReportUploadService
from ..storage import build_storage
from .routers import agent, auth, dashboard, health, issues, reports, tracker
from .security import bearer_token, set_auth_headers

PUBLIC_PATHS = {
    "/health",
    "/auth/login",
    "/auth/register",
    "/auth/forgot-password",
    "/auth/reset-password",
}


def create_app() -> FastAPI:
    settings = Settings.from_env()
    repository = build_repository(settings)
    repository.initialize()
    storage = build_storage(settings)
    malware_scanner = NoopMalwareScanner()
    email_service = EmailService(settings)
    ingestion_service = ReportIngestionService(
        settings=settings,
        repository=repository,
        storage=storage,
    )
    report_service = ReportUploadService(
        settings=settings,
        repository=repository,
        storage=storage,
        ingestion_service=ingestion_service,
        malware_scanner=malware_scanner,
    )
    issue_service = IssueService(repository)
    dashboard_service = DashboardService(repository)
    agent_service = SoterraAgentService(repository=repository)
    insights_agent_service = InsightsAgentService(
        repository=repository,
        agent_service=agent_service,
        settings=settings,
    )

    app = FastAPI(
        title="Soterra Backend",
        version="0.1.0",
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.state.settings = settings
    app.state.repository = repository
    app.state.storage = storage
    app.state.malware_scanner = malware_scanner
    app.state.email_service = email_service
    app.state.ingestion_service = ingestion_service
    app.state.report_service = report_service
    app.state.issue_service = issue_service
    app.state.dashboard_service = dashboard_service
    app.state.agent_service = agent_service
    app.state.insights_agent_service = insights_agent_service

    register_auth_middleware(app)
    register_routers(app)
    return app


def register_auth_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def require_valid_tenant_session(request: Request, call_next):
        public_paths = set(PUBLIC_PATHS)
        if request.app.state.settings.enable_docs:
            public_paths.update({"/docs", "/redoc", "/openapi.json"})
        if request.method == "OPTIONS" or request.url.path in public_paths:
            return await call_next(request)

        access_token = bearer_token(request)
        if not access_token:
            return JSONResponse({"detail": "Authentication required."}, status_code=401)

        session = request.app.state.repository.get_auth_session(access_token=access_token)
        if not session:
            return JSONResponse({"detail": "Invalid or expired session."}, status_code=401)

        request.state.auth_session = session
        set_auth_headers(request, tenant_id=session.user.tenant_id, user_id=session.user.id)
        return await call_next(request)


def register_routers(app: FastAPI) -> None:
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(reports.router)
    app.include_router(issues.router)
    app.include_router(dashboard.router)
    app.include_router(tracker.router)
    app.include_router(agent.router)


@lru_cache(maxsize=1)
def _cached_app() -> FastAPI:
    return create_app()


app = _cached_app()
