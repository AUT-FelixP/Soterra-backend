from __future__ import annotations

import time
from functools import lru_cache
from typing import Literal

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .analytics import (
    build_company_page,
    build_dashboard_insights_preview,
    build_dashboard_live_tracker,
    build_dashboard_overview,
    build_dashboard_risk,
    build_dashboard_top_failures,
    build_dashboard_upcoming_risk,
    build_insights_page,
    build_inspection_risk_page,
    build_issue_detail,
    build_issues_list,
    build_legacy_insights_summary,
    build_performance_page,
    build_project_page,
    build_report_detail,
    build_report_list,
    build_tracker_page,
)
from .config import Settings
from .email_service import EmailService
from .repository import build_repository
from .service import ReportIngestionService, UploadContext
from .storage import build_storage

DEFAULT_TENANT_ID = "ten-default"
DEFAULT_USER_ID = "usr-default-admin"
PDF_CONTENT_TYPES = {"application/pdf", "application/octet-stream"}
UPLOAD_CHUNK_SIZE = 1024 * 1024


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterAccountRequest(StrictRequestModel):
    tenantName: str | None = Field(default=None, min_length=1, max_length=160)
    company: str | None = Field(default=None, min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(StrictRequestModel):
    email: str | None = Field(default=None, min_length=3, max_length=254)
    username: str | None = Field(default=None, min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ForgotPasswordRequest(StrictRequestModel):
    email: str = Field(min_length=3, max_length=254)


class ResetPasswordRequest(StrictRequestModel):
    token: str = Field(min_length=16, max_length=512)
    password: str = Field(min_length=12, max_length=256)


class InviteMemberRequest(StrictRequestModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)


class BulkDeleteReportsRequest(StrictRequestModel):
    ids: list[str] = Field(min_length=1, max_length=100)


class IssueUpdateRequest(StrictRequestModel):
    status: Literal["Open", "Ready", "Closed"] | None = None
    reinspections: int | None = Field(default=None, ge=0, le=1000)
    lastSentTo: str | None = Field(default=None, max_length=254)


class _RateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = {}

    def check(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        starts_after = now - window_seconds
        attempts = [value for value in self._attempts.get(key, []) if value >= starts_after]
        allowed = len(attempts) < limit
        attempts.append(now)
        self._attempts[key] = attempts
        return allowed


rate_limiter = _RateLimiter()


def create_app() -> FastAPI:
    settings = Settings.from_env()
    repository = build_repository(settings)
    repository.initialize()
    storage = build_storage(settings)
    email_service = EmailService(settings)
    ingestion_service = ReportIngestionService(
        settings=settings,
        repository=repository,
        storage=storage,
    )

    app = FastAPI(
        title="Soterra Backend",
        version="0.1.0",
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    @app.middleware("http")
    async def require_valid_tenant_session(request: Request, call_next):
        public_paths = {
            "/health",
            "/auth/login",
            "/auth/register",
            "/auth/forgot-password",
            "/auth/reset-password",
        }
        if settings.enable_docs:
            public_paths.update({"/docs", "/redoc", "/openapi.json"})
        if request.method == "OPTIONS" or request.url.path in public_paths:
            return await call_next(request)

        access_token = _bearer_token(request)
        if not access_token:
            return JSONResponse({"detail": "Authentication required."}, status_code=401)

        session = repository.get_auth_session(access_token=access_token)
        if not session:
            return JSONResponse({"detail": "Invalid or expired session."}, status_code=401)

        request.state.auth_session = session
        _set_auth_headers(request, tenant_id=session.user.tenant_id, user_id=session.user.id)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/auth/register", status_code=201)
    def register_account(request: Request, payload: RegisterAccountRequest) -> dict:
        _check_rate_limit(request, "register", payload.email.lower(), limit=10, window_seconds=15 * 60)
        tenant_name = str(payload.tenantName or payload.company or "").strip()
        if not tenant_name:
            raise HTTPException(status_code=400, detail="Tenant name, name, email, and password are required.")
        try:
            session = repository.register_tenant(tenant_name=tenant_name, name=payload.name.strip(), email=payload.email.strip(), password=payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        email_service.send_registration_email(
            to_email=session.user.email,
            name=session.user.name,
            tenant_name=session.user.tenant_name,
        )
        return session.model_dump()

    @app.post("/auth/login")
    def login(request: Request, payload: LoginRequest) -> dict:
        email = str(payload.email or payload.username or "").strip()
        password = payload.password
        if not email or not password:
            raise HTTPException(status_code=401, detail="Invalid credentials.")
        _check_rate_limit(request, "login", email.lower(), limit=8, window_seconds=15 * 60)
        session = repository.authenticate_user(email=email, password=password)
        if not session:
            raise HTTPException(status_code=401, detail="Invalid credentials.")
        return session.model_dump()

    @app.post("/auth/forgot-password")
    def forgot_password(request: Request, payload: ForgotPasswordRequest) -> dict:
        email = payload.email.strip()
        _check_rate_limit(request, "forgot-password", email.lower(), limit=5, window_seconds=15 * 60)
        reset = repository.create_password_reset_token(email=email)
        response: dict = {
            "message": "If an account exists for this email, a password reset link has been sent.",
            "emailSent": False,
        }
        if reset:
            email_sent = email_service.send_password_reset_email(
                to_email=reset["email"],
                name=reset["name"],
                token=reset["token"],
            )
            response["emailSent"] = email_sent
            if settings.app_env != "production" and not email_sent:
                response["resetToken"] = reset["token"]
        return response

    @app.post("/auth/reset-password")
    def reset_password(request: Request, payload: ResetPasswordRequest) -> dict:
        _check_rate_limit(request, "reset-password", payload.token[:16], limit=8, window_seconds=15 * 60)
        session = repository.reset_password(token=payload.token.strip(), password=payload.password)
        if not session:
            raise HTTPException(status_code=400, detail="Invalid or expired password reset token.")
        return session.model_dump()

    @app.get("/auth/session")
    def auth_session(
        tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id"),
        user_id: str = Header(default=DEFAULT_USER_ID, alias="X-Soterra-User-Id"),
    ) -> dict:
        session = repository.get_user_session(user_id=user_id, tenant_id=tenant_id)
        if not session:
            raise HTTPException(status_code=401, detail="Invalid session.")
        return session.model_dump()

    @app.get("/tenants/members")
    def list_members(tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return {"items": repository.list_members(tenant_id=tenant_id)}

    @app.post("/tenants/members", status_code=201)
    def invite_member(
        request: Request,
        payload: InviteMemberRequest,
        tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id"),
        actor_user_id: str = Header(default=DEFAULT_USER_ID, alias="X-Soterra-User-Id"),
    ) -> dict:
        _check_rate_limit(request, "invite", tenant_id, limit=30, window_seconds=60 * 60)
        try:
            member = repository.invite_member(tenant_id=tenant_id, actor_user_id=actor_user_id, name=payload.name.strip(), email=payload.email.strip(), password=payload.password)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        actor_session = repository.get_user_session(user_id=actor_user_id, tenant_id=tenant_id)
        email_service.send_invitation_email(
            to_email=member["email"],
            name=member["name"],
            tenant_name=actor_session.user.tenant_name if actor_session else "your tenant",
        )
        return {"item": member}

    @app.delete("/tenants/members/{user_id}")
    def remove_member(
        user_id: str,
        tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id"),
        actor_user_id: str = Header(default=DEFAULT_USER_ID, alias="X-Soterra-User-Id"),
    ) -> dict:
        try:
            removed = repository.remove_member(tenant_id=tenant_id, actor_user_id=actor_user_id, user_id=user_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail="Member not found.")
        return {"removed": True}

    @app.get("/reports")
    def list_reports(tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_report_list(repository.load_snapshot(tenant_id))

    @app.delete("/reports")
    def bulk_delete_reports(
        payload: BulkDeleteReportsRequest,
        tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id"),
    ) -> dict:
        return _bulk_delete_reports(payload, tenant_id)

    @app.get("/reports/{report_id}")
    def get_report(report_id: str, tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        payload = build_report_detail(repository.load_snapshot(tenant_id), report_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Report not found")
        return payload

    @app.delete("/reports/{report_id}")
    def delete_report(report_id: str, tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        deleted = repository.delete_report(tenant_id, report_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Report not found")
        storage_path = deleted.get("storage_path")
        if storage_path:
            storage.delete(storage_path=storage_path)
        return {"deleted": True, "id": report_id}

    @app.post("/reports/bulk-delete")
    def bulk_delete_reports_alias(
        payload: BulkDeleteReportsRequest,
        tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id"),
    ) -> dict:
        return _bulk_delete_reports(payload, tenant_id)

    def _bulk_delete_reports(payload: BulkDeleteReportsRequest, tenant_id: str) -> dict:
        report_ids = payload.ids
        unique_ids = []
        seen = set()
        for value in report_ids:
            report_id = str(value or "").strip()
            if report_id and report_id not in seen:
                seen.add(report_id)
                unique_ids.append(report_id)
        if not unique_ids:
            raise HTTPException(status_code=400, detail="At least one report ID is required.")
        deleted_ids = []
        missing_ids = []
        for report_id in unique_ids:
            deleted = repository.delete_report(tenant_id, report_id)
            if not deleted:
                missing_ids.append(report_id)
                continue
            storage_path = deleted.get("storage_path")
            if storage_path:
                storage.delete(storage_path=storage_path)
            deleted_ids.append(report_id)
        return {"deleted": deleted_ids, "missing": missing_ids, "deletedCount": len(deleted_ids), "missingCount": len(missing_ids)}

    @app.post("/reports", status_code=201)
    async def create_report(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        project: str = Form(...),
        site: str = Form(...),
        status: str = Form("Reviewing"),
        inspector: str = Form(""),
        trade: str = Form("General"),
        tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id"),
    ) -> dict:
        _ = status
        _ = inspector
        content = await _read_limited_upload(file, settings.max_upload_bytes)
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        if (file.content_type or "application/pdf") not in PDF_CONTENT_TYPES or not content.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")

        upload_ctx = UploadContext(
            tenant_id=tenant_id,
            filename=file.filename or "uploaded-report.pdf",
            content=content,
            content_type=file.content_type or "application/pdf",
            project_name=project,
            site_name=site,
            trade=trade or "General",
            address=None,
        )

        outcome, start = ingestion_service.start_ingest(upload_ctx)
        if outcome:
            payload = {"item": outcome.item, "isDuplicate": outcome.is_duplicate}
            return JSONResponse(payload, status_code=200)

        assert start is not None
        if settings.process_inline:
            report = ingestion_service.finish_ingest(start, upload_ctx)
            return JSONResponse({"item": report, "isDuplicate": False}, status_code=201)

        # Async mode: return immediately and run extraction after the response.
        background_tasks.add_task(ingestion_service.finish_ingest, start, upload_ctx)
        placeholder = repository.get_report(tenant_id, start.document_id) or {"id": start.document_id}
        return JSONResponse(
            {"item": placeholder, "isDuplicate": False, "isProcessing": True},
            status_code=202,
        )

    @app.get("/issues")
    def list_issues(tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_issues_list(repository.load_snapshot(tenant_id))

    @app.get("/issues/{issue_id}")
    def get_issue(issue_id: str, tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        payload = build_issue_detail(repository.load_snapshot(tenant_id), issue_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Issue not found")
        return payload

    @app.patch("/issues/{issue_id}")
    def patch_issue(
        issue_id: str,
        payload: IssueUpdateRequest,
        tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id"),
    ) -> dict:
        updated = repository.update_issue(
            tenant_id,
            issue_id,
            status=payload.status,
            reinspections=payload.reinspections,
            last_sent_to=payload.lastSentTo,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Issue not found")
        return build_issue_detail(repository.load_snapshot(tenant_id), issue_id) or {"item": updated}

    @app.get("/dashboard")
    def dashboard(tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_dashboard_overview(repository.load_snapshot(tenant_id))

    @app.get("/dashboard/company")
    def dashboard_company(tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_company_page(repository.load_snapshot(tenant_id))

    @app.get("/dashboard/performance")
    def dashboard_performance(inspectionType: str = "All types", tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_performance_page(repository.load_snapshot(tenant_id), inspectionType)

    @app.get("/dashboard/insights")
    def dashboard_insights(inspectionType: str = "All inspection types", tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_insights_page(repository.load_snapshot(tenant_id), inspectionType)

    @app.get("/insights")
    def insights_summary(tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_legacy_insights_summary(repository.load_snapshot(tenant_id))

    @app.get("/dashboard/project/{slug}")
    def dashboard_project(slug: str, tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        payload = build_project_page(repository.load_snapshot(tenant_id), slug)
        if not payload:
            raise HTTPException(status_code=404, detail="Project not found")
        return payload

    @app.get("/dashboard/risk")
    def dashboard_risk(site: str = "All sites", window: str = "30d", inspectionId: str | None = None, tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_dashboard_risk(repository.load_snapshot(tenant_id), site, window, inspectionId)

    @app.get("/dashboard/live-tracker")
    def dashboard_live_tracker(tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_dashboard_live_tracker(repository.load_snapshot(tenant_id))

    @app.get("/dashboard/top-failures")
    def dashboard_top_failures(inspectionType: str | None = None, tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_dashboard_top_failures(repository.load_snapshot(tenant_id), inspectionType)

    @app.get("/dashboard/upcoming-risk")
    def dashboard_upcoming_risk(tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_dashboard_upcoming_risk(repository.load_snapshot(tenant_id))

    @app.get("/dashboard/insights-preview")
    def dashboard_insights_preview(tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_dashboard_insights_preview(repository.load_snapshot(tenant_id))

    @app.get("/inspection-risk")
    def inspection_risk(site: str | None = None, dateRange: str | None = None, inspectionType: str | None = None, tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        return build_inspection_risk_page(repository.load_snapshot(tenant_id), site, dateRange, inspectionType)

    @app.get("/tracker")
    def tracker(
        site: str | None = None,
        search: str | None = None,
        status: str | None = None,
        type: str | None = None,
        dateRange: str | None = None,
        issueId: str | None = None,
        tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id"),
    ) -> dict:
        return build_tracker_page(
            repository.load_snapshot(tenant_id),
            {
                "site": site,
                "search": search,
                "status": status,
                "type": type,
                "dateRange": dateRange,
                "issueId": issueId,
            },
        )

    @app.get("/tracker/{issue_id}")
    def tracker_issue(issue_id: str, tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id")) -> dict:
        payload = build_issue_detail(repository.load_snapshot(tenant_id), issue_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Issue not found")
        item = payload["item"]
        return {
            "item": {
                "id": item["id"],
                "issue": item["description"],
                "site": item["site"],
                "dateIdentified": item["dateIdentified"],
                "status": item["status"],
                "reinspections": item["reinspections"],
            }
        }

    @app.patch("/tracker/{issue_id}")
    def patch_tracker_issue(
        issue_id: str,
        payload: IssueUpdateRequest,
        tenant_id: str = Header(default=DEFAULT_TENANT_ID, alias="X-Soterra-Tenant-Id"),
    ) -> dict:
        updated = repository.update_issue(
            tenant_id,
            issue_id,
            status=payload.status,
            reinspections=payload.reinspections,
            last_sent_to=payload.lastSentTo,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Issue not found")
        return {"item": updated}

    return app


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _set_auth_headers(request: Request, *, tenant_id: str, user_id: str) -> None:
    next_headers = [
        item
        for item in request.scope.get("headers", [])
        if item[0].lower() not in {b"x-soterra-tenant-id", b"x-soterra-user-id"}
    ]
    next_headers.extend(
        [
            (b"x-soterra-tenant-id", tenant_id.encode("utf-8")),
            (b"x-soterra-user-id", user_id.encode("utf-8")),
        ]
    )
    request.scope["headers"] = next_headers


def _check_rate_limit(request: Request, action: str, subject: str, *, limit: int, window_seconds: int) -> None:
    ip_address = request.client.host if request.client else "unknown"
    key = f"{action}:{ip_address}:{subject}"
    if not rate_limiter.check(key, limit=limit, window_seconds=window_seconds):
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")


async def _read_limited_upload(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Uploaded file is too large.")
        chunks.append(chunk)
    return b"".join(chunks)


@lru_cache(maxsize=1)
def _cached_app() -> FastAPI:
    return create_app()


app = _cached_app()
