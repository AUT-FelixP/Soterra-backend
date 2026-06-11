from __future__ import annotations

import unittest
import zipfile
from io import BytesIO

from fastapi import HTTPException

from soterra_backend.services.upload_validation import (
    detect_supported_type,
    is_docx,
    resolve_filename_conflict,
    sanitize_filename,
)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _docx_bytes() -> bytes:
    return _zip_bytes(
        {
            "[Content_Types].xml": b"<Types></Types>",
            "word/document.xml": b"<w:document></w:document>",
        }
    )


class UploadValidationTest(unittest.TestCase):
    def test_docx_detection_requires_office_document_members(self) -> None:
        self.assertTrue(is_docx(_docx_bytes()))
        self.assertFalse(is_docx(_zip_bytes({"readme.txt": b"not a docx"})))
        self.assertEqual(detect_supported_type("report.docx", _docx_bytes()), "docx")

    def test_zip_uploads_get_specific_rejection_message(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            detect_supported_type("reports.zip", _zip_bytes({"report.pdf": b"%PDF-1.4"}))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.detail,
            "ZIP uploads are not supported. Please upload PDF or Word documents directly.",
        )

    def test_fake_pdf_extension_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            detect_supported_type("report.pdf", b"not really a pdf")
        self.assertEqual(raised.exception.detail, "Only PDF and Word documents are supported.")

    def test_filename_sanitizing_and_conflict_resolution(self) -> None:
        self.assertEqual(sanitize_filename("../../unsafe report?.pdf"), "unsafe report.pdf")
        existing = {"inspection-report.pdf", "inspection-report (1).pdf"}
        self.assertEqual(
            resolve_filename_conflict(
                original_filename="inspection-report.pdf",
                filename_exists=lambda value: value in existing,
            ),
            "inspection-report (2).pdf",
        )


if __name__ == "__main__":
    unittest.main()
