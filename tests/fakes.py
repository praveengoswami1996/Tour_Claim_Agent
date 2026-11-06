"""A fake Tour API + Organisation service for tests. All data is synthetic."""

from __future__ import annotations

import email
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx
import jwt

TOUR_BASE = "https://tour.test/api"
ORG_BASE = "https://org.test/api"
# the ARYAAN AI gateway of the old Claim Assistant (its /chat/stream)
GATEWAY_BASE = "https://gw.test/chat-api"


def make_token(emp_code: str = "E1001", *, expires_in: int = 3600, **extra: Any) -> str:
    claims = {"UserId": emp_code, "iss": "https://sso.test", "exp": int(time.time()) + expires_in, **extra}
    return jwt.encode(claims, "test-secret-not-used-for-verification", algorithm="HS256")


def tour(
    tour_id: int, emp_id: int, *, kind_dest: str, status_id: int, status: str, start: str, days: int = 3, **extra: Any
):
    end = (date.fromisoformat(start) + timedelta(days=days - 1)).isoformat()
    return {
        "tourId": tour_id,
        "empId": emp_id,
        "tourType": "TourRequestClaim",
        "purposeofTour": f"Site inspection {tour_id}",
        "source": "Noida",
        "destination": kind_dest,
        "tourStartDate": f"{start}T00:00:00",
        "tantetiveEndDate": f"{end}T00:00:00",
        "totalDays": days,
        "status": status,
        "statusId": status_id,
        "recipientId": 9001,
        "recipientEmpName": "Approver One",
        "recipientDesignation": "Manager",
        "sendBackToRemark": "",
        **extra,
    }


@dataclass
class FakeBackend:
    employees: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "E1001": {"empId": 1001, "empCode": "E1001", "empName": "Test Employee", "designation": "Engineer"},
            "E2002": {"empId": 2002, "empCode": "E2002", "empName": "Other Employee", "designation": "Officer"},
        }
    )
    outside_tours: dict[int, list[dict[str, Any]]] = field(
        default_factory=lambda: {
            1001: [
                tour(501, 1001, kind_dest="Mumbai", status_id=3, status="Finance Processing", start="2026-10-05"),
                tour(
                    502, 1001, kind_dest="Kolkata", status_id=11, status="Clarification Processing", start="2026-09-12"
                ),
                tour(503, 1001, kind_dest="Mumbai", status_id=2, status="Tour Approved", start="2026-08-20"),
            ],
            2002: [
                tour(777, 2002, kind_dest="Chennai", status_id=1, status="Pending for Approval", start="2026-10-01")
            ],
        }
    )
    within_tours: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    fail_with: int | None = None
    org_override: dict[str, Any] | None = None
    # Claim drafting
    prefills: dict[int, dict[str, Any]] = field(default_factory=lambda: {503: MUMBAI_PREFILL})
    drafts: dict[int, dict[str, Any]] = field(default_factory=dict)
    ocr_docs: dict[int, dict[str, Any]] = field(default_factory=dict)
    saved_requests: list[dict[str, Any]] = field(default_factory=list)
    released: list[int] = field(default_factory=list)
    cancelled: list[int] = field(default_factory=list)
    taken_elsewhere: set[int] = field(default_factory=set)
    retried: list[int] = field(default_factory=list)
    # Failure switches: endpoint suffix -> HTTP statuses returned by the next calls, one per call.
    failures: dict[str, list[int]] = field(default_factory=dict)
    next_draft_id: int = 9001
    next_file_id: int = 100
    next_batch: int = 1
    # TourPlan/GetTourDetails/{tourId} bodies' `data` per tour (none: 404)
    tour_plans: dict[int, dict[str, Any]] = field(default_factory=dict)
    # The AI gateway: replies to give (in order; when empty, gateway_reply() works one out), calls seen, failures.
    gateway_replies: list[str] = field(default_factory=list)
    gateway_calls: list[dict[str, Any]] = field(default_factory=list)
    gateway_fail: list[int] = field(default_factory=list)
    upload_parts: list[list[str]] = field(default_factory=list)
    list_params: list[dict[str, str]] = field(default_factory=list)

    def fail_next(self, endpoint: str, *statuses: int) -> None:
        self.failures.setdefault(endpoint, []).extend(statuses)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def finish_ocr(self, file_name: str, response: dict[str, Any] | None, *, failed: bool = False) -> None:
        """Simulate the OCR worker finishing a document."""
        doc = next(d for d in self.ocr_docs.values() if d["fileName"] == file_name)
        doc["status"] = "Failed" if failed else "Completed"
        doc["response"] = response
        doc["errorMessage"] = "could not read" if failed else None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(GATEWAY_BASE):
            return self._gateway(request)
        response = self._claim_routes(request)
        if response is not None:
            self.calls.append(f"{request.method} {request.url.path}")
            return response
        return self._read_routes(request)

    def _claim_routes(self, request: httpx.Request) -> httpx.Response | None:
        path = request.url.path
        if self.fail_with or not request.url.path.startswith("/api/"):
            return None
        for endpoint, statuses in self.failures.items():
            if path.endswith(endpoint) and statuses:
                return httpx.Response(statuses.pop(0), json={"message": "failure"})
        if m := re.search(r"Claim/RaiseClaimRequest/(\d+)$", path):
            prefill = self.prefills.get(int(m.group(1)))
            return _env(prefill) if prefill else _env(None, status=404, message="Tour id is not found.")
        if path.endswith("Claim/GetRaiseClaimDraft"):
            draft = self.drafts.get(int(request.url.params["empTourPlanId"]))
            return _env(draft) if draft else _env(None, status=404, message="No draft found for this tour plan.")
        if path.endswith("Claim/GetAllRaiseClaimDrafts"):
            rows = [
                {
                    "id": d["draftId"],
                    "empTourPlanId": d["empTourPlanId"],
                    "createdDate": "2026-10-01T10:00:00",
                    "lastSavedDate": d.get("lastSavedDate"),
                    "lastEditedSection": "Transportation",
                    "draftName": None,
                }
                for d in self.drafts.values()
            ]
            return _env(rows)
        if m := re.search(r"TourPlan/GetTourDetails/(\d+)$", path):
            plan = self.tour_plans.get(int(m.group(1)))
            return _env(plan) if plan else httpx.Response(404, json={"message": "Tour not found"})
        if path.endswith("Claim/SaveRaiseClaimDraft"):
            parts = _multipart(request)
            draft = json.loads(parts["draftData"][0][1])
            files = [(k, n) for k, vals in parts.items() for n, _ in vals if n]
            self.saved_requests.append({"draft": draft, "files": files})
            draft_id = draft.get("DraftId")
            if not draft_id:
                draft_id = self.next_draft_id
                self.next_draft_id += 1
            self.drafts[draft["EmpTourPlanId"]] = self._stored_draft(draft_id, draft, files)
            return _env({"draftId": draft_id, "message": "Draft saved successfully."})
        if path.endswith("Ocr/Upload"):
            parts = _multipart(request)
            self.upload_parts.append(list(parts))
            reference = parts["Reference"][0][1].decode()
            batch_id = f"b{self.next_batch}"
            self.next_batch += 1
            documents = []
            for index, (name, content) in enumerate(parts["Files"]):
                doc_id = 7000 + len(self.ocr_docs) + 1
                self.ocr_docs[doc_id] = {
                    "documentId": doc_id,
                    "index": index,
                    "reference": reference,
                    "fileName": name,
                    "batchId": batch_id,
                    "status": "Pending",
                    "isRead": False,
                    "content": content,
                    "response": None,
                }
                documents.append({"documentId": doc_id, "index": index, "fileName": name, "status": "Pending"})
            return _env({"batchId": batch_id, "documents": documents})
        if path.endswith("Ocr/GetMyDocuments"):
            self.list_params.append(dict(request.url.params))
            prefix = request.url.params.get("ReferencePrefix", "")
            include_read = request.url.params.get("IncludeRead") == "true"
            include_response = request.url.params.get("IncludeResponse") == "true"
            docs = [
                {k: v for k, v in d.items() if k != "content" and (include_response or k != "response")}
                for d in self.ocr_docs.values()
                if d["reference"].startswith(prefix)
                and (include_read or (not d["isRead"] and d["status"] != "Cancelled"))
            ]
            return _env({"summary": {}, "documents": docs})
        if m := re.search(r"Ocr/GetDocument/(\d+)$", path):
            doc = self.ocr_docs.get(int(m.group(1)))
            if doc is None:
                return _env(None, status=404, message="Document not found")
            return _env({k: v for k, v in doc.items() if k != "content"})
        if path.endswith("Ocr/MarkAsRead"):
            marked, already, pending, missing = [], [], [], []
            for doc_id in json.loads(request.content)["documentIds"]:
                doc = self.ocr_docs.get(doc_id)
                if doc is None:
                    missing.append(doc_id)
                elif doc["isRead"] or doc_id in self.taken_elsewhere:
                    already.append(doc_id)
                elif doc["status"] not in ("Completed", "Failed"):
                    pending.append(doc_id)
                else:
                    doc["isRead"] = True
                    marked.append(doc_id)
            return _env({"marked": marked, "alreadyRead": already, "stillPending": pending, "notFound": missing})
        if path.endswith("Ocr/Release"):
            ids = json.loads(request.content)["documentIds"]
            self.released += ids
            for doc_id in ids:
                if doc_id in self.ocr_docs:
                    self.ocr_docs[doc_id]["isRead"] = False
            return _env({"released": ids})
        if path.endswith("Ocr/Cancel"):
            ids = json.loads(request.content)["documentIds"]
            self.cancelled += ids
            for doc_id in ids:
                if doc_id in self.ocr_docs:
                    self.ocr_docs[doc_id]["status"] = "Cancelled"
            return _env({"cancelled": ids})
        if m := re.search(r"Ocr/Retry/(\d+)$", path):
            doc = self.ocr_docs[int(m.group(1))]
            self.retried.append(doc["documentId"])
            doc.update(status="Pending", isRead=False, response=None, errorMessage=None)
            return _env({"documentId": doc["documentId"], "status": "Pending"})
        if m := re.search(r"Ocr/GetFile/(\d+)$", path):
            doc = self.ocr_docs[int(m.group(1))]
            return httpx.Response(200, content=doc["content"], headers={"content-type": "application/pdf"})
        return None

    def _gateway(self, request: httpx.Request) -> httpx.Response:
        """POST <gateway>/chat/stream: the reply streams as plain text, in two parts."""
        body = json.loads(request.content)
        self.gateway_calls.append(body)
        if self.gateway_fail:
            return httpx.Response(self.gateway_fail.pop(0))
        reply = self.gateway_replies.pop(0) if self.gateway_replies else gateway_reply(body["message"])
        conversation = body.get("conversation_id") or f"conv-{len(self.gateway_calls)}"
        half = len(reply) // 2

        async def chunks():
            yield reply[:half].encode()
            yield reply[half:].encode()

        return httpx.Response(200, content=chunks(), headers={"X-Conversation-Id": conversation})

    def _stored_draft(self, draft_id: int, draft: dict[str, Any], files: list[tuple[str, str]]) -> dict[str, Any]:
        """Store a saved draft the way the Tour API returns it: rows plus file references by rowIndex."""
        previous = (self.drafts.get(draft["EmpTourPlanId"]) or {}).get("draftData") or {}
        data = {k: v for k, v in draft.items() if not k.startswith("Retained")}
        for rows_field, files_field, refs_field, retained_field in (
            (
                "TransportaionDetailDtos",
                "transportationFiles",
                "transportationFileReferences",
                "RetainedTransportationFileIds",
            ),
            (
                "AccomodationDetailDto",
                "accommodationFiles",
                "accomodationFileReferences",
                "RetainedAccomodationFileIds",
            ),
        ):
            retained = set(draft.get(retained_field) or [])
            refs = [r for r in previous.get(refs_field, []) if r["fileId"] in retained]
            new_names = [name for kind, name in files if kind == files_field]
            for index, row in enumerate(json.loads(draft.get(rows_field) or "[]")):
                start, end = row.get("StartFileIndexCount", -1), row.get("EndFileIndexCount", -1)
                if start is None or start < 0:
                    continue
                for i in range(start, end + 1):
                    refs.append(
                        {
                            "fileId": self.next_file_id,
                            "originalFileName": new_names[i],
                            "fileSize": 1,
                            "rowIndex": index,
                        }
                    )
                    self.next_file_id += 1
            data[refs_field] = refs
        return {"draftId": draft_id, "empTourPlanId": draft["EmpTourPlanId"], "lastSavedDate": "now", "draftData": data}

    def _read_routes(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(f"{request.method} {request.url.path}")
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"message": "failure"})
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            return httpx.Response(401, json={"message": "User is unauthorized.", "statusCode": 401})
        path = request.url.path

        if url.startswith(ORG_BASE):
            match = re.search(r"GetEmployeDetailsWithEmpCode/(.+)$", path)
            emp = self.org_override or (self.employees.get(match.group(1)) if match else None)
            if not emp:
                return _env(None, status=404, message="Employee not found")
            return _env(emp)

        if m := re.search(r"TourListing/GetTourRequest/(\d+)$", path):
            # .NET binds query names case-insensitively: the old page sent `toureType`, the 0.3 agent `touretype`
            kind = next((v for k, v in request.url.params.items() if k.lower() == "touretype"), None)
            source = self.outside_tours if kind == "1" else self.within_tours
            items = source.get(int(m.group(1)), [])
            return _env(items or None, status=200 if items else 0)
        if m := re.search(r"TourListing/GetTourRaiseClaimList/(\d+)$", path):
            items = [t for t in self.outside_tours.get(int(m.group(1)), []) if t["statusId"] == 2]
            return _env(items, status=200 if items else 404)
        if m := re.search(r"TourListing/GetTourTrajectory/(\d+)$", path):
            tour_id = int(m.group(1))
            return httpx.Response(
                200,
                json={
                    "statusCode": 0,
                    "message": "Tour trajectory retrieved successfully",
                    "data": {
                        "empTourId": tour_id,
                        "tourPlanTitle": "Tour",
                        "trajectory": [
                            {
                                "statusId": 1,
                                "statusName": "Pending for Approval",
                                "senderName": "Test Employee",
                                "recipientName": "Approver One",
                                "createdDate": "2026-09-01T10:00:00",
                            },
                            {
                                "statusId": 2,
                                "statusName": "Tour Approved",
                                "senderName": "Approver One",
                                "recipientName": "Test Employee",
                                "createdDate": "2026-09-02T10:00:00",
                            },
                        ],
                    },
                },
            )
        if re.search(r"TourListing/GetTotalPendingTaskCount/\d+/USER$", path):
            return _env(1)
        return httpx.Response(404, json={"message": "no route in fake"})


def gateway_reply(message: str) -> str:
    """What the fake model writes: the NOTES (or what the employee said) back, so tests see what it was told."""
    said = re.search(r"EMPLOYEE SAID:\n(.*?)(\n\n|$)", message, re.S)
    if said:
        return f"You said: {said.group(1)}"
    notes = re.search(r"NOTES:\n(.*?)(\n\nQUESTION:|\n\nWrite the message now)", message, re.S)
    lines = [re.sub(r"^\d+\. ", "", line) for line in (notes.group(1) if notes else "").split("\n")]
    return "\n".join(line for line in lines if line and not line.startswith("   data:"))


def _env(data: Any, *, status: int = 200, message: str = "") -> httpx.Response:
    return httpx.Response(200, content=json.dumps({"statusCode": status, "message": message, "data": data}))


def _multipart(request: httpx.Request) -> dict[str, list[tuple[str | None, bytes]]]:
    """Parse a multipart body into {field: [(file name or None, content)]}."""
    raw = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.read()
    message = email.message_from_bytes(raw)
    fields: dict[str, list[tuple[str | None, bytes]]] = {}
    for part in message.get_payload():
        name = part.get_param("name", header="content-disposition")
        fields.setdefault(name, []).append((part.get_filename(), part.get_payload(decode=True)))
    return fields


# The approved Mumbai tour (503). Dates use .NET's default DateTime.ToString() (en-US).
MUMBAI_PREFILL = {
    "empTourDetails": {
        "purposeOfTour": "Site inspection 503",
        "source": "Noida",
        "destination": "Mumbai",
        "tentativeStartDate": "08/20/2026 00:00:00",
        "noOfDays": "3",
    },
    "tentativeTourPlanList": [
        {"date": "08/20/2026 00:00:00", "from": "Noida", "to": "Mumbai", "travelMode": "Flight", "classOfCity": "X"},
        {"date": "08/22/2026 00:00:00", "from": "Mumbai", "to": "Noida", "travelMode": "Flight", "classOfCity": "Y"},
    ],
    "transportationList": [],
    "accommodationList": [],
}

FLIGHT_OUT = {
    "meta": {"per_file_results": [{"filename": "out.pdf", "success": True, "bill_type": "flight"}]},
    "tourDetails": {
        "startDate": "2026-08-20",
        "startTime": "7:15",
        "endDate": "2026-08-20",
        "endTime": "09:30",
        "from": "Delhi (Noida)",
        "to": "Mumbai",
        "modeOfTravel": "Flight",
        "amountDetails": {
            "billedAmount": 6500,
            "invoiceNo": "AI-123",
            "invoiceDate": "2026-08-10",
            "taxStructure": "IGST",
            "billIGST": 310,
        },
    },
}

FLIGHT_BACK = {
    "meta": {"per_file_results": [{"filename": "back.pdf", "success": True, "bill_type": "flight"}]},
    "tourDetails": {
        "startDate": "2026-08-22",
        "startTime": "18:05",
        "endDate": "2026-08-22",
        "endTime": "20:15",
        "from": "Mumbai",
        "to": "Delhi (Noida)",
        "modeOfTravel": "Flight",
        "amountDetails": {"billedAmount": 5800, "invoiceNo": "AI-456", "taxStructure": "IGST", "billIGST": 280},
    },
}

HOTEL = {
    "meta": {"per_file_results": [{"filename": "hotel.pdf", "success": True, "bill_type": "hotel"}]},
    "accommodationDetails": {
        "entries": [
            {
                "location": "Mumbai",
                "hotelName": "Harbour Inn",
                "cityClass": "X",
                "checkInDate": "20-08-2026",
                "checkInTime": "11:00",
                "checkOutDate": "22-08-2026",
                "checkOutTime": "10:00",
                "invoice": {
                    "billedAmount": 9000,
                    "invoiceNo": "HI-77",
                    "taxStructure": "SGST_CGST",
                    "billCGST": 540,
                    "billSGST": 540,
                },
            }
        ]
    },
}

PDF = b"%PDF-1.4 synthetic test file"
