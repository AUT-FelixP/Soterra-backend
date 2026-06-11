from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Callable, Literal

from fastapi import HTTPException

SUPPORTED_UPLOAD_ERROR = "Only PDF and Word documents are supported."
ZIP_UPLOAD_ERROR = "ZIP uploads are not supported. Please upload PDF or Word documents directly."
MAX_FILENAME_LENGTH = 160


def detect_supported_type(filename: str, content: bytes) -> Literal["pdf", "docx"]:
    extension = Path(filename or "").suffix.lower()
    if extension == ".zip":
        raise HTTPException(status_code=400, detail=ZIP_UPLOAD_ERROR)
    if extension == ".pdf" and is_pdf(content):
        return "pdf"
    if extension == ".docx" and is_docx(content):
        return "docx"
    raise HTTPException(status_code=400, detail=SUPPORTED_UPLOAD_ERROR)


def is_pdf(content: bytes) -> bool:
    return content.startswith(b"%PDF")


def is_docx(content: bytes) -> bool:
    if not zipfile.is_zipfile(BytesIO(content)):
        return False
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            return "[Content_Types].xml" in names and "word/document.xml" in names
    except zipfile.BadZipFile:
        return False


def validate_readable_file(file_type: Literal["pdf", "docx"], content: bytes) -> None:
    if file_type == "pdf":
        if not _readable_pdf(content):
            raise HTTPException(status_code=400, detail="The PDF appears to be corrupted or unreadable.")
        return
    if not _readable_docx(content):
        raise HTTPException(status_code=400, detail="The Word document appears to be corrupted or unreadable.")


def resolve_filename_conflict(
    *,
    original_filename: str,
    filename_exists: Callable[[str], bool],
) -> str:
    safe_name = sanitize_filename(original_filename)
    if not filename_exists(safe_name):
        return safe_name

    path = Path(safe_name)
    stem = path.stem[: max(1, MAX_FILENAME_LENGTH - len(path.suffix) - 5)]
    suffix = path.suffix
    counter = 1
    while True:
        candidate = f"{stem} ({counter}){suffix}"
        if not filename_exists(candidate):
            return candidate
        counter += 1


def sanitize_filename(filename: str) -> str:
    raw_name = Path((filename or "uploaded-report").replace("\\", "/")).name
    suffix = Path(raw_name).suffix.lower()
    stem = raw_name[: -len(suffix)] if suffix else raw_name
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "-", stem).strip(" .-_")
    stem = re.sub(r"\s+", " ", stem)
    if not stem:
        stem = "uploaded-report"
    if suffix not in {".pdf", ".docx"}:
        suffix = ""
    max_stem = max(1, MAX_FILENAME_LENGTH - len(suffix))
    return f"{stem[:max_stem].rstrip()}{suffix}"


def _readable_pdf(content: bytes) -> bool:
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content), strict=False)
        return len(reader.pages) > 0
    except ModuleNotFoundError:
        try:
            import fitz

            with fitz.open(stream=content, filetype="pdf") as document:
                return document.page_count > 0
        except Exception:
            return False
    except Exception:
        return False


def _readable_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            document_xml = archive.read("word/document.xml")
            content_types_xml = archive.read("[Content_Types].xml")
            return bool(document_xml.strip()) and bool(content_types_xml.strip())
    except Exception:
        return False
