from __future__ import annotations

from typing import Protocol

from ..models import StoredFile


class StorageBackend(Protocol):
    def store(
        self,
        *,
        tenant_id: str,
        document_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> StoredFile:
        ...

    def delete(self, *, tenant_id: str, document_id: str, filename: str) -> None:
        ...

    def read(self, *, tenant_id: str, document_id: str, filename: str) -> bytes:
        ...
