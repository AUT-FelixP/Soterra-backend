from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...email_service import EmailService
from ...repositories.base import RepositoryBackend
from ...schemas.auth import (
    ForgotPasswordRequest,
    InviteMemberRequest,
    LoginRequest,
    RegisterAccountRequest,
    ResetPasswordRequest,
)
from ..dependencies import AuthContext, get_auth_context, get_email_service, get_repository
from ..security import check_rate_limit

router = APIRouter()


@router.post("/auth/register", status_code=201)
def register_account(
    request: Request,
    payload: RegisterAccountRequest,
    repository: RepositoryBackend = Depends(get_repository),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    check_rate_limit(request, "register", payload.email.lower(), limit=10, window_seconds=15 * 60)
    tenant_name = str(payload.tenantName or payload.company or "").strip()
    if not tenant_name:
        raise HTTPException(status_code=400, detail="Tenant name, name, email, and password are required.")
    try:
        session = repository.register_tenant(
            tenant_name=tenant_name,
            name=payload.name.strip(),
            email=payload.email.strip(),
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    email_sent = email_service.send_registration_email(
        to_email=session.user.email,
        name=session.user.name,
        tenant_name=session.user.tenant_name,
    )
    response = session.model_dump()
    response["emailSent"] = email_sent
    return response


@router.post("/auth/login")
def login(
    request: Request,
    payload: LoginRequest,
    repository: RepositoryBackend = Depends(get_repository),
) -> dict:
    email = str(payload.email or payload.username or "").strip()
    password = payload.password
    if not email or not password:
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    check_rate_limit(request, "login", email.lower(), limit=8, window_seconds=15 * 60)
    session = repository.authenticate_user(email=email, password=password)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    return session.model_dump()


@router.post("/auth/forgot-password")
def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    repository: RepositoryBackend = Depends(get_repository),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    settings = request.app.state.settings
    email = payload.email.strip()
    check_rate_limit(request, "forgot-password", email.lower(), limit=5, window_seconds=15 * 60)
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


@router.post("/auth/reset-password")
def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    repository: RepositoryBackend = Depends(get_repository),
) -> dict:
    check_rate_limit(request, "reset-password", payload.token[:16], limit=8, window_seconds=15 * 60)
    session = repository.reset_password(token=payload.token.strip(), password=payload.password)
    if not session:
        raise HTTPException(status_code=400, detail="Invalid or expired password reset token.")
    return session.model_dump()


@router.get("/auth/session")
def auth_session(
    context: AuthContext = Depends(get_auth_context),
    repository: RepositoryBackend = Depends(get_repository),
) -> dict:
    session = repository.get_user_session(user_id=context.user_id, tenant_id=context.tenant_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session.")
    return session.model_dump()


@router.get("/tenants/members")
def list_members(
    context: AuthContext = Depends(get_auth_context),
    repository: RepositoryBackend = Depends(get_repository),
) -> dict:
    return {"items": repository.list_members(tenant_id=context.tenant_id)}


@router.post("/tenants/members", status_code=201)
def invite_member(
    request: Request,
    payload: InviteMemberRequest,
    context: AuthContext = Depends(get_auth_context),
    repository: RepositoryBackend = Depends(get_repository),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    check_rate_limit(request, "invite", context.tenant_id, limit=30, window_seconds=60 * 60)
    try:
        member = repository.invite_member(
            tenant_id=context.tenant_id,
            actor_user_id=context.user_id,
            name=payload.name.strip(),
            email=payload.email.strip(),
            password=payload.password,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    actor_session = repository.get_user_session(user_id=context.user_id, tenant_id=context.tenant_id)
    email_sent = email_service.send_invitation_email(
        to_email=member["email"],
        name=member["name"],
        tenant_name=actor_session.user.tenant_name if actor_session else "your tenant",
    )
    return {"item": member, "emailSent": email_sent}


@router.delete("/tenants/members/{user_id}")
def remove_member(
    user_id: str,
    context: AuthContext = Depends(get_auth_context),
    repository: RepositoryBackend = Depends(get_repository),
) -> dict:
    try:
        removed = repository.remove_member(
            tenant_id=context.tenant_id,
            actor_user_id=context.user_id,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found.")
    return {"removed": True}
