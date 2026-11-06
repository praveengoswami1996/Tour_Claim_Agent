"""The bill reading queue for the chat: port of Tour_React src/lib/ocr/ocrApi.ts, ocrJobs.ts and ocrClaims.ts.

Runtime design §7. The old browser code, kept call for call and message for message:

- `OcrApi` (ocrApi.ts): the Tour API's Ocr/* endpoints with the old error messages. The calls are the old ones:
  GetMyDocuments with IncludeResponse=false and PageSize=200 (no ReferencePrefix), GetDocument/{id} for a full
  result, GetFile/{id}, Upload (parts `Files`, then `Reference`), MarkAsRead, Release, Cancel, Retry/{id} (a 409 is
  tried once more). A read-only (impersonated) login is refused before any write, as the browser's axios
  interceptor cancelled it ("This login is read-only, so bills cannot be sent.").
- `OcrSession` (ocrJobs.ts + ocrClaims.ts) for one chat: the bill list as last read (`refresh()`; the frontend's
  `poll` calls are the old 20 s / 60 s poller), ids hidden here until the server stops listing them, the bytes of
  files uploaded in this session (`local`), `batches_of`, `read_taken_documents`; and taking (MarkAsRead),
  settling, giving back (Release) and discarding (Cancel).

What the server does instead of the browser (design §7):
- The old module-level list and `localFiles` were per page load; here they are per chat session.
- Browser leases (`aryaan.ocrClaims.v1.*`, the tab heartbeat, `pagehide` keepalive release, `recoverOcrClaims`) are
  replaced by the session itself: results taken and not settled are recorded in the session (`ocr.claims`), and are
  given back when the chat is abandoned (Start over) or left idle (not touched for 15 minutes), see session.py.
- Taking runs one at a time per employee (the lock passed in).
- A downloaded file (GetFile) keeps its bytes next to the session (`dl-<id>`), so saving a draft does not fetch it
  twice; the old browser held it as a File object in memory.

Documents are plain dicts with the TypeScript field names (documentId, batchId, reference, fileName, index, status,
isRead, uploadedDate, processedDate, readDate, errorMessage, queuePosition, response).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

import httpx

from tourclaim_agent.claimchat.js_compat import (
    UNDEFINED,
    jget,
    js_int,
    js_is_finite,
    js_number,
    js_str,
    js_truthy,
    nullish,
)
from tourclaim_agent.claimchat.ocr_results import FileResult, uniquely_named
from tourclaim_agent.claimchat.runtime.session import ChatSession, WorkflowFiles
from tourclaim_agent.clients.errors import BackendUnavailable
from tourclaim_agent.clients.ocr_api import OcrApiClient

log = logging.getLogger(__name__)

# ocrJobs.ts: one file normally takes 1-2 minutes, so asking more often does not help (the frontend's poll interval)
POLL_MS: Final = 20_000
# ocrJobs.ts: after a failed check (offline, server down), the next one waits longer
RETRY_MS: Final = 60_000

READ_ONLY_MESSAGE: Final = "This login is read-only, so bills cannot be sent."
UNREACHABLE_MESSAGE: Final = "The server is not reachable right now. Please check your connection and try again."
SESSION_EXPIRED_MESSAGE: Final = "Your session has expired. Please sign in again."


def is_in_flight(d: Any) -> bool:
    """ocrApi.ts:isInFlight"""
    return jget(d, "status") in ("Pending", "Processing")


class OcrApiError(Exception):
    """ocrApi.ts:OcrApiError. One of the bill API's answers was a refusal; `invalid` lists the files an upload
    turned away, with the reason."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        invalid: list[dict[str, str]] | None = None,
        read_only: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.invalid = invalid or []
        self.read_only = read_only


class _Cancelled(Exception):
    """The browser's axios.Cancel('READ_ONLY_MODE'): a write from a read-only login, never sent."""


class _HttpError(Exception):
    """An answer with an error status (axios threw with `error.response`)."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(status)
        self.status = status
        self.body = body


def _to_api_error(error: BaseException, fallback: str) -> OcrApiError:
    """ocrApi.ts:toApiError"""
    if isinstance(error, _Cancelled):
        return OcrApiError(READ_ONLY_MESSAGE, read_only=True)
    if not isinstance(error, _HttpError):
        # no `error.response`: nothing came back (or the error was not an HTTP answer at all)
        return OcrApiError(UNREACHABLE_MESSAGE)
    body = nullish(error.body, {})
    detail = jget(body, "errorDetail")
    invalid = (
        [
            {"fileName": js_str(nullish(jget(x, "fileName"), "")), "reason": js_str(nullish(jget(x, "reason"), ""))}
            for x in detail
            if isinstance(x, dict)
        ]
        if isinstance(detail, list)
        else []
    )
    if error.status == 401:
        return OcrApiError(SESSION_EXPIRED_MESSAGE, status=401)
    if error.status == 403:
        return OcrApiError(READ_ONLY_MESSAGE, status=403, read_only=True)
    message = jget(body, "message")
    return OcrApiError(
        message if isinstance(message, str) and message else fallback, status=error.status, invalid=invalid
    )


def _unwrap(body: Any, fallback: str) -> Any:
    """ocrApi.ts:unwrap: the body's statusCode decides, not just the HTTP status (the API wraps every answer)."""
    code = jget(body, "statusCode")
    if isinstance(code, int | float) and not isinstance(code, bool) and code >= 400:
        message = jget(body, "message")
        raise OcrApiError(message if js_truthy(message) else fallback, status=int(code))
    return jget(body, "data")


def normalise(d: Any) -> dict[str, Any]:
    """ocrApi.ts:normalise. A document that is not an object fails as the old code did (a TypeError)."""
    if not isinstance(d, dict):
        raise TypeError("Cannot read properties of undefined (reading 'documentId')")
    index = js_number(jget(d, "index"))
    status = jget(d, "status")
    return {
        "documentId": js_int(js_number(jget(d, "documentId"))),
        "batchId": js_str(nullish(jget(d, "batchId"), "")),
        "reference": nullish(jget(d, "reference"), None),
        "fileName": js_str(nullish(jget(d, "fileName"), "")),
        "index": js_int(index) if js_is_finite(index) else 0,
        "status": None if status is UNDEFINED else status,
        "isRead": js_truthy(jget(d, "isRead")),
        "uploadedDate": nullish(jget(d, "uploadedDate"), None),
        "processedDate": nullish(jget(d, "processedDate"), None),
        "readDate": nullish(jget(d, "readDate"), None),
        "errorMessage": nullish(jget(d, "errorMessage"), None),
        "queuePosition": nullish(jget(d, "queuePosition"), None),
        "response": nullish(jget(d, "response"), None),
    }


def _doc_id(d: Any) -> int:
    value = jget(d, "documentId")
    return int(value) if isinstance(value, int | float) and js_is_finite(float(value)) else 0


_PDF = re.compile(r"\.pdf\Z", re.IGNORECASE)
_PNG = re.compile(r"\.png\Z", re.IGNORECASE)
_JPEG = re.compile(r"\.jpe?g\Z", re.IGNORECASE)


def type_from_name(name: str) -> str:
    """ocrApi.ts:typeFromName"""
    if _PDF.search(name):
        return "application/pdf"
    if _PNG.search(name):
        return "image/png"
    if _JPEG.search(name):
        return "image/jpeg"
    return ""


@dataclass(frozen=True)
class OcrFile:
    """A file as GetFile returned it (the old `new File([blob], doc.fileName, {type})`)."""

    name: str
    type: str
    data: bytes


class OcrApi:
    """ocrApi.ts: the Tour API endpoints for bill OCR, called with the employee's own token."""

    def __init__(self, client: OcrApiClient, *, read_only: bool = False) -> None:
        self._c = client.client
        self._read_only = read_only

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        files: list[Any] | None = None,
    ) -> httpx.Response:
        if self._read_only and method != "GET":
            raise _Cancelled()
        try:
            response = await self._c.send_raw(method, path, params=params, json=json, files=files)
        except BackendUnavailable as exc:
            raise _NoAnswer() from exc
        if response.status_code >= 400:
            raise _HttpError(response.status_code, _body(response))
        return response

    async def upload_ocr_files(
        self, files: list[tuple[str, bytes, str]], reference: str | None = None
    ) -> dict[str, Any]:
        """ocrApi.ts:uploadOcrFiles. All or nothing: one bad file and none is queued.

        `files` are (name, bytes, type); returns {batchId, documents}.
        """
        fallback = "The bills could not be sent."
        parts: list[Any] = [("Files", (name, data, type_ or "application/octet-stream")) for name, data, type_ in files]
        if reference:
            parts.append(("Reference", (None, reference)))
        try:
            response = await self._call("POST", "Ocr/Upload", files=parts)
            result = _unwrap(_body(response), fallback)
            if result is UNDEFINED or result is None:
                # `result.batchId` on nothing: the TypeError became the "not reachable" message
                raise TypeError("Cannot read properties of undefined (reading 'batchId')")
            batch_id = jget(result, "batchId")
            raw_documents = nullish(jget(result, "documents"), [])
            documents = [
                normalise(
                    {
                        **d,
                        "batchId": nullish(jget(d, "batchId"), batch_id),
                        "reference": nullish(jget(d, "reference"), nullish(reference, None)),
                        "index": nullish(jget(d, "index"), i),
                        "isRead": False,
                    }
                    if isinstance(d, dict)
                    else d
                )
                for i, d in enumerate(raw_documents if isinstance(raw_documents, list) else [])
            ]
            return {"batchId": None if batch_id is UNDEFINED else batch_id, "documents": documents}
        except OcrApiError:
            raise
        except Exception as error:  # noqa: BLE001 - as the old catch-all
            raise _to_api_error(error, fallback) from error

    async def get_my_ocr_documents(
        self,
        *,
        include_response: bool = False,
        reference_prefix: str | None = None,
        batch_id: str | None = None,
        page_size: int = 200,
    ) -> dict[str, Any]:
        """ocrApi.ts:getMyOcrDocuments: {summary, documents, totalRecords}."""
        fallback = "The bill list could not be read."
        params: dict[str, Any] = {"IncludeResponse": "true" if include_response else "false"}
        if reference_prefix is not None:
            params["ReferencePrefix"] = reference_prefix
        if batch_id is not None:
            params["BatchId"] = batch_id
        params["PageSize"] = page_size
        try:
            response = await self._call("GET", "Ocr/GetMyDocuments", params=params)
            data = _body(response)
            result = _unwrap(data, fallback)
            documents = nullish(jget(result, "documents"), [])
            summary = nullish(jget(result, "summary"), {"pending": 0, "completed": 0, "failed": 0, "total": 0})
            total = nullish(jget(data, "totalRecords"), len(documents) if isinstance(documents, list) else UNDEFINED)
            return {
                "summary": summary,
                "documents": [normalise(d) for d in (documents if isinstance(documents, list) else [])],
                "totalRecords": js_int(js_number(nullish(total, 0))),
            }
        except OcrApiError:
            raise
        except Exception as error:  # noqa: BLE001
            raise _to_api_error(error, fallback) from error

    async def get_ocr_document(self, document_id: int) -> dict[str, Any]:
        """ocrApi.ts:getOcrDocument: the full result (with `response`)."""
        fallback = "The bill could not be read."
        try:
            response = await self._call("GET", f"Ocr/GetDocument/{document_id}")
            return normalise(_unwrap(_body(response), fallback))
        except OcrApiError:
            raise
        except Exception as error:  # noqa: BLE001
            raise _to_api_error(error, fallback) from error

    async def get_ocr_file(self, doc: dict[str, Any]) -> OcrFile:
        """ocrApi.ts:getOcrFile: the file as it was uploaded."""
        try:
            response = await self._call("GET", f"Ocr/GetFile/{_doc_id(doc)}")
            name = js_str(jget(doc, "fileName"))
            return OcrFile(name, response.headers.get("content-type") or type_from_name(name), response.content)
        except Exception as error:  # noqa: BLE001 - every failure is turned into the API error, as before
            raise _to_api_error(error, "The bill file is no longer available.") from error

    async def mark_ocr_read(self, document_ids: list[int]) -> dict[str, list[int]]:
        """ocrApi.ts:markOcrRead: exactly one caller gets each id in `marked`, and only it applies that result."""
        if not document_ids:
            return {"marked": [], "alreadyRead": [], "stillPending": [], "notFound": []}
        fallback = "The results could not be taken."
        try:
            response = await self._call("POST", "Ocr/MarkAsRead", json={"documentIds": document_ids})
            r = _unwrap(_body(response), fallback)
            return {k: _ids(nullish(jget(r, k), [])) for k in ("marked", "alreadyRead", "stillPending", "notFound")}
        except OcrApiError:
            raise
        except Exception as error:  # noqa: BLE001
            raise _to_api_error(error, fallback) from error

    async def release_ocr(self, document_ids: list[int]) -> None:
        """ocrApi.ts:releaseOcr: gives results back (unread again)."""
        await self._post_ids("Ocr/Release", document_ids, "The results could not be given back.")

    async def cancel_ocr(self, document_ids: list[int]) -> None:
        """ocrApi.ts:cancelOcr: "stop reading" or "discard"."""
        await self._post_ids("Ocr/Cancel", document_ids, "The reading could not be stopped.")

    async def _post_ids(self, path: str, document_ids: list[int], fallback: str) -> None:
        if not document_ids:
            return
        try:
            response = await self._call("POST", path, json={"documentIds": document_ids})
            _unwrap(_body(response), fallback)
        except OcrApiError:
            raise
        except Exception as error:  # noqa: BLE001
            raise _to_api_error(error, fallback) from error

    async def retry_ocr(self, document_id: int) -> None:
        """ocrApi.ts:retryOcr: reads a Failed file again. A 409 is tried once more."""
        fallback = "The bill could not be read again."

        async def once() -> None:
            response = await self._call("POST", f"Ocr/Retry/{document_id}")
            _unwrap(_body(response), fallback)

        try:
            await once()
        except Exception as error:  # noqa: BLE001
            if isinstance(error, _HttpError) and error.status == 409:
                try:
                    await once()
                    return
                except Exception as second:  # noqa: BLE001
                    raise _to_api_error(second, fallback) from second
            raise (error if isinstance(error, OcrApiError) else _to_api_error(error, fallback)) from error


class _NoAnswer(Exception):
    """No answer came back (axios error without `response`)."""


def _body(response: httpx.Response) -> Any:
    """axios's `response.data`: the parsed JSON, or the text when it is not JSON."""
    try:
        return response.json()
    except ValueError:
        return response.text


def _ids(v: Any) -> list[int]:
    return [int(i) for i in v if isinstance(i, int | float) and not isinstance(i, bool)] if isinstance(v, list) else []


# ── the chat's view of the queue (ocrJobs.ts + ocrClaims.ts) ──


def batches_of(documents: list[dict[str, Any]], local: Iterable[int] = ()) -> list[dict[str, Any]]:
    """ocrJobs.ts:batchesOf: documents grouped by upload, oldest upload first.

    Each batch: {batchId, reference, documents (upload order), done (none in flight), local (every file's bytes
    were uploaded in this session)}. `local` = the ids uploaded in this session.
    """
    local_ids = set(local)
    by_batch: dict[str, list[dict[str, Any]]] = {}
    for d in documents:
        key = d.get("batchId") or f"doc-{js_str(d.get('documentId'))}"
        by_batch.setdefault(key, []).append(d)
    batches = []
    for batch_id, docs in by_batch.items():
        ordered = sorted(docs, key=lambda d: js_number(d.get("index")))
        batches.append(
            {
                "batchId": batch_id,
                "reference": nullish(jget(ordered[0], "reference"), None) if ordered else None,
                "documents": ordered,
                "done": all(not is_in_flight(d) for d in ordered),
                "local": all(_doc_id(d) in local_ids for d in ordered),
            }
        )
    return sorted(batches, key=lambda b: min(js_number(d.get("documentId")) for d in b["documents"]))


def queue_ahead(documents: list[dict[str, Any]]) -> int | float | None:
    """ocrJobs.ts:queueAhead: "about N files ahead of yours" for the files still waiting, or None."""
    waiting = [
        d
        for d in documents
        if d.get("status") == "Pending"
        and isinstance(d.get("queuePosition"), int | float)
        and not isinstance(d.get("queuePosition"), bool)
    ]
    if not waiting:
        return None
    return max(0, min(d["queuePosition"] for d in waiting))


def local_key(document_id: int) -> str:
    """Where the bytes of a file uploaded in this session are kept (the old in-memory localFiles)."""
    return f"ocr-{document_id}"


def downloaded_key(document_id: int) -> str:
    return f"dl-{document_id}"


class OcrSession:
    """One chat's bill list, its files and the results it took (ocrJobs.ts + ocrClaims.ts), kept in the session."""

    def __init__(self, api: OcrApi, session: ChatSession, files: WorkflowFiles, take_lock: asyncio.Lock) -> None:
        self.api = api
        self._session = session
        self._files = files
        self._take_lock = take_lock

    @property
    def _s(self) -> dict[str, Any]:
        return self._session.ocr

    # ── the list (useOcrDocuments snapshot) ──
    @property
    def documents(self) -> list[dict[str, Any]]:
        """The employee's bill reads as the server last listed them, without the ids hidden here."""
        return self._s["documents"]

    @property
    def loaded(self) -> bool:
        """The first answer has arrived (until then "nothing pending" is not known)."""
        return bool(self._s.get("loaded"))

    @property
    def error(self) -> str | None:
        return self._s.get("error")

    @property
    def local_ids(self) -> list[int]:
        """Documents uploaded in this session (their bytes are kept here)."""
        return list(self._s.get("local") or [])

    async def refresh(self) -> None:
        """ocrJobs.ts:refreshOcrDocuments: asks the server now."""
        try:
            result = await self.api.get_my_ocr_documents(include_response=False)
            listed = {_doc_id(d) for d in result["documents"]}
            hidden = [i for i in self._s["hidden"] if i in listed]
            self._s["hidden"] = hidden
            self._s["documents"] = [d for d in result["documents"] if _doc_id(d) not in set(hidden)]
            self._s["loaded"] = True
            self._s["error"] = None
        except Exception as error:  # noqa: BLE001 - as the old catch: the error is kept, the list stays
            self._s["loaded"] = True
            self._s["error"] = getattr(error, "message", None) or "The bill list could not be read."

    def hide(self, ids: Iterable[int]) -> None:
        """ocrJobs.ts:hideOcrDocuments: takes documents off the list now (taken or cancelled here)."""
        ids = list(ids)
        if not ids:
            return
        hidden = list(self._s["hidden"])
        hidden += [i for i in ids if i not in hidden]
        self._s["hidden"] = hidden
        self._s["documents"] = [d for d in self._s["documents"] if _doc_id(d) not in set(hidden)]

    async def unhide(self, ids: Iterable[int]) -> None:
        """ocrJobs.ts:unhideOcrDocuments: lets documents back on the list (given back unread), and checks again."""
        drop = set(ids)
        self._s["hidden"] = [i for i in self._s["hidden"] if i not in drop]
        await self.refresh()

    # ── files uploaded in this session ──
    def is_local(self, document_id: int) -> bool:
        return document_id in (self._s.get("local") or [])

    async def local_file(self, document_id: int) -> dict[str, Any] | None:
        """ocrJobs.ts:localOcrFile: the file as uploaded in this session (a file dict), or None."""
        if not self.is_local(document_id):
            return None
        stored = await self._files.get(local_key(document_id))
        if stored is None:
            return None
        return {"name": stored.name, "size": stored.size, "type": stored.type, "documentId": document_id}

    async def forget_local_files(self, ids: Iterable[int]) -> None:
        """ocrJobs.ts:forgetLocalOcrFiles (and the downloaded copies)."""
        ids = list(ids)
        if not ids:
            return
        self._s["local"] = [i for i in (self._s.get("local") or []) if i not in set(ids)]
        await self._files.delete(*[local_key(i) for i in ids], *[downloaded_key(i) for i in ids])

    async def upload_for_ocr(self, files: list[tuple[str, bytes, str]], reference: str) -> list[dict[str, Any]]:
        """ocrJobs.ts:uploadForOcr: sends the files and puts them on the list as "being read" straight away.

        `files` are (name, bytes, type) in the order they are sent. Raises OcrApiError.
        """
        result = await self.api.upload_ocr_files(files, reference)
        documents = result["documents"]
        local = list(self._s.get("local") or [])
        for d in documents:
            index = d["index"]
            if isinstance(index, int | float) and 0 <= index < len(files) and index == int(index):
                name, data, type_ = files[int(index)]
                await self._files.put(local_key(_doc_id(d)), name, type_, data)
                if _doc_id(d) not in local:
                    local.append(_doc_id(d))
        self._s["local"] = local
        known = {_doc_id(d) for d in self._s["documents"]}
        self._s["documents"] = [d for d in documents if _doc_id(d) not in known] + self._s["documents"]
        return documents

    def batches(self, documents: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """batchesOf(documents) with this session's local files."""
        return batches_of(self.documents if documents is None else documents, self.local_ids)

    async def read_taken_documents(
        self, docs: list[dict[str, Any]], *, with_files: bool, taken_names: Iterable[str] = ()
    ) -> list[dict[str, Any]]:
        """ocrJobs.ts:readTakenDocuments: the full result of each taken document, in upload order, and its file.

        Returns [{doc, file (file dict {name, size, type, documentId}) or None, result: FileResult}]. Names are made
        unique among the files and `taken_names`. Raises OcrApiError if a result cannot be read (the caller gives
        the batch back).
        """
        batch_order: dict[Any, int] = {}
        for d in docs:
            batch_order.setdefault(d.get("batchId"), len(batch_order))
        ordered = sorted(docs, key=lambda d: (batch_order[d.get("batchId")], js_number(d.get("index"))))
        full = await asyncio.gather(
            *(self.api.get_ocr_document(_doc_id(d)) if d.get("status") == "Completed" else _value(d) for d in ordered)
        )

        async def file_of(d: dict[str, Any]) -> dict[str, Any] | None:
            local = await self.local_file(_doc_id(d))
            if local is not None:
                return local
            if not with_files:
                return None
            try:
                got = await self.api.get_ocr_file(d)
            except OcrApiError:
                return None
            await self._files.put(downloaded_key(_doc_id(d)), got.name, got.type, got.data)
            return {"name": got.name, "size": len(got.data), "type": got.type, "documentId": _doc_id(d)}

        files = await asyncio.gather(*(file_of(d) for d in ordered))
        # unique names for all of them, kept files or not (a missing file still names its rows in what is said)
        named = uniquely_named(
            [
                f if f is not None else {"name": js_str(d.get("fileName")), "size": 0, "type": ""}
                for d, f in zip(ordered, files, strict=True)
            ],
            taken_names,
        )
        taken: list[dict[str, Any]] = []
        for i in range(len(ordered)):
            result: FileResult = {
                "fileName": named[i]["name"],
                "failed": full[i].get("status") != "Completed",
                "response": nullish(full[i].get("response"), None),
            }
            taken.append({"doc": full[i], "file": named[i] if files[i] is not None else None, "result": result})
        return taken

    async def file_bytes(self, file: dict[str, Any]) -> tuple[bytes, str]:
        """The bytes of a bill file in the claim (a file dict with `documentId`): uploaded here, downloaded, or
        fetched again with GetFile. Raises OcrApiError when the server no longer has it."""
        document_id = int(js_number(file.get("documentId")))
        for key in (local_key(document_id), downloaded_key(document_id)):
            stored = await self._files.get(key)
            if stored is not None:
                return stored.data, file.get("type") or stored.type
        got = await self.api.get_ocr_file({"documentId": document_id, "fileName": file.get("name") or ""})
        return got.data, file.get("type") or got.type

    # ── taking, settling, giving back (ocrClaims.ts) ──
    @property
    def held(self) -> list[int]:
        """Results taken by this chat and not settled or given back."""
        return [int(i) for i in self._s["claims"]]

    def _drop_claims(self, ids: Iterable[int]) -> None:
        for i in ids:
            self._s["claims"].pop(str(i), None)

    async def take_ocr_results(self, ids: list[int]) -> list[int]:
        """ocrClaims.ts:takeOcrResults: MarkAsRead; resolves to the ids this chat got (only those are applied here).

        Raises OcrApiError.
        """
        async with self._take_lock:
            r = await self.api.mark_ocr_read(ids)
            now = int(time.time() * 1000)
            for i in r["marked"]:
                self._s["claims"][str(i)] = now
        # taken here or elsewhere: off the list either way (one still being read stays on it)
        self.hide([*r["marked"], *r["alreadyRead"], *r["notFound"]])
        return r["marked"]

    async def settle_ocr_results(self, ids: Iterable[int]) -> None:
        """ocrClaims.ts:settleOcrResults: applied (in a saved claim, or handed to the form): nothing to give back."""
        ids = list(ids)
        self._drop_claims(ids)
        await self.forget_local_files(ids)

    async def give_back_ocr_results(self, ids: Iterable[int]) -> None:
        """ocrClaims.ts:giveBackOcrResults: not applied: unread again, so they come back on the next look.

        A failed Release keeps them as taken: they are given back when the chat is closed or left idle.
        """
        ids = list(ids)
        if not ids:
            return
        try:
            await self.api.release_ocr(ids)
        except Exception:  # noqa: BLE001 - kept as taken
            log.info("could not give back %d OCR result(s) now", len(ids))
            return
        self._drop_claims(ids)
        await self.unhide(ids)

    async def discard_ocr(self, ids: Iterable[int]) -> None:
        """ocrClaims.ts:discardOcr: never to be shown again ("stop reading", "discard"). Raises OcrApiError."""
        ids = list(ids)
        if not ids:
            return
        await self.api.cancel_ocr(ids)
        self._drop_claims(ids)
        await self.forget_local_files(ids)
        self.hide(ids)

    async def give_back_all(self) -> bool:
        """Every result the chat still holds is given back (Start over, or the chat was left idle).

        They are let back on the list, so a chat that carries on takes them again (MarkAsRead) when it needs them.
        Returns whether nothing is held any more.
        """
        held = self.held
        if held:
            try:
                await self.api.release_ocr(held)
            except Exception:  # noqa: BLE001
                log.info("could not give back %d OCR result(s) of a chat", len(held))
                return False
            self._drop_claims(held)
            self._s["hidden"] = [i for i in self._s["hidden"] if i not in set(held)]
        return True


async def _value(v: Any) -> Any:
    return v
