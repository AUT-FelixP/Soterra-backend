from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from ..config import Settings
from ..models import StoredFile
from .base import StorageBackend


class LocalFileStorage:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def store(
        self,
        *,
        tenant_id: str,
        document_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> StoredFile:
        _ = content_type
        document_dir, destination = self._paths(tenant_id=tenant_id, document_id=document_id, filename=filename)
        document_dir.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        resolved = str(destination)
        return StoredFile(storage_path=resolved, download_url=resolved)

    def delete(self, *, tenant_id: str, document_id: str, filename: str) -> None:
        document_dir, target = self._paths(tenant_id=tenant_id, document_id=document_id, filename=filename)
        if target.exists() and target.is_file():
            target.unlink()
        if document_dir.exists() and document_dir.is_dir():
            try:
                document_dir.rmdir()
            except OSError:
                return

    def read(self, *, tenant_id: str, document_id: str, filename: str) -> bytes:
        _, target = self._paths(tenant_id=tenant_id, document_id=document_id, filename=filename)
        return target.read_bytes()

    def _paths(self, *, tenant_id: str, document_id: str, filename: str) -> tuple[Path, Path]:
        safe_tenant_id = _safe_storage_component(tenant_id, "tenant_id")
        safe_document_id = _safe_storage_component(document_id, "document_id")
        safe_name = _safe_storage_filename(filename)
        root = self.root_dir.resolve()
        document_dir = (root / safe_tenant_id / safe_document_id).resolve()
        destination = (document_dir / safe_name).resolve()
        if root not in document_dir.parents or document_dir not in destination.parents:
            raise RuntimeError("Resolved storage path escaped the storage directory.")
        return document_dir, destination


class SupabaseFileStorage:
    def __init__(self, *, url: str, service_role_key: str, bucket: str) -> None:
        try:
            from supabase import create_client
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "The supabase package is not installed. Run the Python dependency install step first."
            ) from exc

        self.client = create_client(url, service_role_key)
        self.bucket = bucket

    def store(
        self,
        *,
        tenant_id: str,
        document_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> StoredFile:
        path = _supabase_storage_path(tenant_id=tenant_id, document_id=document_id, filename=filename)

        try:
            self.client.storage.from_(self.bucket).upload(
                path=path,
                file=content,
                file_options={"content-type": content_type, "upsert": "true"},
            )
        except Exception as exc:
            raise RuntimeError(
                f"Supabase storage upload failed for bucket '{self.bucket}'. "
                "Make sure the bucket exists and the secret key has storage access."
            ) from exc

        signed = None
        try:
            signed = self.client.storage.from_(self.bucket).create_signed_url(path, 3600)
        except Exception:
            signed = None

        signed_url = None
        if isinstance(signed, dict):
            signed_url = signed.get("signedURL") or signed.get("signedUrl")
        return StoredFile(storage_path=path, download_url=signed_url)

    def delete(self, *, tenant_id: str, document_id: str, filename: str) -> None:
        path = _supabase_storage_path(tenant_id=tenant_id, document_id=document_id, filename=filename)
        self.client.storage.from_(self.bucket).remove([path])

    def read(self, *, tenant_id: str, document_id: str, filename: str) -> bytes:
        path = _supabase_storage_path(tenant_id=tenant_id, document_id=document_id, filename=filename)
        return self.client.storage.from_(self.bucket).download(path)


def build_storage(settings: Settings) -> StorageBackend:
    if settings.storage_mode == "supabase":
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required when SOTERRA_STORAGE_MODE=supabase."
            )
        return SupabaseFileStorage(
            url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_bucket,
        )

    return LocalFileStorage(settings.local_storage_dir)


def _safe_storage_filename(filename: str) -> str:
    cleaned = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    cleaned = cleaned.replace("\\", "-").replace("/", "-")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", cleaned).strip(".-")
    return cleaned or "uploaded-report.pdf"


def _safe_storage_component(value: str, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or not re.fullmatch(r"[A-Za-z0-9_-]+", cleaned):
        raise ValueError(f"Invalid {field_name} for storage path.")
    return cleaned


def _supabase_storage_path(*, tenant_id: str, document_id: str, filename: str) -> str:
    safe_tenant_id = _safe_storage_component(tenant_id, "tenant_id")
    safe_document_id = _safe_storage_component(document_id, "document_id")
    return f"{safe_tenant_id}/{safe_document_id}/{_safe_storage_filename(filename)}"
