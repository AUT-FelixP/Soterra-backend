from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...agent import AgentChatRequest, AgentConfigurationError, AgentDisabledError, SoterraAgentService
from ..dependencies import AuthContext, get_agent_service, require_auth_context, require_tenant_data_access

router = APIRouter(prefix="/agent/chat", dependencies=[Depends(require_tenant_data_access)])


@router.get("/status")
def agent_chat_status(
    context: AuthContext = Depends(require_auth_context),
    agent_service: SoterraAgentService = Depends(get_agent_service),
) -> dict:
    _ = context
    return agent_service.status()


@router.post("")
def agent_chat(
    payload: AgentChatRequest,
    context: AuthContext = Depends(require_auth_context),
    agent_service: SoterraAgentService = Depends(get_agent_service),
) -> dict:
    try:
        response = agent_service.chat(
            message=payload.message,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            role=context.role or "member",
            session_id=payload.session_id,
            report_id=payload.report_id,
            issue_id=payload.issue_id,
            project_slug=payload.project_slug,
            page_context=payload.page_context,
        )
    except AgentDisabledError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AgentConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return response.model_dump()


@router.get("/sessions")
def agent_chat_sessions(
    context: AuthContext = Depends(require_auth_context),
    agent_service: SoterraAgentService = Depends(get_agent_service),
) -> dict:
    return {"items": agent_service.list_sessions(tenant_id=context.tenant_id, user_id=context.user_id)}


@router.get("/sessions/{session_id}")
def agent_chat_session(
    session_id: str,
    context: AuthContext = Depends(require_auth_context),
    agent_service: SoterraAgentService = Depends(get_agent_service),
) -> dict:
    payload = agent_service.get_session(tenant_id=context.tenant_id, user_id=context.user_id, session_id=session_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return payload


@router.delete("/sessions/{session_id}")
def delete_agent_chat_session(
    session_id: str,
    context: AuthContext = Depends(require_auth_context),
    agent_service: SoterraAgentService = Depends(get_agent_service),
) -> dict:
    deleted = agent_service.delete_session(tenant_id=context.tenant_id, user_id=context.user_id, session_id=session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"deleted": True, "id": session_id}
