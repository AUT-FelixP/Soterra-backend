from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..config import Settings
from ..extraction_quality_gate import ExtractionQualityError, validate_extraction_quality
from ..extractors import build_extractor
from ..extractors.base import ExtractionRequest
from ..models import ExtractionResult, IngestionOutcome
from ..repositories.base import RepositoryBackend
from ..storage.base import StorageBackend
from ..utils import create_id, slugify, summarize_status
from .malware import MalwareScanner, NoopMalwareScanner
from .upload_validation import (
    detect_supported_type,
    resolve_filename_conflict,
    validate_readable_file,
)

logger = logging.getLogger("soterra_backend")

UPLOAD_CHUNK_SIZE = 1024 * 1024


@dataclass
class UploadContext:
    tenant_id: str
    filename: str
    stored_filename: str
    content: bytes
    content_type: str
    file_type: Literal["pdf", "docx"]
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
            tenant_id=upload.tenant_id,
            document_id=document_id,
            filename=upload.stored_filename,
            content=upload.content,
            content_type=upload.content_type,
            project_slug=slugify(upload.project_name),
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
            source_filename=upload.stored_filename,
            stored_filename=upload.stored_filename,
            stored_file=stored_file,  # type: ignore[arg-type]
            trade=upload.trade,
            malware_scan_status="clean",
        )

        return None, IngestionStart(
            document_id=document_id,
            job_id=job_id,
            file_hash=file_hash,
            file_tag=file_tag,
            stored_file=stored_file,
        )

    def finish_ingest(self, start: IngestionStart, upload: UploadContext) -> dict:
        """
        Complete extraction and persist the final report.

        Beginner explanation:
        - The placeholder document was already created in start_ingest().
        - This method actually runs extraction.
        - If extraction succeeds, we mark the document completed.
        - If extraction fails, we mark the job failed.
        - This prevents reports from staying stuck in "processing".
        """
        started = time.perf_counter()
        raw_text = ""
        extractor_name = self.settings.extractor_mode
        extraction_metadata: dict = {}

        try:
            logger.info("extraction_start report_id=%s", start.document_id)

            extraction, raw_text, extractor_name, extraction_metadata = self._extract_with_timeout(
                upload,
                start.stored_file,
            )

            logger.info(
                "extraction_done report_id=%s extractor=%s findings=%d predicted=%d raw_text_len=%d",
                start.document_id,
                extractor_name,
                len(extraction.findings),
                len(extraction.predicted_inspections),
                len(raw_text),
            )

            # Quality gate:
            # This checks that we do not accidentally save a report as completed
            # when the text looks like it has issues but findings=[].
            quality_metadata = validate_extraction_quality(extraction, raw_text)

            extraction_metadata = {
                **extraction_metadata,
                **quality_metadata,
            }

            normalized = extraction.model_copy(
                update={
                    "project_name": upload.project_name,
                    "site_name": upload.site_name,
                    "address": upload.address or extraction.address,
                    "overall_outcome": summarize_status([item.severity for item in extraction.findings]),
                }
            )

            self.repository.complete_document(
                tenant_id=upload.tenant_id,
                document_id=start.document_id,
                job_id=start.job_id,
                source_filename=upload.stored_filename,
                stored_file=start.stored_file,  # type: ignore[arg-type]
                extraction=normalized,
                extractor_name=extractor_name,
                raw_text=raw_text,
                raw_payload={
                    **normalized.model_dump(),
                    "extraction_metadata": extraction_metadata,
                },
            )

            logger.info(
                "persist_done report_id=%s elapsed_ms=%d",
                start.document_id,
                int((time.perf_counter() - started) * 1000),
            )

        except ExtractionQualityError as exc:
            self.repository.fail_job(
                tenant_id=upload.tenant_id,
                document_id=start.document_id,
                job_id=start.job_id,
                extractor_name=extractor_name,
                error_message=str(exc),
                raw_text=raw_text,
            )
            logger.warning(
                "ingest_quality_failed report_id=%s diagnostics=%s elapsed_ms=%d",
                start.document_id,
                exc.diagnostics,
                int((time.perf_counter() - started) * 1000),
            )
            raise

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

    def finish_ingest_safely(self, start: IngestionStart, upload: UploadContext) -> None:
        try:
            self.finish_ingest(start, upload)
        except Exception:
            logger.exception("background_ingest_failed report_id=%s", start.document_id)

    def _extract_with_timeout(
            self,
            upload: UploadContext,
            stored_file,
    ) -> tuple[ExtractionResult, str, str, dict]:
        """
        Run extraction with a timeout.

        Beginner explanation:
        - Sometimes OCR/model extraction can hang.
        - This wrapper gives extraction a maximum time.
        - If it takes too long, we raise an error.
        - Then finish_ingest() marks the job as failed instead of leaving it processing.
        """
        timeout_seconds = int(getattr(self.settings, "extraction_timeout_seconds", 90) or 90)

        executor = ThreadPoolExecutor(max_workers=1)

        try:
            future = executor.submit(self._extract, upload, stored_file)

            try:
                return future.result(timeout=timeout_seconds)
            except FutureTimeoutError as exc:
                raise TimeoutError(
                    f"Extraction timed out after {timeout_seconds} seconds."
                ) from exc

        finally:
            # Do not block the request forever while shutting down.
            # This prevents the request from hanging if OCR is stuck.
            executor.shutdown(wait=False, cancel_futures=True)

    def _extract(self, upload: UploadContext, stored_file) -> tuple[ExtractionResult, str, str, dict]:
        suffix = ".pdf" if upload.file_type == "pdf" else ".docx"
        with tempfile.NamedTemporaryFile(suffix=suffix, prefix="soterra-upload-", delete=False) as tmp_file:
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

        return artifacts.extraction, artifacts.raw_text, artifacts.extractor_name, artifacts.metadata


class ReportUploadService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: RepositoryBackend,
        storage: StorageBackend,
        ingestion_service: ReportIngestionService,
        malware_scanner: MalwareScanner | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.ingestion_service = ingestion_service
        self.malware_scanner = malware_scanner or NoopMalwareScanner()

    async def upload_report(
        self,
        *,
        file: UploadFile,
        tenant_id: str,
        project: str,
        site: str,
        trade: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> JSONResponse:
        if not self.repository.consume_upload_rate_limit(
            tenant_id=tenant_id,
            limit=self.settings.upload_rate_limit_per_hour,
            window_seconds=60 * 60,
        ):
            logger.warning("upload_rejected tenant_id=%s reason=rate_limit", tenant_id)
            raise HTTPException(status_code=429, detail="Too many report uploads. Please try again later.")
        payload = await self.upload_one_report(
            file=file,
            tenant_id=tenant_id,
            project=project,
            site=site,
            trade=trade,
            background_tasks=background_tasks,
        )
        status_code = payload.pop("_status_code")
        payload.pop("_bytes", None)
        return JSONResponse(payload, status_code=status_code)

    async def upload_reports_bulk(
        self,
        *,
        files: list[UploadFile],
        tenant_id: str,
        project: str,
        site: str,
        trade: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> JSONResponse:
        if len(files) > self.settings.max_bulk_file_count:
            raise HTTPException(status_code=413, detail=f"You can upload up to {self.settings.max_bulk_file_count} files at once.")
        if not self.repository.consume_upload_rate_limit(
            tenant_id=tenant_id,
            limit=self.settings.upload_rate_limit_per_hour,
            window_seconds=60 * 60,
        ):
            raise HTTPException(status_code=429, detail="Too many report uploads. Please try again later.")

        total_bytes = 0
        results = []
        for file in files:
            filename = file.filename or "uploaded-report"
            try:
                payload = await self.upload_one_report(
                    file=file,
                    tenant_id=tenant_id,
                    project=project,
                    site=site,
                    trade=trade,
                    background_tasks=background_tasks,
                    consume_rate_limit=False,
                )
                total_bytes += int(payload.pop("_bytes", 0))
                if total_bytes > self.settings.max_bulk_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="Bulk upload is too large. Upload fewer files or split the upload.",
                    )
                payload.pop("_status_code", None)
                results.append({"filename": filename, "status": "accepted", **payload})
            except HTTPException as exc:
                results.append({"filename": filename, "status": "failed", "error": str(exc.detail)})

        accepted = sum(1 for item in results if item["status"] == "accepted")
        return JSONResponse(
            {
                "results": results,
                "summary": {"total": len(results), "accepted": accepted, "failed": len(results) - accepted},
            },
            status_code=207 if accepted and accepted < len(results) else 201 if accepted else 400,
        )

    async def upload_one_report(
        self,
        *,
        file: UploadFile,
        tenant_id: str,
        project: str,
        site: str,
        trade: str,
        background_tasks: BackgroundTasks | None = None,
        consume_rate_limit: bool = False,
    ) -> dict:
        if consume_rate_limit and not self.repository.consume_upload_rate_limit(
            tenant_id=tenant_id,
            limit=self.settings.upload_rate_limit_per_hour,
            window_seconds=60 * 60,
        ):
            raise HTTPException(status_code=429, detail="Too many report uploads. Please try again later.")

        content = await read_limited_upload(file, self.settings.max_upload_bytes)
        if not content:
            logger.warning("upload_rejected tenant_id=%s reason=empty_file", tenant_id)
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        original_filename = file.filename or "uploaded-report"
        file_type = detect_supported_type(original_filename, content)
        validate_readable_file(file_type, content)
        if file_type == "pdf" and _pdf_page_count(content) > self.settings.max_upload_pages:
            logger.warning("upload_rejected tenant_id=%s reason=page_limit", tenant_id)
            raise HTTPException(status_code=413, detail="Uploaded PDF has too many pages.")

        scan_result = await self.malware_scanner.scan_bytes(filename=original_filename, content=content)
        if not scan_result.clean:
            logger.warning(
                "upload_rejected tenant_id=%s reason=malware scanner=%s detail=%s",
                tenant_id,
                scan_result.scanner,
                scan_result.reason,
            )
            raise HTTPException(status_code=422, detail="This file failed security scanning and cannot be uploaded.")

        file_hash = _file_hash(content)
        if self.repository.get_report_by_file_hash(tenant_id, file_hash):
            logger.info("upload_rejected tenant_id=%s reason=duplicate file_hash=%s", tenant_id, file_hash[:12])
            raise HTTPException(status_code=409, detail="This file has already been uploaded.")

        stored_filename = resolve_filename_conflict(
            original_filename=original_filename,
            filename_exists=lambda candidate: self.repository.source_filename_exists(
                tenant_id=tenant_id,
                project_name=project,
                filename=candidate,
            ),
        )

        if len(self.repository.load_snapshot(tenant_id).documents) >= self.settings.max_reports_per_tenant:
            logger.warning("upload_rejected tenant_id=%s reason=tenant_quota", tenant_id)
            raise HTTPException(status_code=429, detail="Tenant report upload quota reached.")

        upload_ctx = UploadContext(
            tenant_id=tenant_id,
            filename=original_filename,
            stored_filename=stored_filename,
            content=content,
            content_type=file.content_type or "application/pdf",
            file_type=file_type,
            project_name=project,
            site_name=site,
            trade=trade or "General",
            address=None,
        )

        outcome, start = self.ingestion_service.start_ingest(upload_ctx)
        if outcome:
            raise HTTPException(status_code=409, detail="This file has already been uploaded.")

        assert start is not None
        if not self.settings.process_inline:
            if background_tasks is None:
                raise HTTPException(status_code=503, detail="Background processing is not available for this upload.")
            background_tasks.add_task(self.ingestion_service.finish_ingest_safely, start, upload_ctx)
            report = self.repository.get_report(tenant_id, start.document_id)
            if not report:
                raise RuntimeError("The report was queued but could not be loaded back from the repository.")
            return {"item": report, "isDuplicate": False, "isProcessing": True, "_status_code": 202, "_bytes": len(content)}

        try:
            report = self.ingestion_service.finish_ingest(start, upload_ctx)
        except ExtractionQualityError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "diagnostics": exc.diagnostics,
                    "reportId": start.document_id,
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Report extraction failed. The failed report row was kept for debugging.",
                    "reportId": start.document_id,
                },
            ) from exc
        return {"item": report, "isDuplicate": False, "isProcessing": False, "_status_code": 201, "_bytes": len(content)}

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
        self.storage.delete(
            tenant_id=tenant_id,
            document_id=report_id,
            filename=deleted["source_filename"],
        )
        return {"deleted": True, "id": report_id}

    def download_report(self, *, tenant_id: str, report_id: str) -> tuple[bytes, str]:
        report = self.repository.get_report(tenant_id, report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        filename = report["source_filename"]
        return self.storage.read(tenant_id=tenant_id, document_id=report_id, filename=filename), filename

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
            self.storage.delete(
                tenant_id=tenant_id,
                document_id=report_id,
                filename=deleted["source_filename"],
            )
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
            raise HTTPException(status_code=413, detail=f"File is too large. Maximum size is {_format_mb(max_bytes)} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _pdf_page_count(content: bytes) -> int:
    try:
        import fitz

        with fitz.open(stream=content, filetype="pdf") as document:
            return document.page_count
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Uploaded PDF could not be read.") from exc


def _format_mb(value: int) -> int:
    return max(1, value // (1024 * 1024))
