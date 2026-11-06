"""Client for the Tour API's OCR queue (api/Ocr/*), used with the employee's own token.

The backend isolates documents per employee. Results are claimed with MarkAsRead so that
the agent and the web app never apply the same result twice (FRS DOC-05).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from tourclaim_agent.clients.http import FilePart, UserScopedClient
from tourclaim_agent.domain.base import BackendModel


class OcrDocument(BackendModel):
    document_id: int = Field(alias="documentId")
    batch_id: str | None = Field(default=None, alias="batchId")
    index: int | None = None  # position of the file in its upload
    reference: str | None = None
    file_name: str | None = Field(default=None, alias="fileName")
    status: str = "Pending"
    is_read: bool = Field(default=False, alias="isRead")
    error_message: str | None = Field(default=None, alias="errorMessage")
    response: Any = None


class OcrApiClient:
    def __init__(self, client: UserScopedClient) -> None:
        self._c = client

    @property
    def client(self) -> UserScopedClient:
        """The user's allow-listed client (the Claim Assistant port makes the old calls on it as they were)."""
        return self._c

    async def upload(self, files: list[tuple[str, bytes, str]], reference: str) -> list[OcrDocument]:
        """POST Ocr/Upload. All-or-nothing: the backend rejects the batch if any file is invalid."""
        parts: list[FilePart] = [("Files", f) for f in files]
        envelope = await self._c.post_envelope("Ocr/Upload", data={"Reference": reference}, files=parts)
        documents = (envelope.data or {}).get("documents", [])
        return [OcrDocument.model_validate(d) for d in documents]

    async def list_documents(self, reference_prefix: str, *, include_response: bool) -> list[OcrDocument]:
        envelope = await self._c.get_envelope(
            "Ocr/GetMyDocuments",
            params={
                "ReferencePrefix": reference_prefix,
                "IncludeRead": "true",
                "IncludeResponse": "true" if include_response else "false",
                "PageSize": 200,
            },
            not_found_ok=True,
        )
        if envelope is None or not envelope.data:
            return []
        return [OcrDocument.model_validate(d) for d in envelope.data.get("documents", [])]

    async def mark_as_read(self, document_ids: list[int]) -> set[int]:
        """Claim results. Returns only the IDs this caller won; act on those alone."""
        if not document_ids:
            return set()
        envelope = await self._c.post_envelope("Ocr/MarkAsRead", json={"documentIds": document_ids})
        return {int(i) for i in (envelope.data or {}).get("marked", [])}

    async def release(self, document_ids: list[int]) -> None:
        """Hand results back unread, so the web app can still use them."""
        if document_ids:
            await self._c.post_envelope("Ocr/Release", json={"documentIds": document_ids})

    async def cancel(self, document_ids: list[int]) -> None:
        """Withdraw documents the employee no longer needs (frees the shared OCR queue)."""
        if document_ids:
            await self._c.post_envelope("Ocr/Cancel", json={"documentIds": document_ids})

    async def retry(self, document_id: int) -> None:
        """Put a failed document back in the queue to be read again (BIL-15)."""
        await self._c.post_envelope(f"Ocr/Retry/{document_id}")

    async def get_file(self, document_id: int) -> tuple[bytes, str]:
        return await self._c.get_bytes(f"Ocr/GetFile/{document_id}")
