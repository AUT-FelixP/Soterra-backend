from __future__ import annotations

import json
import sqlite3
import hashlib
import hmac
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

from .config import Settings
from .models import AuthSession, ExtractionResult, RepositorySnapshot, StoredFile, TenantUser
from .utils import create_id, utc_now_iso

_DEFAULT_TENANT_ID = "ten-default"
_DEFAULT_ADMIN_ID = "usr-default-admin"
_PASSWORD_ITERATIONS = 120_000


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

    def get_report_by_file_hash(self, tenant_id: str, file_hash: str) -> dict | None:
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
        stored_file: StoredFile,
        trade: str,
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


class SqliteRepository:
    def __init__(
        self,
        db_path: Path,
        schema_path: Path,
        *,
        session_ttl_hours: int,
        bootstrap_demo_account: bool,
        demo_admin_password: str | None,
    ) -> None:
        self.db_path = db_path
        self.schema_path = schema_path
        self.session_ttl_hours = session_ttl_hours
        self.bootstrap_demo_account = bootstrap_demo_account
        self.demo_admin_password = demo_admin_password
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as connection:
            self._bootstrap_schema(connection)

    def _bootstrap_schema(self, connection: sqlite3.Connection) -> None:
        schema_sql = self.schema_path.read_text(encoding="utf-8")

        # When a local demo DB already exists, CREATE TABLE IF NOT EXISTS will not add new columns.
        # Older DBs (pre file_hash/file_tag) would fail when the schema tries to create indexes
        # referencing missing columns. Create tables first, then apply column backfills, then
        # create indexes, and finally refresh views.
        tables_sql = schema_sql
        indexes_sql = ""

        index_marker = "CREATE INDEX IF NOT EXISTS"
        if index_marker in schema_sql:
            tables_sql, _, tail = schema_sql.partition(index_marker)
            indexes_sql = f"{index_marker}{tail}"

        view_marker = "CREATE VIEW IF NOT EXISTS analytics_report_summary_v AS"
        if view_marker in indexes_sql:
            indexes_sql, _, _ = indexes_sql.partition(view_marker)

        connection.executescript(tables_sql)
        self._drop_analytics_views(connection)
        self._ensure_auth_tables(connection)
        self._ensure_tenant_columns(connection)
        self._ensure_document_columns(connection)
        if indexes_sql.strip():
            connection.executescript(indexes_sql)
        self._refresh_views(connection)

    def register_tenant(self, *, tenant_name: str, name: str, email: str, password: str) -> AuthSession:
        timestamp = utc_now_iso()
        normalized_email = _normalize_email(email)
        email_domain = _email_domain(normalized_email)
        tenant_id = create_id("ten")
        tenant_slug = _slug(tenant_name)
        user_id = create_id("usr")
        with self._connect() as connection:
            if connection.execute("SELECT id FROM users WHERE email = ?", (normalized_email,)).fetchone():
                raise ValueError("An account with this email already exists.")
            existing_tenant = None
            if email_domain and not _is_public_email_domain(email_domain):
                existing_tenant = connection.execute(
                    "SELECT id, name FROM tenants WHERE email_domain = ? LIMIT 1",
                    (email_domain,),
                ).fetchone()
            if existing_tenant:
                tenant_id = existing_tenant["id"]
                role = "member"
            else:
                role = "admin"
                if connection.execute("SELECT id FROM tenants WHERE slug = ?", (tenant_slug,)).fetchone():
                    tenant_slug = f"{tenant_slug}-{tenant_id[-6:]}"
                connection.execute(
                    "INSERT INTO tenants (id, name, slug, email_domain, created_at) VALUES (?, ?, ?, ?, ?)",
                    (tenant_id, tenant_name, tenant_slug, email_domain, timestamp),
                )
            connection.execute(
                """
                INSERT INTO users (id, tenant_id, name, email, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, tenant_id, name, normalized_email, _hash_password(password), role, timestamp),
            )
        return self.create_auth_session(user_id=user_id, tenant_id=tenant_id)

    def authenticate_user(self, *, email: str, password: str) -> AuthSession | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, tenant_id, password_hash FROM users WHERE email = ?",
                (_normalize_email(email),),
            ).fetchone()
        if not row or not _verify_password(password, row["password_hash"]):
            return None
        return self.create_auth_session(user_id=row["id"], tenant_id=row["tenant_id"])

    def create_password_reset_token(self, *, email: str) -> dict | None:
        normalized_email = _normalize_email(email)
        token = secrets.token_urlsafe(32)
        timestamp = utc_now_iso()
        expires_at = (datetime.now(tz=UTC).replace(microsecond=0) + timedelta(hours=1)).isoformat()
        with self._connect() as connection:
            user = connection.execute(
                """
                SELECT u.id, u.name, u.email, u.tenant_id, t.name AS tenant_name
                FROM users u
                JOIN tenants t ON t.id = u.tenant_id
                WHERE u.email = ?
                """,
                (normalized_email,),
            ).fetchone()
            if not user:
                return None
            connection.execute(
                """
                INSERT INTO password_reset_tokens (id, user_id, tenant_id, token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (create_id("prt"), user["id"], user["tenant_id"], _token_hash(token), timestamp, expires_at),
            )
        return {
            "token": token,
            "expires_at": expires_at,
            "user_id": user["id"],
            "tenant_id": user["tenant_id"],
            "name": user["name"],
            "email": user["email"],
            "tenant_name": user["tenant_name"],
        }

    def reset_password(self, *, token: str, password: str) -> AuthSession | None:
        now = utc_now_iso()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, user_id, tenant_id
                FROM password_reset_tokens
                WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
                """,
                (_token_hash(token), now),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ? AND tenant_id = ?",
                (_hash_password(password), row["user_id"], row["tenant_id"]),
            )
            connection.execute(
                "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND tenant_id = ? AND revoked_at IS NULL",
                (now, row["user_id"], row["tenant_id"]),
            )
        return self.create_auth_session(user_id=row["user_id"], tenant_id=row["tenant_id"])

    def list_members(self, *, tenant_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, tenant_id, name, email, role, created_at FROM users WHERE tenant_id = ? ORDER BY role, name",
                (tenant_id,),
            ).fetchall()
        return [_dict(row) for row in rows]

    def invite_member(self, *, tenant_id: str, actor_user_id: str, name: str, email: str, password: str) -> dict:
        if not self._is_admin(tenant_id, actor_user_id):
            raise PermissionError("Only tenant admins can invite members.")
        timestamp = utc_now_iso()
        user_id = create_id("usr")
        normalized_email = _normalize_email(email)
        with self._connect() as connection:
            if connection.execute("SELECT id FROM users WHERE email = ?", (normalized_email,)).fetchone():
                raise ValueError("An account with this email already exists.")
            connection.execute(
                """
                INSERT INTO users (id, tenant_id, name, email, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, tenant_id, name, normalized_email, _hash_password(password), "member", timestamp),
            )
        return next(member for member in self.list_members(tenant_id=tenant_id) if member["id"] == user_id)

    def remove_member(self, *, tenant_id: str, actor_user_id: str, user_id: str) -> bool:
        if not self._is_admin(tenant_id, actor_user_id):
            raise PermissionError("Only tenant admins can remove members.")
        if actor_user_id == user_id:
            raise ValueError("Admins cannot remove their own account.")
        with self._connect() as connection:
            current = connection.execute(
                "SELECT role FROM users WHERE tenant_id = ? AND id = ?",
                (tenant_id, user_id),
            ).fetchone()
            if not current:
                return False
            if current["role"] == "admin":
                raise ValueError("Admin users cannot be removed from this in-app member flow.")
            connection.execute("DELETE FROM users WHERE tenant_id = ? AND id = ?", (tenant_id, user_id))
        return True

    def get_user_session(self, *, user_id: str, tenant_id: str) -> AuthSession | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT u.id, u.tenant_id, t.name AS tenant_name, u.name, u.email, u.role
                FROM users u
                JOIN tenants t ON t.id = u.tenant_id
                WHERE u.id = ? AND u.tenant_id = ?
                """,
                (user_id, tenant_id),
            ).fetchone()
        if not row:
            return None
        return AuthSession(user=TenantUser(**_dict(row)))

    def create_auth_session(self, *, user_id: str, tenant_id: str) -> AuthSession:
        access_token = secrets.token_urlsafe(32)
        timestamp = utc_now_iso()
        expires_at = _session_expires_at(self.session_ttl_hours)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions (id, user_id, tenant_id, token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (create_id("ses"), user_id, tenant_id, _token_hash(access_token), timestamp, expires_at),
            )
        session = self.get_user_session(user_id=user_id, tenant_id=tenant_id)
        if not session:
            raise RuntimeError("Authenticated user could not be loaded.")
        return session.model_copy(update={"access_token": access_token, "expires_at": expires_at})

    def get_auth_session(self, *, access_token: str) -> AuthSession | None:
        now = utc_now_iso()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id, tenant_id, expires_at
                FROM auth_sessions
                WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?
                """,
                (_token_hash(access_token), now),
            ).fetchone()
        if not row:
            return None
        session = self.get_user_session(user_id=row["user_id"], tenant_id=row["tenant_id"])
        if not session:
            return None
        return session.model_copy(update={"access_token": access_token, "expires_at": row["expires_at"]})

    def get_report_by_file_hash(self, tenant_id: str, file_hash: str) -> dict | None:
        row = None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM documents WHERE tenant_id = ? AND file_hash = ? LIMIT 1",
                (tenant_id, file_hash),
            ).fetchone()
        if not row:
            return None
        return self.get_report(tenant_id, row["id"])

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
        stored_file: StoredFile,
        trade: str,
    ) -> None:
        project_id = create_id("prj")
        project_slug = _slug(project_name)
        timestamp = utc_now_iso()

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM projects WHERE tenant_id = ? AND slug = ?",
                (tenant_id, project_slug),
            ).fetchone()
            if existing:
                project_id = existing["id"]
            else:
                # If the user typed a close-but-not-identical project name (typo, spacing, punctuation),
                # map it to the nearest existing slug to avoid creating duplicate projects locally.
                candidate = _closest_project_slug(connection, tenant_id, project_slug)
                if candidate:
                    project_id = candidate["id"]
                    project_slug = candidate["slug"]
                    project_name = candidate["name"]
                else:
                    connection.execute(
                        """
                        INSERT INTO projects (id, tenant_id, slug, name, site_name, address, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (project_id, tenant_id, project_slug, project_name, site_name, address, timestamp),
                    )

            connection.execute(
                """
                INSERT INTO documents (
                  id, tenant_id, project_id, file_hash, file_tag, site_name, address, source_filename, storage_path, download_url, inspection_type,
                  trade, inspector, report_date, status, summary, units_json, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    tenant_id,
                    project_id,
                    file_hash,
                    file_tag,
                    site_name,
                    address,
                    source_filename,
                    stored_file.storage_path,
                    stored_file.download_url,
                    "Pending extraction",
                    trade or "General",
                    "Pending extractor",
                    timestamp[:10],
                    "In progress",
                    "Extraction pending.",
                    "[]",
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO jobs (id, document_id, status, extractor, started_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, document_id, "running", "pending", timestamp),
            )

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
        timestamp = utc_now_iso()

        with self._connect() as connection:
            # Preserve the existing project_id chosen at upload time so we don't create duplicate
            # projects when the extractor returns a slightly different name.
            document_row = connection.execute(
                "SELECT project_id FROM documents WHERE tenant_id = ? AND id = ?",
                (tenant_id, document_id),
            ).fetchone()
            project_id = document_row["project_id"] if document_row else None
            project_row = None
            if project_id:
                project_row = connection.execute(
                    "SELECT id, slug FROM projects WHERE tenant_id = ? AND id = ?",
                    (tenant_id, project_id),
                ).fetchone()
            if not project_row:
                # Fallback for any legacy rows that might not have a valid project reference.
                project_slug = _slug(extraction.project_name)
                project_row = connection.execute(
                    "SELECT id, slug FROM projects WHERE tenant_id = ? AND slug = ?",
                    (tenant_id, project_slug),
                ).fetchone()
                project_id = project_row["id"] if project_row else create_id("prj")
            else:
                project_slug = project_row["slug"]

            if project_row:
                connection.execute(
                    """
                    UPDATE projects
                    SET name = ?, site_name = ?, address = ?
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (
                        extraction.project_name,
                        extraction.site_name,
                        extraction.address,
                        tenant_id,
                        project_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO projects (id, tenant_id, slug, name, site_name, address, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        tenant_id,
                        _slug(extraction.project_name),
                        extraction.project_name,
                        extraction.site_name,
                        extraction.address,
                        timestamp,
                    ),
                )

            connection.execute(
                """
                UPDATE documents
                SET project_id = ?, source_filename = ?, storage_path = ?, download_url = ?,
                    site_name = ?, address = ?, inspection_type = ?, trade = ?, inspector = ?, report_date = ?, status = ?,
                    summary = ?, units_json = ?, uploaded_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    project_id,
                    source_filename,
                    stored_file.storage_path,
                    stored_file.download_url,
                    extraction.site_name,
                    extraction.address,
                    extraction.inspection_type,
                    extraction.trade,
                    extraction.inspector,
                    extraction.report_date,
                    extraction.overall_outcome,
                    extraction.summary,
                    json.dumps(extraction.units),
                    timestamp,
                    tenant_id,
                    document_id,
                ),
            )

            connection.execute("DELETE FROM findings WHERE tenant_id = ? AND document_id = ?", (tenant_id, document_id))
            connection.execute(
                "DELETE FROM predicted_inspections WHERE tenant_id = ? AND project_id = ? AND source LIKE ?",
                (tenant_id, project_id, f"{extraction.inspection_type}%"),
            )

            for finding in extraction.findings:
                connection.execute(
                    """
                    INSERT INTO findings (
                      id, tenant_id, document_id, project_id, title, description, category, trade, severity,
                      status, location, unit_label, recurrence_risk, reinspections, last_sent_to,
                      created_at, closed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        create_id("issue"),
                        tenant_id,
                        document_id,
                        project_id,
                        finding.title,
                        finding.description,
                        finding.category,
                        finding.trade,
                        finding.severity,
                        finding.status,
                        finding.location,
                        finding.unit_label,
                        finding.recurrence_risk,
                        0,
                        None,
                        timestamp,
                        timestamp if finding.status == "Closed" else None,
                    ),
                )

            for prediction in extraction.predicted_inspections:
                connection.execute(
                    """
                    INSERT INTO predicted_inspections (
                      id, tenant_id, project_id, inspection_type, site_name, expected_date, risk_level, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        create_id("pred"),
                        tenant_id,
                        project_id,
                        prediction.inspection_type,
                        prediction.site_name,
                        prediction.expected_date,
                        prediction.risk_level,
                        prediction.source,
                        timestamp,
                    ),
                )

            connection.execute(
                """
                UPDATE jobs
                SET status = ?, extractor = ?, raw_text_excerpt = ?, raw_payload_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    "completed",
                    extractor_name,
                    raw_text[:4000],
                    json.dumps(raw_payload),
                    timestamp,
                    job_id,
                ),
            )

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
        timestamp = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE documents SET status = ?, summary = ? WHERE tenant_id = ? AND id = ?",
                (
                    "Reviewing",
                    "Extraction failed. Manual review is required before trusting this report.",
                    tenant_id,
                    document_id,
                ),
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, extractor = ?, error_message = ?, raw_text_excerpt = ?, completed_at = ?
                WHERE id = ?
                """,
                ("failed", extractor_name, error_message, raw_text[:4000], timestamp, job_id),
            )

    def load_snapshot(self, tenant_id: str) -> RepositorySnapshot:
        with self._connect() as connection:
            projects = [_dict(row) for row in connection.execute("SELECT * FROM projects WHERE tenant_id = ? ORDER BY name", (tenant_id,))]
            documents = [_normalize_document(row) for row in connection.execute(_DOCUMENT_QUERY, (tenant_id,))]
            jobs = [_dict(row) for row in connection.execute(_JOBS_QUERY, (tenant_id,))]
            findings = [_normalize_finding(row) for row in connection.execute(_FINDING_QUERY, (tenant_id,))]
            predictions = [_dict(row) for row in connection.execute(_PREDICTION_QUERY, (tenant_id,))]

        return RepositorySnapshot(
            projects=projects,
            documents=documents,
            jobs=jobs,
            findings=findings,
            predicted_inspections=predictions,
        )

    def get_report(self, tenant_id: str, report_id: str) -> dict | None:
        snapshot = self.load_snapshot(tenant_id)
        for document in snapshot.documents:
            if document["id"] == report_id:
                document["issues"] = [
                    {
                        "id": finding["id"],
                        "title": finding["title"],
                        "severity": finding["severity"],
                    }
                    for finding in snapshot.findings
                    if finding["document_id"] == report_id
                ]
                return document
        return None

    def delete_report(self, tenant_id: str, report_id: str) -> dict | None:
        with self._connect() as connection:
            document = connection.execute(
                "SELECT id, project_id, storage_path FROM documents WHERE tenant_id = ? AND id = ?",
                (tenant_id, report_id),
            ).fetchone()
            if not document:
                return None
            project_id = document["project_id"]
            storage_path = document["storage_path"]
            connection.execute("DELETE FROM jobs WHERE document_id = ?", (report_id,))
            connection.execute("DELETE FROM findings WHERE tenant_id = ? AND document_id = ?", (tenant_id, report_id))
            connection.execute("DELETE FROM documents WHERE tenant_id = ? AND id = ?", (tenant_id, report_id))
            remaining = connection.execute(
                "SELECT COUNT(*) AS count FROM documents WHERE tenant_id = ? AND project_id = ?",
                (tenant_id, project_id),
            ).fetchone()["count"]
            if remaining == 0:
                connection.execute("DELETE FROM predicted_inspections WHERE tenant_id = ? AND project_id = ?", (tenant_id, project_id))
                connection.execute("DELETE FROM projects WHERE tenant_id = ? AND id = ?", (tenant_id, project_id))
        return {"id": report_id, "storage_path": storage_path}

    def get_issue(self, tenant_id: str, issue_id: str) -> dict | None:
        snapshot = self.load_snapshot(tenant_id)
        return next((item for item in snapshot.findings if item["id"] == issue_id), None)

    def update_issue(
        self,
        tenant_id: str,
        issue_id: str,
        *,
        status: str | None = None,
        reinspections: int | None = None,
        last_sent_to: str | None = None,
    ) -> dict | None:
        timestamp = utc_now_iso()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM findings WHERE tenant_id = ? AND id = ?",
                (tenant_id, issue_id),
            ).fetchone()
            if not current:
                return None

            next_status = status or current["status"]
            next_reinspections = current["reinspections"] if reinspections is None else reinspections
            next_last_sent_to = last_sent_to or current["last_sent_to"]
            next_closed_at = timestamp if next_status == "Closed" else None

            connection.execute(
                """
                UPDATE findings
                SET status = ?, reinspections = ?, last_sent_to = ?, closed_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (next_status, next_reinspections, next_last_sent_to, next_closed_at, tenant_id, issue_id),
            )

        return self.get_issue(tenant_id, issue_id)

    @contextmanager
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            # If the DB file was deleted/reset while the server is running, reopen will create a new
            # empty SQLite file. Self-heal by applying the schema on first use.
            has_projects = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects' LIMIT 1"
            ).fetchone()
            if not has_projects:
                self._bootstrap_schema(connection)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_document_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)")}
        if "file_hash" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN file_hash TEXT")
            connection.execute("UPDATE documents SET file_hash = id WHERE file_hash IS NULL")
        if "file_tag" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN file_tag TEXT")
            connection.execute("UPDATE documents SET file_tag = id WHERE file_tag IS NULL")
        if "site_name" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN site_name TEXT")
            connection.execute(
                """
                UPDATE documents
                SET site_name = (
                  SELECT p.site_name FROM projects p WHERE p.id = documents.project_id
                )
                WHERE site_name IS NULL
                """
            )
        if "address" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN address TEXT")
            connection.execute(
                """
                UPDATE documents
                SET address = (
                  SELECT p.address FROM projects p WHERE p.id = documents.project_id
                )
                WHERE address IS NULL
                """
            )
        if self._documents_have_global_file_uniques(connection):
            self._rebuild_documents_without_global_uniques(connection)
        connection.execute("DROP INDEX IF EXISTS idx_documents_file_hash_unique")
        connection.execute("DROP INDEX IF EXISTS idx_documents_file_tag_unique")
        connection.execute("DROP INDEX IF EXISTS idx_documents_file_hash")
        connection.execute("DROP INDEX IF EXISTS idx_documents_file_tag")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(tenant_id, file_hash)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_tenant_file_hash_unique ON documents(tenant_id, file_hash)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_tenant_file_tag_unique ON documents(tenant_id, file_tag)")

    def _documents_have_global_file_uniques(self, connection: sqlite3.Connection) -> bool:
        for index in connection.execute("PRAGMA index_list(documents)").fetchall():
            if not index["unique"]:
                continue
            columns = [row["name"] for row in connection.execute(f"PRAGMA index_info({index['name']})").fetchall()]
            if columns in (["file_hash"], ["file_tag"]):
                return True
        return False

    def _rebuild_documents_without_global_uniques(self, connection: sqlite3.Connection) -> None:
        timestamp = utc_now_iso()
        legacy_table = f"documents_legacy_{int(datetime.now(tz=UTC).timestamp())}"
        connection.execute("DROP INDEX IF EXISTS idx_documents_file_hash_unique")
        connection.execute("DROP INDEX IF EXISTS idx_documents_file_tag_unique")
        connection.execute("DROP INDEX IF EXISTS idx_documents_file_hash")
        connection.execute("DROP INDEX IF EXISTS idx_documents_file_tag")
        connection.execute("DROP INDEX IF EXISTS idx_documents_tenant_file_hash_unique")
        connection.execute("DROP INDEX IF EXISTS idx_documents_tenant_file_tag_unique")
        connection.execute("DROP INDEX IF EXISTS idx_documents_project_date")
        connection.execute(f"ALTER TABLE documents RENAME TO {legacy_table}")
        connection.execute(
            """
            CREATE TABLE documents (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              file_hash TEXT NOT NULL,
              file_tag TEXT NOT NULL,
              site_name TEXT,
              address TEXT,
              source_filename TEXT NOT NULL,
              storage_path TEXT NOT NULL,
              download_url TEXT,
              inspection_type TEXT NOT NULL,
              trade TEXT NOT NULL,
              inspector TEXT NOT NULL,
              report_date TEXT NOT NULL,
              status TEXT NOT NULL,
              summary TEXT NOT NULL,
              units_json TEXT NOT NULL,
              uploaded_at TEXT NOT NULL,
              FOREIGN KEY(project_id) REFERENCES projects(id)
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO documents (
              id, tenant_id, project_id, file_hash, file_tag, site_name, address, source_filename,
              storage_path, download_url, inspection_type, trade, inspector, report_date, status,
              summary, units_json, uploaded_at
            )
            SELECT
              id,
              COALESCE(tenant_id, ?),
              project_id,
              COALESCE(file_hash, id),
              CASE
                WHEN file_tag IS NULL OR file_tag = '' THEN COALESCE(tenant_id, ?) || '-file-' || substr(COALESCE(file_hash, id), 1, 12)
                WHEN instr(file_tag, '-file-') = 0 THEN COALESCE(tenant_id, ?) || '-' || file_tag
                ELSE file_tag
              END,
              site_name,
              address,
              source_filename,
              storage_path,
              download_url,
              inspection_type,
              trade,
              inspector,
              report_date,
              status,
              summary,
              COALESCE(units_json, '[]'),
              COALESCE(uploaded_at, ?)
            FROM {legacy_table}
            """
            ,
            (_DEFAULT_TENANT_ID, _DEFAULT_TENANT_ID, _DEFAULT_TENANT_ID, timestamp),
        )
        connection.execute(f"DROP TABLE {legacy_table}")

    def _ensure_auth_tables(self, connection: sqlite3.Connection) -> None:
        timestamp = utc_now_iso()
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              slug TEXT NOT NULL UNIQUE,
              email_domain TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        tenant_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tenants)")}
        if "email_domain" not in tenant_columns:
            connection.execute("ALTER TABLE tenants ADD COLUMN email_domain TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              name TEXT NOT NULL,
              email TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
              created_at TEXT NOT NULL,
              FOREIGN KEY(tenant_id) REFERENCES tenants(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              tenant_id TEXT NOT NULL,
              token_hash TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              revoked_at TEXT,
              FOREIGN KEY(user_id) REFERENCES users(id),
              FOREIGN KEY(tenant_id) REFERENCES tenants(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              tenant_id TEXT NOT NULL,
              token_hash TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              used_at TEXT,
              FOREIGN KEY(user_id) REFERENCES users(id),
              FOREIGN KEY(tenant_id) REFERENCES tenants(id)
            )
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, slug, email_domain, created_at) VALUES (?, ?, ?, ?, ?)",
            (_DEFAULT_TENANT_ID, "Default Tenant", "default-tenant", None, timestamp),
        )
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower_unique ON users(lower(email))")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tenants_email_domain ON tenants(email_domain)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_token_hash ON auth_sessions(token_hash)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_tenant ON auth_sessions(user_id, tenant_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_token_hash ON password_reset_tokens(token_hash)")
        if self.bootstrap_demo_account:
            if not self.demo_admin_password or len(self.demo_admin_password) < 12:
                raise RuntimeError("SOTERRA_DEMO_ADMIN_PASSWORD must be set to at least 12 characters when demo bootstrap is enabled.")
            connection.execute(
                """
                INSERT OR IGNORE INTO users (id, tenant_id, name, email, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (_DEFAULT_ADMIN_ID, _DEFAULT_TENANT_ID, "Demo Admin", "admin@soterra.local", _hash_password(self.demo_admin_password), "admin", timestamp),
            )

    def _ensure_tenant_columns(self, connection: sqlite3.Connection) -> None:
        for table in ("projects", "documents", "findings", "predicted_inspections"):
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "tenant_id" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT")
                connection.execute(f"UPDATE {table} SET tenant_id = ? WHERE tenant_id IS NULL", (_DEFAULT_TENANT_ID,))
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_tenant_slug_unique ON projects(tenant_id, slug)")

    def _is_admin(self, tenant_id: str, user_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT role FROM users WHERE tenant_id = ? AND id = ?",
                (tenant_id, user_id),
            ).fetchone()
        return bool(row and row["role"] == "admin")

    def _refresh_views(self, connection: sqlite3.Connection) -> None:
        # Recreate analytics views so local databases pick up view fixes without a manual reset.
        self._drop_analytics_views(connection)
        schema_sql = self.schema_path.read_text(encoding="utf-8")
        marker = "CREATE VIEW IF NOT EXISTS analytics_report_summary_v AS"
        _, _, view_sql = schema_sql.partition(marker)
        if not view_sql:
            raise RuntimeError("Could not locate analytics view definitions in local_schema.sql.")
        view_sql = f"{marker}{view_sql}"
        connection.executescript(view_sql)

    def _drop_analytics_views(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            DROP VIEW IF EXISTS analytics_report_summary_v;
            DROP VIEW IF EXISTS analytics_company_metrics_v;
            DROP VIEW IF EXISTS analytics_project_metrics_v;
            DROP VIEW IF EXISTS analytics_top_failure_drivers_v;
            DROP VIEW IF EXISTS analytics_upcoming_risk_v;
            """
        )


class SupabaseRepository:
    def __init__(self, *, url: str, service_role_key: str, session_ttl_hours: int) -> None:
        self.session_ttl_hours = session_ttl_hours
        self._memory_password_reset_tokens: dict[str, dict] = {}
        try:
            from supabase import create_client
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "The supabase package is not installed. Run the Python dependency install step first."
            ) from exc

        self.client = create_client(url, service_role_key)

    def initialize(self) -> None:
        return None

    def register_tenant(self, *, tenant_name: str, name: str, email: str, password: str) -> AuthSession:
        timestamp = utc_now_iso()
        tenant_id = create_id("ten")
        user_id = create_id("usr")
        normalized_email = _normalize_email(email)
        email_domain = _email_domain(normalized_email)
        tenant_slug = _slug(tenant_name)
        existing = self.client.table("users").select("id").eq("email", normalized_email).limit(1).execute().data
        if existing:
            raise ValueError("An account with this email already exists.")
        existing_tenant = []
        if email_domain and not _is_public_email_domain(email_domain):
            try:
                existing_tenant = self.client.table("tenants").select("id, name").eq("email_domain", email_domain).limit(1).execute().data
            except Exception as exc:
                if not _is_postgrest_missing_column(exc, "email_domain"):
                    raise
        if existing_tenant:
            tenant_id = existing_tenant[0]["id"]
            role = "member"
        else:
            role = "admin"
            if self.client.table("tenants").select("id").eq("slug", tenant_slug).limit(1).execute().data:
                tenant_slug = f"{tenant_slug}-{tenant_id[-6:]}"
            tenant_payload = {"id": tenant_id, "name": tenant_name, "slug": tenant_slug, "email_domain": email_domain, "created_at": timestamp}
            try:
                self.client.table("tenants").insert(tenant_payload).execute()
            except Exception as exc:
                if not _is_postgrest_missing_column(exc, "email_domain"):
                    raise
                tenant_payload.pop("email_domain", None)
                self.client.table("tenants").insert(tenant_payload).execute()
        self.client.table("users").insert(
            {
                "id": user_id,
                "tenant_id": tenant_id,
                "name": name,
                "email": normalized_email,
                "password_hash": _hash_password(password),
                "role": role,
                "created_at": timestamp,
            }
        ).execute()
        return self.create_auth_session(user_id=user_id, tenant_id=tenant_id)

    def authenticate_user(self, *, email: str, password: str) -> AuthSession | None:
        row = self.client.table("users").select("id, tenant_id, password_hash").eq("email", _normalize_email(email)).limit(1).execute().data
        if not row or not _verify_password(password, row[0]["password_hash"]):
            return None
        return self.create_auth_session(user_id=row[0]["id"], tenant_id=row[0]["tenant_id"])

    def create_password_reset_token(self, *, email: str) -> dict | None:
        normalized_email = _normalize_email(email)
        user_rows = (
            self.client.table("users")
            .select("id, tenant_id, name, email, tenants(name)")
            .eq("email", normalized_email)
            .limit(1)
            .execute()
            .data
        )
        if not user_rows:
            return None
        user = user_rows[0]
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(tz=UTC).replace(microsecond=0) + timedelta(hours=1)).isoformat()
        tenant = user.get("tenants") or {}
        payload = {
            "token": token,
            "expires_at": expires_at,
            "user_id": user["id"],
            "tenant_id": user["tenant_id"],
            "name": user["name"],
            "email": user["email"],
            "tenant_name": tenant.get("name", "Tenant"),
        }
        try:
            self.client.table("password_reset_tokens").insert(
                {
                    "id": create_id("prt"),
                    "user_id": user["id"],
                    "tenant_id": user["tenant_id"],
                    "token_hash": _token_hash(token),
                    "created_at": utc_now_iso(),
                    "expires_at": expires_at,
                }
            ).execute()
        except Exception as exc:
            if not _is_postgrest_missing_relation(exc, "password_reset_tokens"):
                raise
            self._memory_password_reset_tokens[_token_hash(token)] = payload
        return payload

    def reset_password(self, *, token: str, password: str) -> AuthSession | None:
        now = utc_now_iso()
        try:
            rows = (
                self.client.table("password_reset_tokens")
                .select("id, user_id, tenant_id")
                .eq("token_hash", _token_hash(token))
                .is_("used_at", "null")
                .gt("expires_at", now)
                .limit(1)
                .execute()
                .data
            )
        except Exception as exc:
            if not _is_postgrest_missing_relation(exc, "password_reset_tokens"):
                raise
            return self._reset_password_from_memory_token(token=token, password=password, now=now)
        if not rows:
            return None
        row = rows[0]
        self.client.table("users").update({"password_hash": _hash_password(password)}).eq("id", row["user_id"]).eq("tenant_id", row["tenant_id"]).execute()
        self.client.table("password_reset_tokens").update({"used_at": now}).eq("id", row["id"]).execute()
        self.client.table("auth_sessions").update({"revoked_at": now}).eq("user_id", row["user_id"]).eq("tenant_id", row["tenant_id"]).is_("revoked_at", "null").execute()
        return self.create_auth_session(user_id=row["user_id"], tenant_id=row["tenant_id"])

    def _reset_password_from_memory_token(self, *, token: str, password: str, now: str) -> AuthSession | None:
        token_hash = _token_hash(token)
        row = self._memory_password_reset_tokens.pop(token_hash, None)
        if not row or row["expires_at"] <= now:
            return None
        self.client.table("users").update({"password_hash": _hash_password(password)}).eq("id", row["user_id"]).eq("tenant_id", row["tenant_id"]).execute()
        self.client.table("auth_sessions").update({"revoked_at": now}).eq("user_id", row["user_id"]).eq("tenant_id", row["tenant_id"]).is_("revoked_at", "null").execute()
        return self.create_auth_session(user_id=row["user_id"], tenant_id=row["tenant_id"])

    def list_members(self, *, tenant_id: str) -> list[dict]:
        return self.client.table("users").select("id, tenant_id, name, email, role, created_at").eq("tenant_id", tenant_id).order("name").execute().data

    def invite_member(self, *, tenant_id: str, actor_user_id: str, name: str, email: str, password: str) -> dict:
        session = self.get_user_session(user_id=actor_user_id, tenant_id=tenant_id)
        if not session or session.user.role != "admin":
            raise PermissionError("Only tenant admins can invite members.")
        user_id = create_id("usr")
        normalized_email = _normalize_email(email)
        if self.client.table("users").select("id").eq("email", normalized_email).limit(1).execute().data:
            raise ValueError("An account with this email already exists.")
        row = self.client.table("users").insert(
            {
                "id": user_id,
                "tenant_id": tenant_id,
                "name": name,
                "email": normalized_email,
                "password_hash": _hash_password(password),
                "role": "member",
                "created_at": utc_now_iso(),
            }
        ).execute().data[0]
        return {key: row[key] for key in ("id", "tenant_id", "name", "email", "role", "created_at")}

    def remove_member(self, *, tenant_id: str, actor_user_id: str, user_id: str) -> bool:
        session = self.get_user_session(user_id=actor_user_id, tenant_id=tenant_id)
        if not session or session.user.role != "admin":
            raise PermissionError("Only tenant admins can remove members.")
        if actor_user_id == user_id:
            raise ValueError("Admins cannot remove their own account.")
        row = self.client.table("users").select("id, role").eq("tenant_id", tenant_id).eq("id", user_id).limit(1).execute().data
        if not row:
            return False
        if row[0]["role"] == "admin":
            raise ValueError("Admin users cannot be removed from this in-app member flow.")
        self.client.table("users").delete().eq("tenant_id", tenant_id).eq("id", user_id).execute()
        return True

    def get_user_session(self, *, user_id: str, tenant_id: str) -> AuthSession | None:
        row = self.client.table("users").select("id, tenant_id, name, email, role, tenants(name)").eq("tenant_id", tenant_id).eq("id", user_id).limit(1).execute().data
        if not row:
            return None
        user = row[0]
        tenant = user.get("tenants") or {}
        return AuthSession(
            user=TenantUser(
                id=user["id"],
                tenant_id=user["tenant_id"],
                tenant_name=tenant.get("name", "Tenant"),
                name=user["name"],
                email=user["email"],
                role=user["role"],
            )
        )

    def create_auth_session(self, *, user_id: str, tenant_id: str) -> AuthSession:
        access_token = secrets.token_urlsafe(32)
        expires_at = _session_expires_at(self.session_ttl_hours)
        self.client.table("auth_sessions").insert(
            {
                "id": create_id("ses"),
                "user_id": user_id,
                "tenant_id": tenant_id,
                "token_hash": _token_hash(access_token),
                "created_at": utc_now_iso(),
                "expires_at": expires_at,
            }
        ).execute()
        session = self.get_user_session(user_id=user_id, tenant_id=tenant_id)
        if not session:
            raise RuntimeError("Authenticated user could not be loaded.")
        return session.model_copy(update={"access_token": access_token, "expires_at": expires_at})

    def get_auth_session(self, *, access_token: str) -> AuthSession | None:
        row = (
            self.client.table("auth_sessions")
            .select("user_id, tenant_id, expires_at")
            .eq("token_hash", _token_hash(access_token))
            .is_("revoked_at", "null")
            .gt("expires_at", utc_now_iso())
            .limit(1)
            .execute()
            .data
        )
        if not row:
            return None
        session = self.get_user_session(user_id=row[0]["user_id"], tenant_id=row[0]["tenant_id"])
        if not session:
            return None
        return session.model_copy(update={"access_token": access_token, "expires_at": row[0]["expires_at"]})

    def get_report_by_file_hash(self, tenant_id: str, file_hash: str) -> dict | None:
        row = (
            self.client.table("documents")
            .select("id")
            .eq("tenant_id", tenant_id)
            .eq("file_hash", file_hash)
            .limit(1)
            .execute()
            .data
        )
        if not row:
            return None
        return self.get_report(tenant_id, row[0]["id"])

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
        stored_file: StoredFile,
        trade: str,
    ) -> None:
        timestamp = utc_now_iso()
        project = (
            self.client.table("projects")
            .upsert(
                {
                    "id": create_id("prj"),
                    "tenant_id": tenant_id,
                    "slug": _slug(project_name),
                    "name": project_name,
                    "site_name": site_name,
                    "address": address,
                    "created_at": timestamp,
                },
                on_conflict="tenant_id,slug",
            )
            .execute()
            .data[0]
        )

        document_payload = {
            "id": document_id,
            "tenant_id": tenant_id,
            "project_id": project["id"],
            "file_hash": file_hash,
            "file_tag": file_tag,
            "site_name": site_name,
            "address": address,
            "source_filename": source_filename,
            "storage_path": stored_file.storage_path,
            "download_url": stored_file.download_url,
            "inspection_type": "Pending extraction",
            "trade": trade or "General",
            "inspector": "Pending extractor",
            "report_date": timestamp[:10],
            "status": "In progress",
            "summary": "Extraction pending.",
            "units_json": [],
            "uploaded_at": timestamp,
        }
        try:
            self.client.table("documents").insert(document_payload).execute()
        except Exception as exc:
            if not (_is_postgrest_missing_column(exc, "site_name") or _is_postgrest_missing_column(exc, "address")):
                raise
            document_payload.pop("site_name", None)
            document_payload.pop("address", None)
            self.client.table("documents").insert(document_payload).execute()

        self.client.table("jobs").insert(
            {
                "id": job_id,
                "document_id": document_id,
                "status": "running",
                "extractor": "pending",
                "started_at": timestamp,
            }
        ).execute()

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
        timestamp = utc_now_iso()
        project = (
            self.client.table("projects")
            .upsert(
                {
                    "id": create_id("prj"),
                    "tenant_id": tenant_id,
                    "slug": _slug(extraction.project_name),
                    "name": extraction.project_name,
                    "site_name": extraction.site_name,
                    "address": extraction.address,
                    "created_at": timestamp,
                },
                on_conflict="tenant_id,slug",
            )
            .execute()
            .data[0]
        )

        document_payload = {
            "project_id": project["id"],
            "source_filename": source_filename,
            "storage_path": stored_file.storage_path,
            "download_url": stored_file.download_url,
            "site_name": extraction.site_name,
            "address": extraction.address,
            "inspection_type": extraction.inspection_type,
            "trade": extraction.trade,
            "inspector": extraction.inspector,
            "report_date": extraction.report_date,
            "status": extraction.overall_outcome,
            "summary": extraction.summary,
            "units_json": extraction.units,
            "uploaded_at": timestamp,
        }
        try:
            self.client.table("documents").update(document_payload).eq("tenant_id", tenant_id).eq("id", document_id).execute()
        except Exception as exc:
            if not (_is_postgrest_missing_column(exc, "site_name") or _is_postgrest_missing_column(exc, "address")):
                raise
            document_payload.pop("site_name", None)
            document_payload.pop("address", None)
            self.client.table("documents").update(document_payload).eq("tenant_id", tenant_id).eq("id", document_id).execute()

        self.client.table("findings").delete().eq("tenant_id", tenant_id).eq("document_id", document_id).execute()

        findings_payload = []
        for finding in extraction.findings:
            findings_payload.append(
                {
                    "id": create_id("issue"),
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "project_id": project["id"],
                    "title": finding.title,
                    "description": finding.description,
                    "category": finding.category,
                    "trade": finding.trade,
                    "severity": finding.severity,
                    "status": finding.status,
                    "location": finding.location,
                    "unit_label": finding.unit_label,
                    "recurrence_risk": finding.recurrence_risk,
                    "reinspections": 0,
                    "last_sent_to": None,
                    "created_at": timestamp,
                    "closed_at": timestamp if finding.status == "Closed" else None,
                }
            )
        if findings_payload:
            self.client.table("findings").insert(findings_payload).execute()

        predicted_payload = [
            {
                "id": create_id("pred"),
                "tenant_id": tenant_id,
                "project_id": project["id"],
                "inspection_type": prediction.inspection_type,
                "site_name": prediction.site_name,
                "expected_date": prediction.expected_date,
                "risk_level": prediction.risk_level,
                "source": prediction.source,
                "created_at": timestamp,
            }
            for prediction in extraction.predicted_inspections
        ]
        if predicted_payload:
            self.client.table("predicted_inspections").insert(predicted_payload).execute()

        self.client.table("jobs").update(
            {
                "status": "completed",
                "extractor": extractor_name,
                "raw_text_excerpt": raw_text[:4000],
                "raw_payload_json": raw_payload,
                "completed_at": timestamp,
            }
        ).eq("id", job_id).execute()

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
        timestamp = utc_now_iso()
        self.client.table("documents").update(
            {
                "status": "Reviewing",
                "summary": "Extraction failed. Manual review is required before trusting this report.",
            }
        ).eq("tenant_id", tenant_id).eq("id", document_id).execute()
        self.client.table("jobs").update(
            {
                "status": "failed",
                "extractor": extractor_name,
                "error_message": error_message,
                "raw_text_excerpt": raw_text[:4000],
                "completed_at": timestamp,
            }
        ).eq("id", job_id).execute()

    def load_snapshot(self, tenant_id: str) -> RepositorySnapshot:
        projects = self.client.table("projects").select("*").eq("tenant_id", tenant_id).order("name").execute().data
        documents = self.client.table("documents").select("*, projects(*)").eq("tenant_id", tenant_id).order("report_date", desc=True).execute().data
        jobs = self.client.table("jobs").select("*, documents!inner(tenant_id)").eq("documents.tenant_id", tenant_id).order("started_at", desc=True).execute().data
        findings = self.client.table("findings").select("*, projects(*), documents(*)").eq("tenant_id", tenant_id).execute().data
        predictions = self.client.table("predicted_inspections").select("*").eq("tenant_id", tenant_id).order("expected_date").execute().data

        normalized_documents = []
        for row in documents:
            project = row.get("projects") or {}
            normalized_documents.append(
                {
                    **row,
                    "project_name": project.get("name", "Unknown project"),
                    "project_slug": project.get("slug", "unknown-project"),
                    "site_name": row.get("site_name") or project.get("site_name", "Unknown site"),
                    "address": row.get("address") or project.get("address"),
                    "units": row.get("units_json") or [],
                }
            )

        normalized_findings = []
        for row in findings:
            project = row.get("projects") or {}
            document = row.get("documents") or {}
            normalized_findings.append(
                {
                    **row,
                    "project_name": project.get("name", "Unknown project"),
                    "project_slug": project.get("slug", "unknown-project"),
                    "site_name": project.get("site_name", "Unknown site"),
                    "inspection_type": document.get("inspection_type", "Unknown inspection"),
                    "document_status": document.get("status", "Reviewing"),
                }
            )

        return RepositorySnapshot(
            projects=projects,
            documents=normalized_documents,
            jobs=jobs,
            findings=normalized_findings,
            predicted_inspections=predictions,
        )

    def get_report(self, tenant_id: str, report_id: str) -> dict | None:
        snapshot = self.load_snapshot(tenant_id)
        for document in snapshot.documents:
            if document["id"] == report_id:
                document["issues"] = [
                    {
                        "id": finding["id"],
                        "title": finding["title"],
                        "severity": finding["severity"],
                    }
                    for finding in snapshot.findings
                    if finding["document_id"] == report_id
                ]
                return document
        return None

    def delete_report(self, tenant_id: str, report_id: str) -> dict | None:
        row = self.client.table("documents").select("id, project_id, storage_path").eq("tenant_id", tenant_id).eq("id", report_id).limit(1).execute().data
        if not row:
            return None
        document = row[0]
        project_id = document["project_id"]
        self.client.table("jobs").delete().eq("document_id", report_id).execute()
        self.client.table("findings").delete().eq("tenant_id", tenant_id).eq("document_id", report_id).execute()
        self.client.table("documents").delete().eq("tenant_id", tenant_id).eq("id", report_id).execute()
        remaining = self.client.table("documents").select("id").eq("tenant_id", tenant_id).eq("project_id", project_id).limit(1).execute().data
        if not remaining:
            self.client.table("predicted_inspections").delete().eq("tenant_id", tenant_id).eq("project_id", project_id).execute()
            self.client.table("projects").delete().eq("tenant_id", tenant_id).eq("id", project_id).execute()
        return {"id": report_id, "storage_path": document.get("storage_path")}

    def get_issue(self, tenant_id: str, issue_id: str) -> dict | None:
        snapshot = self.load_snapshot(tenant_id)
        return next((item for item in snapshot.findings if item["id"] == issue_id), None)

    def update_issue(
        self,
        tenant_id: str,
        issue_id: str,
        *,
        status: str | None = None,
        reinspections: int | None = None,
        last_sent_to: str | None = None,
    ) -> dict | None:
        payload: dict = {}
        if status:
            payload["status"] = status
            payload["closed_at"] = utc_now_iso() if status == "Closed" else None
        if reinspections is not None:
            payload["reinspections"] = reinspections
        if last_sent_to:
            payload["last_sent_to"] = last_sent_to
        if payload:
            self.client.table("findings").update(payload).eq("tenant_id", tenant_id).eq("id", issue_id).execute()
        return self.get_issue(tenant_id, issue_id)


def build_repository(settings: Settings) -> RepositoryBackend:
    if settings.repository_mode == "supabase":
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required when SOTERRA_REPOSITORY_MODE=supabase."
            )
        return SupabaseRepository(
            url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            session_ttl_hours=settings.auth_session_ttl_hours,
        )

    return SqliteRepository(
        db_path=settings.local_db_path,
        schema_path=settings.repo_root / "soterra_backend" / "local_schema.sql",
        session_ttl_hours=settings.auth_session_ttl_hours,
        bootstrap_demo_account=settings.bootstrap_demo_account,
        demo_admin_password=settings.demo_admin_password,
    )


_DOCUMENT_QUERY = """
SELECT
  d.*,
  p.name AS project_name,
  p.slug AS project_slug,
  COALESCE(d.site_name, p.site_name) AS site_name,
  COALESCE(d.address, p.address) AS address
FROM documents d
JOIN projects p ON p.id = d.project_id
WHERE d.tenant_id = ?
ORDER BY d.report_date DESC, d.uploaded_at DESC
"""

_JOBS_QUERY = """
SELECT j.*
FROM jobs j
JOIN documents d ON d.id = j.document_id
WHERE d.tenant_id = ?
ORDER BY j.started_at DESC
"""

_FINDING_QUERY = """
SELECT
  f.*,
  p.name AS project_name,
  p.slug AS project_slug,
  COALESCE(d.site_name, p.site_name) AS site_name,
  d.inspection_type AS inspection_type,
  d.status AS document_status
FROM findings f
JOIN projects p ON p.id = f.project_id
JOIN documents d ON d.id = f.document_id
WHERE f.tenant_id = ?
ORDER BY f.created_at DESC
"""

_PREDICTION_QUERY = """
SELECT
  pi.*,
  p.slug AS project_slug,
  p.name AS project_name
FROM predicted_inspections pi
JOIN projects p ON p.id = pi.project_id
WHERE pi.tenant_id = ?
ORDER BY pi.expected_date ASC
"""


def _dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _email_domain(email: str) -> str | None:
    _, separator, domain = _normalize_email(email).rpartition("@")
    if not separator:
        return None
    return domain.strip() or None


def _is_public_email_domain(domain: str) -> bool:
    return domain in {
        "aol.com",
        "icloud.com",
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "live.com",
        "mail.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "proton.me",
        "protonmail.com",
        "yahoo.com",
        "ymail.com",
    }


def _is_postgrest_missing_relation(exc: Exception, relation: str) -> bool:
    text = str(exc)
    return "PGRST205" in text and relation in text


def _is_postgrest_missing_column(exc: Exception, column: str) -> bool:
    text = str(exc)
    return ("PGRST204" in text or "PGRST204" in repr(exc)) and column in text


def _token_hash(access_token: str) -> str:
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()


def _session_expires_at(ttl_hours: int) -> str:
    ttl = max(1, ttl_hours)
    return (datetime.now(tz=UTC).replace(microsecond=0) + timedelta(hours=ttl)).isoformat()


def _hash_password(password: str) -> str:
    try:
        from argon2 import PasswordHasher
    except ModuleNotFoundError as exc:
        raise RuntimeError("argon2-cffi is required for secure password hashing.") from exc

    return PasswordHasher().hash(password)


def _verify_password(password: str, encoded: str) -> bool:
    if encoded.startswith("$argon2"):
        try:
            from argon2 import PasswordHasher
            from argon2.exceptions import VerifyMismatchError, VerificationError
        except ModuleNotFoundError as exc:
            raise RuntimeError("argon2-cffi is required for secure password verification.") from exc
        try:
            return PasswordHasher().verify(encoded, password)
        except (VerifyMismatchError, VerificationError):
            return False

    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)).hex()
        return hmac.compare_digest(digest, expected)
    except ValueError:
        return False


def _normalize_document(row: sqlite3.Row) -> dict:
    payload = _dict(row)
    payload["units"] = json.loads(payload.pop("units_json", "[]"))
    return payload


def _normalize_finding(row: sqlite3.Row) -> dict:
    return _dict(row)


def _slug(value: str) -> str:
    import re

    cleaned = value.strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "unknown-project"


def _closest_project_slug(connection: sqlite3.Connection, tenant_id: str, slug: str) -> dict | None:
    rows = connection.execute("SELECT id, slug, name FROM projects WHERE tenant_id = ?", (tenant_id,)).fetchall()
    if not rows:
        return None

    best_row = None
    best_score = 0.0
    for row in rows:
        candidate_slug = (row["slug"] or "").strip().lower()
        if not candidate_slug:
            continue
        score = SequenceMatcher(a=slug, b=candidate_slug).ratio()
        if score > best_score:
            best_score = score
            best_row = row

    # Only accept very close matches to avoid accidental merges.
    if best_row and best_score >= 0.92:
        return {"id": best_row["id"], "slug": best_row["slug"], "name": best_row["name"]}
    return None
