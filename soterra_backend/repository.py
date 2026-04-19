from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

from .config import Settings
from .models import ExtractionResult, RepositorySnapshot, StoredFile
from .utils import create_id, utc_now_iso


class RepositoryBackend(Protocol):
    def initialize(self) -> None:
        ...

    def get_report_by_file_hash(self, file_hash: str) -> dict | None:
        ...

    def create_placeholder_document(
        self,
        *,
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
        document_id: str,
        job_id: str,
        extractor_name: str,
        error_message: str,
        raw_text: str,
    ) -> None:
        ...

    def load_snapshot(self) -> RepositorySnapshot:
        ...

    def get_report(self, report_id: str) -> dict | None:
        ...

    def get_issue(self, issue_id: str) -> dict | None:
        ...

    def update_issue(
        self,
        issue_id: str,
        *,
        status: str | None = None,
        reinspections: int | None = None,
        last_sent_to: str | None = None,
    ) -> dict | None:
        ...


class SqliteRepository:
    def __init__(self, db_path: Path, schema_path: Path) -> None:
        self.db_path = db_path
        self.schema_path = schema_path
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
        self._ensure_document_columns(connection)
        if indexes_sql.strip():
            connection.executescript(indexes_sql)
        self._refresh_views(connection)

    def get_report_by_file_hash(self, file_hash: str) -> dict | None:
        row = None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM documents WHERE file_hash = ? LIMIT 1",
                (file_hash,),
            ).fetchone()
        if not row:
            return None
        return self.get_report(row["id"])

    def create_placeholder_document(
        self,
        *,
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
                "SELECT id FROM projects WHERE slug = ?",
                (project_slug,),
            ).fetchone()
            if existing:
                project_id = existing["id"]
            else:
                # If the user typed a close-but-not-identical project name (typo, spacing, punctuation),
                # map it to the nearest existing slug to avoid creating duplicate projects locally.
                candidate = _closest_project_slug(connection, project_slug)
                if candidate:
                    project_id = candidate["id"]
                    project_slug = candidate["slug"]
                    project_name = candidate["name"]
                else:
                    connection.execute(
                        """
                        INSERT INTO projects (id, slug, name, site_name, address, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (project_id, project_slug, project_name, site_name, address, timestamp),
                    )

            connection.execute(
                """
                INSERT INTO documents (
                  id, project_id, file_hash, file_tag, source_filename, storage_path, download_url, inspection_type,
                  trade, inspector, report_date, status, summary, units_json, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    project_id,
                    file_hash,
                    file_tag,
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
            document_row = connection.execute("SELECT project_id FROM documents WHERE id = ?", (document_id,)).fetchone()
            project_id = document_row["project_id"] if document_row else None
            project_row = None
            if project_id:
                project_row = connection.execute("SELECT id, slug FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not project_row:
                # Fallback for any legacy rows that might not have a valid project reference.
                project_slug = _slug(extraction.project_name)
                project_row = connection.execute("SELECT id, slug FROM projects WHERE slug = ?", (project_slug,)).fetchone()
                project_id = project_row["id"] if project_row else create_id("prj")
            else:
                project_slug = project_row["slug"]

            if project_row:
                connection.execute(
                    """
                    UPDATE projects
                    SET name = ?, site_name = ?, address = ?
                    WHERE id = ?
                    """,
                    (
                        extraction.project_name,
                        extraction.site_name,
                        extraction.address,
                        project_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO projects (id, slug, name, site_name, address, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
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
                    inspection_type = ?, trade = ?, inspector = ?, report_date = ?, status = ?,
                    summary = ?, units_json = ?, uploaded_at = ?
                WHERE id = ?
                """,
                (
                    project_id,
                    source_filename,
                    stored_file.storage_path,
                    stored_file.download_url,
                    extraction.inspection_type,
                    extraction.trade,
                    extraction.inspector,
                    extraction.report_date,
                    extraction.overall_outcome,
                    extraction.summary,
                    json.dumps(extraction.units),
                    timestamp,
                    document_id,
                ),
            )

            connection.execute("DELETE FROM findings WHERE document_id = ?", (document_id,))
            connection.execute(
                "DELETE FROM predicted_inspections WHERE project_id = ? AND source LIKE ?",
                (project_id, f"{extraction.inspection_type}%"),
            )

            for finding in extraction.findings:
                connection.execute(
                    """
                    INSERT INTO findings (
                      id, document_id, project_id, title, description, category, trade, severity,
                      status, location, unit_label, recurrence_risk, reinspections, last_sent_to,
                      created_at, closed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        create_id("issue"),
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
                      id, project_id, inspection_type, site_name, expected_date, risk_level, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        create_id("pred"),
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
        document_id: str,
        job_id: str,
        extractor_name: str,
        error_message: str,
        raw_text: str,
    ) -> None:
        timestamp = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE documents SET status = ?, summary = ? WHERE id = ?",
                (
                    "Reviewing",
                    "Extraction failed. Manual review is required before trusting this report.",
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

    def load_snapshot(self) -> RepositorySnapshot:
        with self._connect() as connection:
            projects = [_dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY name")]
            documents = [_normalize_document(row) for row in connection.execute(_DOCUMENT_QUERY)]
            jobs = [_dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY started_at DESC")]
            findings = [_normalize_finding(row) for row in connection.execute(_FINDING_QUERY)]
            predictions = [_dict(row) for row in connection.execute(_PREDICTION_QUERY)]

        return RepositorySnapshot(
            projects=projects,
            documents=documents,
            jobs=jobs,
            findings=findings,
            predicted_inspections=predictions,
        )

    def get_report(self, report_id: str) -> dict | None:
        snapshot = self.load_snapshot()
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

    def get_issue(self, issue_id: str) -> dict | None:
        snapshot = self.load_snapshot()
        return next((item for item in snapshot.findings if item["id"] == issue_id), None)

    def update_issue(
        self,
        issue_id: str,
        *,
        status: str | None = None,
        reinspections: int | None = None,
        last_sent_to: str | None = None,
    ) -> dict | None:
        timestamp = utc_now_iso()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM findings WHERE id = ?",
                (issue_id,),
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
                WHERE id = ?
                """,
                (next_status, next_reinspections, next_last_sent_to, next_closed_at, issue_id),
            )

        return self.get_issue(issue_id)

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
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_file_hash_unique ON documents(file_hash)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_file_tag_unique ON documents(file_tag)")

    def _refresh_views(self, connection: sqlite3.Connection) -> None:
        # Recreate analytics views so local databases pick up view fixes without a manual reset.
        connection.executescript(
            """
            DROP VIEW IF EXISTS analytics_report_summary_v;
            DROP VIEW IF EXISTS analytics_company_metrics_v;
            DROP VIEW IF EXISTS analytics_project_metrics_v;
            DROP VIEW IF EXISTS analytics_top_failure_drivers_v;
            DROP VIEW IF EXISTS analytics_upcoming_risk_v;
            """
        )
        schema_sql = self.schema_path.read_text(encoding="utf-8")
        marker = "CREATE VIEW IF NOT EXISTS analytics_report_summary_v AS"
        _, _, view_sql = schema_sql.partition(marker)
        if not view_sql:
            raise RuntimeError("Could not locate analytics view definitions in local_schema.sql.")
        view_sql = f"{marker}{view_sql}"
        connection.executescript(view_sql)


class SupabaseRepository:
    def __init__(self, *, url: str, service_role_key: str) -> None:
        try:
            from supabase import create_client
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "The supabase package is not installed. Run the Python dependency install step first."
            ) from exc

        self.client = create_client(url, service_role_key)

    def initialize(self) -> None:
        return None

    def get_report_by_file_hash(self, file_hash: str) -> dict | None:
        row = (
            self.client.table("documents")
            .select("id")
            .eq("file_hash", file_hash)
            .limit(1)
            .execute()
            .data
        )
        if not row:
            return None
        return self.get_report(row[0]["id"])

    def create_placeholder_document(
        self,
        *,
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
                    "slug": _slug(project_name),
                    "name": project_name,
                    "site_name": site_name,
                    "address": address,
                    "created_at": timestamp,
                },
                on_conflict="slug",
            )
            .execute()
            .data[0]
        )

        self.client.table("documents").insert(
            {
                "id": document_id,
                "project_id": project["id"],
                "file_hash": file_hash,
                "file_tag": file_tag,
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
        ).execute()

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
                    "slug": _slug(extraction.project_name),
                    "name": extraction.project_name,
                    "site_name": extraction.site_name,
                    "address": extraction.address,
                    "created_at": timestamp,
                },
                on_conflict="slug",
            )
            .execute()
            .data[0]
        )

        self.client.table("documents").update(
            {
                "project_id": project["id"],
                "source_filename": source_filename,
                "storage_path": stored_file.storage_path,
                "download_url": stored_file.download_url,
                "inspection_type": extraction.inspection_type,
                "trade": extraction.trade,
                "inspector": extraction.inspector,
                "report_date": extraction.report_date,
                "status": extraction.overall_outcome,
                "summary": extraction.summary,
                "units_json": extraction.units,
                "uploaded_at": timestamp,
            }
        ).eq("id", document_id).execute()

        self.client.table("findings").delete().eq("document_id", document_id).execute()

        findings_payload = []
        for finding in extraction.findings:
            findings_payload.append(
                {
                    "id": create_id("issue"),
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
        ).eq("id", document_id).execute()
        self.client.table("jobs").update(
            {
                "status": "failed",
                "extractor": extractor_name,
                "error_message": error_message,
                "raw_text_excerpt": raw_text[:4000],
                "completed_at": timestamp,
            }
        ).eq("id", job_id).execute()

    def load_snapshot(self) -> RepositorySnapshot:
        projects = self.client.table("projects").select("*").order("name").execute().data
        documents = self.client.table("documents").select("*, projects(*)").order("report_date", desc=True).execute().data
        jobs = self.client.table("jobs").select("*").order("started_at", desc=True).execute().data
        findings = self.client.table("findings").select("*, projects(*), documents(*)").execute().data
        predictions = self.client.table("predicted_inspections").select("*").order("expected_date").execute().data

        normalized_documents = []
        for row in documents:
            project = row.get("projects") or {}
            normalized_documents.append(
                {
                    **row,
                    "project_name": project.get("name", "Unknown project"),
                    "project_slug": project.get("slug", "unknown-project"),
                    "site_name": project.get("site_name", "Unknown site"),
                    "address": project.get("address"),
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

    def get_report(self, report_id: str) -> dict | None:
        snapshot = self.load_snapshot()
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

    def get_issue(self, issue_id: str) -> dict | None:
        snapshot = self.load_snapshot()
        return next((item for item in snapshot.findings if item["id"] == issue_id), None)

    def update_issue(
        self,
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
            self.client.table("findings").update(payload).eq("id", issue_id).execute()
        return self.get_issue(issue_id)


def build_repository(settings: Settings) -> RepositoryBackend:
    if settings.repository_mode == "supabase":
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required when SOTERRA_REPOSITORY_MODE=supabase."
            )
        return SupabaseRepository(
            url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
        )

    return SqliteRepository(
        db_path=settings.local_db_path,
        schema_path=settings.repo_root / "soterra_backend" / "local_schema.sql",
    )


_DOCUMENT_QUERY = """
SELECT
  d.*,
  p.name AS project_name,
  p.slug AS project_slug,
  p.site_name AS site_name,
  p.address AS address
FROM documents d
JOIN projects p ON p.id = d.project_id
ORDER BY d.report_date DESC, d.uploaded_at DESC
"""

_FINDING_QUERY = """
SELECT
  f.*,
  p.name AS project_name,
  p.slug AS project_slug,
  p.site_name AS site_name,
  d.inspection_type AS inspection_type,
  d.status AS document_status
FROM findings f
JOIN projects p ON p.id = f.project_id
JOIN documents d ON d.id = f.document_id
ORDER BY f.created_at DESC
"""

_PREDICTION_QUERY = """
SELECT
  pi.*,
  p.slug AS project_slug,
  p.name AS project_name
FROM predicted_inspections pi
JOIN projects p ON p.id = pi.project_id
ORDER BY pi.expected_date ASC
"""


def _dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


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


def _closest_project_slug(connection: sqlite3.Connection, slug: str) -> dict | None:
    rows = connection.execute("SELECT id, slug, name FROM projects").fetchall()
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
