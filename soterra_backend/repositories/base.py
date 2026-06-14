from __future__ import annotations

from typing import Protocol

from ..models import AgentChatMessage, AgentChatSession, AgentMemoryEntry, AuthSession, ExtractionResult, RepositorySnapshot, StoredFile


class RepositoryBackend(Protocol):
    def initialize(self) -> None:
        ...

    def register_tenant(self, *, tenant_name: str, name: str, email: str, password: str) -> AuthSession:
        ...

    def authenticate_user(self, *, email: str, password: str) -> AuthSession | None:
        ...

    def create_password_reset_token(self, *, email: str) -> dict | None:
        ...

    def reset_password(self, *, token: str, password: str) -> AuthSession | None:
        ...

    def list_members(self, *, tenant_id: str) -> list[dict]:
        ...

    def invite_member(self, *, tenant_id: str, actor_user_id: str, name: str, email: str, password: str) -> dict:
        ...

    def remove_member(self, *, tenant_id: str, actor_user_id: str, user_id: str) -> bool:
        ...

    def get_user_session(self, *, user_id: str, tenant_id: str) -> AuthSession | None:
        ...

    def create_auth_session(self, *, user_id: str, tenant_id: str) -> AuthSession:
        ...

    def get_auth_session(self, *, access_token: str) -> AuthSession | None:
        ...

    def consume_upload_rate_limit(self, *, tenant_id: str, limit: int, window_seconds: int) -> bool:
        ...

    def get_report_by_file_hash(self, tenant_id: str, file_hash: str) -> dict | None:
        ...

    def source_filename_exists(self, *, tenant_id: str, project_name: str, filename: str) -> bool:
        ...

    def create_placeholder_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        job_id: str,
        file_hash: str,
        file_tag: str,
        project_name: str,
        site_name: str,
        address: str | None,
        source_filename: str,
        stored_filename: str,
        stored_file: StoredFile,
        trade: str,
        malware_scan_status: str = "clean",
    ) -> None:
        ...

    def complete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        job_id: str,
        source_filename: str,
        stored_file: StoredFile,
        extraction: ExtractionResult,
        extractor_name: str,
        raw_text: str,
        raw_payload: dict,
    ) -> None:
        ...

    def fail_job(
        self,
        *,
        tenant_id: str,
        document_id: str,
        job_id: str,
        extractor_name: str,
        error_message: str,
        raw_text: str,
    ) -> None:
        ...

    def load_snapshot(self, tenant_id: str) -> RepositorySnapshot:
        ...

    def get_report(self, tenant_id: str, report_id: str) -> dict | None:
        ...

    def delete_report(self, tenant_id: str, report_id: str) -> dict | None:
        ...

    def get_issue(self, tenant_id: str, issue_id: str) -> dict | None:
        ...

    def update_issue(
        self,
        tenant_id: str,
        issue_id: str,
        *,
        status: str | None = None,
        reinspections: int | None = None,
        last_sent_to: str | None = None,
    ) -> dict | None:
        ...

    def create_agent_chat_session(self, *, tenant_id: str, user_id: str, title: str | None = None) -> AgentChatSession:
        ...

    def list_agent_chat_sessions(self, *, tenant_id: str, user_id: str, limit: int = 50) -> list[AgentChatSession]:
        ...

    def get_agent_chat_session(self, *, tenant_id: str, user_id: str, session_id: str) -> AgentChatSession | None:
        ...

    def soft_delete_agent_chat_session(self, *, tenant_id: str, user_id: str, session_id: str) -> bool:
        ...

    def list_agent_chat_messages(self, *, tenant_id: str, user_id: str, session_id: str, limit: int = 40) -> list[AgentChatMessage]:
        ...

    def add_agent_chat_message(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        tool_payload_json: str | None = None,
    ) -> AgentChatMessage:
        ...

    def add_agent_memory_entry(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str | None,
        memory_type: str,
        content: str,
        payload_json: str | None = None,
    ) -> AgentMemoryEntry:
        ...

    def list_agent_memory_entries(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[AgentMemoryEntry]:
        ...
