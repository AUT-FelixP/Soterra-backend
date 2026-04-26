from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .extractors import build_extractor
from .extractors.base import ExtractionRequest
from .models import ExtractionResult, IngestionOutcome
from .repository import RepositoryBackend
from .storage import StorageBackend
from .utils import create_id, summarize_status

logger = logging.getLogger("soterra_backend")


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


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
