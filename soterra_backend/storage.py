from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .config import Settings
from .models import StoredFile


class StorageBackend(Protocol):
    def store(self, *, document_id: str, filename: str, content: bytes, content_type: str) -> StoredFile:
        ...


class LocalFileStorage:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def store(self, *, document_id: str, filename: str, content: bytes, content_type: str) -> StoredFile:
        _ = content_type
        destination = self.root_dir / document_id / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        resolved = str(destination.resolve())
        return StoredFile(storage_path=resolved, download_url=resolved)


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

    def store(self, *, document_id: str, filename: str, content: bytes, content_type: str) -> StoredFile:
        path = f"{document_id}/{filename}"
        self.client.storage.from_(self.bucket).upload(
            path,
            content,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        signed = self.client.storage.from_(self.bucket).create_signed_url(path, 3600)
        signed_url = None
        if isinstance(signed, dict):
            signed_url = signed.get("signedURL") or signed.get("signedUrl")
        return StoredFile(storage_path=path, download_url=signed_url)


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

