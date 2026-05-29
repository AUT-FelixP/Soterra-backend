from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..config import Settings
from ..extractors import build_extractor
from ..extractors.base import ExtractionRequest
from ..models import ExtractionResult, IngestionOutcome
from ..repositories.base import RepositoryBackend
from ..storage.base import StorageBackend
from ..utils import create_id, summarize_status

logger = logging.getLogger("soterra_backend")

PDF_CONTENT_TYPES = {"application/pdf", "application/octet-stream"}
UPLOAD_CHUNK_SIZE = 1024 * 1024


@dataclass
class UploadContext:
    tenant_id: str
    filename: str
    content: bytes
    content_type: str
    project_name: str
    site_name: str
    trade: str
    address: str | None = None


@dataclass(frozen=True)
class IngestionStart:
    document_id: str
    job_id: str
    file_hash: str
    file_tag: str
    stored_file: object


class ReportIngestionService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: RepositoryBackend,
        storage: StorageBackend,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.extractor = build_extractor(settings)

    def ingest(self, upload: UploadContext) -> IngestionOutcome:
        outcome, start = self.start_ingest(upload)
        if outcome:
            return outcome
        assert start is not None
        report = self.finish_ingest(start, upload)
        return IngestionOutcome(item=report, is_duplicate=False)

    def start_ingest(self, upload: UploadContext) -> tuple[IngestionOutcome | None, IngestionStart | None]:
        file_hash = _file_hash(upload.content)
        logger.info(
            "ingest_start filename=%s bytes=%d file_hash=%s extractor_mode=%s",
            upload.filename,
            len(upload.content),
            file_hash[:12],
            self.settings.extractor_mode,
        )
        existing_report = self.repository.get_report_by_file_hash(upload.tenant_id, file_hash)
        if existing_report:
            logger.info("ingest_duplicate file_hash=%s report_id=%s", file_hash[:12], existing_report.get("id"))
            return IngestionOutcome(item=existing_report, is_duplicate=True), None

        document_id = create_id("rpt")
        job_id = create_id("job")
        file_tag = f"{upload.tenant_id}-file-{file_hash[:12]}"

        stored_file = self.storage.store(
            document_id=document_id,
            filename=upload.filename,
            content=upload.content,
            content_type=upload.content_type,
        )
        logger.info("file_stored report_id=%s storage_path=%s", document_id, getattr(stored_file, "storage_path", "?"))

        self.repository.create_placeholder_document(
            tenant_id=upload.tenant_id,
            document_id=document_id,
            job_id=job_id,
            file_hash=file_hash,
            file_tag=file_tag,
            project_name=upload.project_name,
            site_name=upload.site_name,
            address=upload.address,
            source_filename=upload.filename,
            stored_file=stored_file,  # type: ignore[arg-type]
            trade=upload.trade,
        )

        return None, IngestionStart(
            document_id=document_id,
            job_id=job_id,
            file_hash=file_hash,
            file_tag=file_tag,
            stored_file=stored_file,
        )

    def finish_ingest(self, start: IngestionStart, upload: UploadContext) -> dict:
        started = time.perf_counter()
        raw_text = ""
        extractor_name = self.settings.extractor_mode

        try:
            logger.info("extraction_start report_id=%s", start.document_id)
            extraction, raw_text, extractor_name = self._extract(upload, start.stored_file)
            logger.info(
                "extraction_done report_id=%s extractor=%s findings=%d predicted=%d raw_text_len=%d",
                start.document_id,
                extractor_name,
                len(extraction.findings),
                len(extraction.predicted_inspections),
                len(raw_text),
            )
            normalized = extraction.model_copy(
                update={"overall_outcome": summarize_status([item.severity for item in extraction.findings])}
            )
            self.repository.complete_document(
                tenant_id=upload.tenant_id,
                document_id=start.document_id,
                job_id=start.job_id,
                source_filename=upload.filename,
                stored_file=start.stored_file,  # type: ignore[arg-type]
                extraction=normalized,
                extractor_name=extractor_name,
                raw_text=raw_text,
                raw_payload=normalized.model_dump(),
            )
            logger.info(
                "persist_done report_id=%s elapsed_ms=%d",
                start.document_id,
                int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            self.repository.fail_job(
                tenant_id=upload.tenant_id,
                document_id=start.document_id,
                job_id=start.job_id,
                extractor_name=extractor_name,
                error_message=str(exc),
                raw_text=raw_text,
            )
            logger.exception(
                "ingest_failed report_id=%s elapsed_ms=%d",
                start.document_id,
                int((time.perf_counter() - started) * 1000),
            )
            raise

        report = self.repository.get_report(upload.tenant_id, start.document_id)
        if not report:
            raise RuntimeError("The report was processed but could not be loaded back from the repository.")
        return report

    def _extract(self, upload: UploadContext, stored_file) -> tuple[ExtractionResult, str, str]:
        with tempfile.NamedTemporaryFile(suffix=".pdf", prefix="soterra-upload-", delete=False) as tmp_file:
            tmp_file.write(upload.content)
            temp_pdf_path = Path(tmp_file.name)

        try:
            artifacts = self.extractor.extract(
                ExtractionRequest(
                    filename=upload.filename,
                    content=upload.content,
                    project_name=upload.project_name,
                    site_name=upload.site_name,
                    trade=upload.trade,
                    address=upload.address,
                ),
                temp_pdf_path,
            )
        finally:
            temp_pdf_path.unlink(missing_ok=True)

        return artifacts.extraction, artifacts.raw_text, artifacts.extractor_name


class ReportUploadService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: RepositoryBackend,
        storage: StorageBackend,
        ingestion_service: ReportIngestionService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.ingestion_service = ingestion_service

    async def upload_report(
        self,
        *,
        background_tasks: BackgroundTasks,
        file: UploadFile,
        tenant_id: str,
        project: str,
        site: str,
        trade: str,
    ) -> JSONResponse:
        content = await read_limited_upload(file, self.settings.max_upload_bytes)
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

        outcome, start = self.ingestion_service.start_ingest(upload_ctx)
        if outcome:
            return JSONResponse({"item": outcome.item, "isDuplicate": outcome.is_duplicate}, status_code=200)

        assert start is not None
        if self.settings.process_inline:
            try:
                report = self.ingestion_service.finish_ingest(start, upload_ctx)
            except Exception as exc:
                deleted = self.repository.delete_report(tenant_id, start.document_id)
                storage_path = (deleted or {}).get("storage_path") or getattr(start.stored_file, "storage_path", None)
                if storage_path:
                    self.storage.delete(storage_path=storage_path)
                raise HTTPException(status_code=422, detail="Report extraction failed. The upload was not saved.") from exc
            return JSONResponse({"item": report, "isDuplicate": False}, status_code=201)

        background_tasks.add_task(self.ingestion_service.finish_ingest, start, upload_ctx)
        placeholder = self.repository.get_report(tenant_id, start.document_id) or {"id": start.document_id}
        return JSONResponse(
            {"item": placeholder, "isDuplicate": False, "isProcessing": True},
            status_code=202,
        )

    def list_reports(self, *, tenant_id: str) -> dict:
        from ..analytics import build_report_list

        return build_report_list(self.repository.load_snapshot(tenant_id))

    def get_report(self, *, tenant_id: str, report_id: str) -> dict:
        from ..analytics import build_report_detail

        payload = build_report_detail(self.repository.load_snapshot(tenant_id), report_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Report not found")
        return payload

    def delete_report(self, *, tenant_id: str, report_id: str) -> dict:
        deleted = self.repository.delete_report(tenant_id, report_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Report not found")
        storage_path = deleted.get("storage_path")
        if storage_path:
            self.storage.delete(storage_path=storage_path)
        return {"deleted": True, "id": report_id}

    def bulk_delete_reports(self, *, tenant_id: str, report_ids: list[str]) -> dict:
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
            deleted = self.repository.delete_report(tenant_id, report_id)
            if not deleted:
                missing_ids.append(report_id)
                continue
            storage_path = deleted.get("storage_path")
            if storage_path:
                self.storage.delete(storage_path=storage_path)
            deleted_ids.append(report_id)
        return {"deleted": deleted_ids, "missing": missing_ids, "deletedCount": len(deleted_ids), "missingCount": len(missing_ids)}


async def read_limited_upload(file: UploadFile, max_bytes: int) -> bytes:
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


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
