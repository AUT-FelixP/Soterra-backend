from __future__ import annotations

from typing import Protocol

from ..models import StoredFile


class StorageBackend(Protocol):
    def store(self, *, document_id: str, filename: str, content: bytes, content_type: str) -> StoredFile:
        ...

    def delete(self, *, storage_path: str) -> None:
        ...
