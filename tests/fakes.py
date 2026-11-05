"""A fake Tour API + Organisation service for tests. All data is synthetic."""

from __future__ import annotations

import email
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

TOUR_BASE = "https://tour.test/api"
ORG_BASE = "https://org.test/api"


def make_token(emp_code: str = "E1001", *, expires_in: int = 3600, **extra: Any) -> str:
    claims = {"UserId": emp_code, "iss": "https://sso.test", "exp": int(time.time()) + expires_in, **extra}
    return jwt.encode(claims, "test-secret-not-used-for-verification", algorithm="HS256")


def tour(tour_id: int, emp_id: int, *, kind_dest: str, status_id: int, status: str, start: str, **extra: Any):
    return {
        "tourId": tour_id,
        "empId": emp_id,
        "tourType": "TourRequestClaim",
        "purposeofTour": f"Site inspection {tour_id}",
        "source": "Noida",
        "destination": kind_dest,
        "tourStartDate": f"{start}T00:00:00",
        "tantetiveEndDate": f"{start}T00:00:00",
        "totalDays": 3,
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

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def finish_ocr(self, file_name: str, response: dict[str, Any] | None, *, failed: bool = False) -> None:
        """Simulate the OCR worker finishing a document."""
        doc = next(d for d in self.ocr_docs.values() if d["fileName"] == file_name)
        doc["status"] = "Failed" if failed else "Completed"
        doc["response"] = response
        doc["errorMessage"] = "could not read" if failed else None

    def handle(self, request: httpx.Request) -> httpx.Response:
        response = self._claim_routes(request)
        if response is not None:
            self.calls.append(f"{request.method} {request.url.path}")
            return response
        return self._read_routes(request)

    def _claim_routes(self, request: httpx.Request) -> httpx.Response | None:
        path = request.url.path
        if self.fail_with or not request.url.path.startswith("/api/"):
            return None
        if m := re.search(r"Claim/RaiseClaimRequest/(\d+)$", path):
            prefill = self.prefills.get(int(m.group(1)))
            return _env(prefill) if prefill else _env(None, status=404, message="Tour id is not found.")
        if path.endswith("Claim/GetRaiseClaimDraft"):
            draft = self.drafts.get(int(request.url.params["empTourPlanId"]))
            return _env(draft) if draft else _env(None, status=404, message="No draft found for this tour plan.")
        if path.endswith("Claim/SaveRaiseClaimDraft"):
            parts = _multipart(request)
            draft = json.loads(parts["draftData"][0][1])
            self.saved_requests.append(
                {"draft": draft, "files": [(k, n) for k, vals in parts.items() for n, _ in vals if n]}
            )
            draft_id = draft.get("DraftId") or 9001
            self.drafts[draft["EmpTourPlanId"]] = {"draftId": draft_id, "empTourPlanId": draft["EmpTourPlanId"]}
            return _env({"draftId": draft_id, "message": "Draft saved successfully."})
        if path.endswith("Ocr/Upload"):
            parts = _multipart(request)
            reference = parts["Reference"][0][1].decode()
            documents = []
            for index, (name, content) in enumerate(parts["Files"]):
                doc_id = 7000 + len(self.ocr_docs) + 1
                self.ocr_docs[doc_id] = {
                    "documentId": doc_id,
                    "index": index,
                    "reference": reference,
                    "fileName": name,
                    "status": "Pending",
                    "isRead": False,
                    "content": content,
                    "response": None,
                }
                documents.append({"documentId": doc_id, "index": index, "fileName": name, "status": "Pending"})
            return _env({"batchId": "b1", "documents": documents})
        if path.endswith("Ocr/GetMyDocuments"):
            prefix = request.url.params.get("ReferencePrefix", "")
            docs = [
                {k: v for k, v in d.items() if k != "content"}
                for d in self.ocr_docs.values()
                if d["reference"].startswith(prefix)
            ]
            return _env({"summary": {}, "documents": docs})
        if path.endswith("Ocr/MarkAsRead"):
            marked = []
            for doc_id in json.loads(request.content)["documentIds"]:
                doc = self.ocr_docs.get(doc_id)
                if (
                    doc
                    and not doc["isRead"]
                    and doc["status"] in ("Completed", "Failed")
                    and doc_id not in self.taken_elsewhere
                ):
                    doc["isRead"] = True
                    marked.append(doc_id)
            return _env({"marked": marked})
        if path.endswith("Ocr/Release"):
            self.released += json.loads(request.content)["documentIds"]
            return _env({"released": []})
        if path.endswith("Ocr/Cancel"):
            self.cancelled += json.loads(request.content)["documentIds"]
            return _env({"cancelled": []})
        if m := re.search(r"Ocr/GetFile/(\d+)$", path):
            doc = self.ocr_docs[int(m.group(1))]
            return httpx.Response(200, content=doc["content"], headers={"content-type": "application/pdf"})
        return None

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
            kind = request.url.params.get("touretype")
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
