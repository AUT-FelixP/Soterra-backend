from __future__ import annotations

from pathlib import Path

from .base import ExtractionArtifacts, ExtractionRequest
from .package_doctr import DoctrRulesPresidioExtractor as _PackageDoctrRulesPresidioExtractor


class DoctrRulesPresidioExtractor(_PackageDoctrRulesPresidioExtractor):
    def extract(
        self,
        request: ExtractionRequest | bytes,
        pdf_path: Path | None = None,
        *,
        filename: str = "uploaded-report.pdf",
    ) -> ExtractionArtifacts:
        if not isinstance(request, bytes):
            if pdf_path is None:
                raise TypeError("pdf_path is required when extracting from an ExtractionRequest.")
            return super().extract(request, pdf_path)

        file_bytes = request
        request = ExtractionRequest(
            filename=filename,
            content=file_bytes,
            project_name="Uploaded report",
            site_name="Uploaded report",
            trade="General",
        )
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", prefix="soterra-upload-", delete=False) as tmp_file:
            tmp_file.write(file_bytes)
            temp_pdf_path = Path(tmp_file.name)

        try:
            return super().extract(request, temp_pdf_path)
        finally:
            temp_pdf_path.unlink(missing_ok=True)
