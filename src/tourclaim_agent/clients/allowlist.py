"""Endpoints the agent may call (FRS §9, IDN-06). Everything else is denied.

Each entry is (HTTP method, path pattern relative to the service base URL, risk class).
Risk classes follow CNF-01: R0 read, R1 reversible write, R2 irreversible write,
OCR for OCR housekeeping. Only R0 and OCR endpoints are enabled in this release;
write endpoints are listed but switched off until payloads are verified against UAT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from tourclaim_agent.clients.errors import EndpointNotAllowed


class Risk(StrEnum):
    R0 = "R0"
    OCR = "OCR"
    R1 = "R1"
    R2 = "R2"


class Service(StrEnum):
    TOUR = "tour"
    ORG = "org"


@dataclass(frozen=True)
class AllowedEndpoint:
    service: Service
    method: str
    pattern: str
    risk: Risk
    enabled: bool = True

    def matches(self, service: Service, method: str, path: str) -> bool:
        if service is not self.service or method.upper() != self.method:
            return False
        regex = "^" + re.sub(r"\{[^/]+\}", r"[^/]+", self.pattern) + "$"
        return re.match(regex, path.split("?", 1)[0].strip("/"), flags=re.IGNORECASE) is not None


_T, _O = Service.TOUR, Service.ORG

ALLOWED_ENDPOINTS: tuple[AllowedEndpoint, ...] = (
    # --- Reads (R0) ---
    AllowedEndpoint(_T, "GET", "TourListing/GetTourRequest/{empId}", Risk.R0),
    AllowedEndpoint(_T, "GET", "TourListing/GetTourRaiseClaimList/{empId}", Risk.R0),
    AllowedEndpoint(_T, "GET", "TourListing/GetTourTrajectory/{tourId}", Risk.R0),
    AllowedEndpoint(_T, "GET", "TourListing/GetTotalPendingTaskCount/{empId}/{type}", Risk.R0),
    AllowedEndpoint(_T, "GET", "TourPlan/GetTourDetails/{tourId}", Risk.R0),
    AllowedEndpoint(_T, "GET", "Claim/RaiseClaimRequest/{tourId}", Risk.R0),
    AllowedEndpoint(_T, "GET", "Claim/ClaimsDetailsById/{tourId}", Risk.R0),
    AllowedEndpoint(_T, "GET", "Claim/GetSupplementaryClaimDetails/{tourId}", Risk.R0),
    AllowedEndpoint(_T, "GET", "Claim/GetAllRaiseClaimDrafts", Risk.R0),
    AllowedEndpoint(_T, "GET", "Claim/GetRaiseClaimDraft", Risk.R0),
    AllowedEndpoint(_T, "GET", "Claim/GetAllClaimWithRequestDrafts", Risk.R0),
    AllowedEndpoint(_T, "GET", "Claim/GetClaimWithRequestDraft", Risk.R0),
    AllowedEndpoint(_T, "GET", "ClaimClarification/thread/{tourId}", Risk.R0),
    AllowedEndpoint(_T, "GET", "ClaimEdit/can-edit/{tourId}", Risk.R0),
    AllowedEndpoint(_T, "GET", "Ocr/GetMyDocuments", Risk.R0),
    AllowedEndpoint(_T, "GET", "Ocr/GetDocument/{id}", Risk.R0),
    AllowedEndpoint(_T, "GET", "Ocr/GetFile/{id}", Risk.R0),
    AllowedEndpoint(_O, "GET", "Organization/GetEmployeDetailsWithEmpCode/{empCode}", Risk.R0),
    # --- OCR housekeeping ---
    AllowedEndpoint(_T, "POST", "Ocr/Upload", Risk.OCR),
    AllowedEndpoint(_T, "POST", "Ocr/MarkAsRead", Risk.OCR),
    AllowedEndpoint(_T, "POST", "Ocr/Release", Risk.OCR),
    AllowedEndpoint(_T, "POST", "Ocr/Cancel", Risk.OCR),
    AllowedEndpoint(_T, "POST", "Ocr/Retry/{id}", Risk.OCR),
    # --- Writes: listed, disabled until verified against UAT (EXE-01..09) ---
    AllowedEndpoint(_T, "POST", "Claim/SaveRaiseClaimDraft", Risk.R1, enabled=False),
    AllowedEndpoint(_T, "POST", "Claim/SaveClaimWithRequestDraft", Risk.R1, enabled=False),
    AllowedEndpoint(_T, "POST", "Claim/SubmitRaiseClaimDraft/{draftId}", Risk.R2, enabled=False),
    AllowedEndpoint(_T, "POST", "Claim/SubmitClaimWithRequestDraft/{draftId}", Risk.R2, enabled=False),
    AllowedEndpoint(_T, "POST", "ClaimClarification/respond", Risk.R2, enabled=False),
    AllowedEndpoint(_T, "POST", "ClaimClarification/respond-message", Risk.R2, enabled=False),
    AllowedEndpoint(_T, "POST", "ClaimEdit/edit", Risk.R2, enabled=False),
    AllowedEndpoint(_T, "POST", "Claim/RaiseSupplementaryClaim", Risk.R2, enabled=False),
    AllowedEndpoint(_T, "DELETE", "Claim/DeleteRaiseClaimDraft/{draftId}", Risk.R2, enabled=False),
    AllowedEndpoint(_T, "DELETE", "Claim/DeleteClaimWithRequestDraft/{draftId}", Risk.R2, enabled=False),
)


def check_allowed(
    service: Service, method: str, path: str, enabled_writes: frozenset[str] = frozenset()
) -> AllowedEndpoint:
    """Return the matching allow-list entry, or raise EndpointNotAllowed.

    `enabled_writes` holds patterns of disabled entries that an operator has switched on
    (setting TCA_ENABLED_WRITE_ENDPOINTS) after verifying them against UAT.
    """
    for entry in ALLOWED_ENDPOINTS:
        if entry.matches(service, method, path):
            if not entry.enabled and entry.pattern not in enabled_writes:
                raise EndpointNotAllowed(f"{method} {path} is on the allow-list but not enabled")
            return entry
    raise EndpointNotAllowed(f"{method} {path} is not on the agent allow-list")


def write_enabled(pattern: str, enabled_writes: frozenset[str]) -> bool:
    entry = next((e for e in ALLOWED_ENDPOINTS if e.pattern == pattern), None)
    return entry is not None and (entry.enabled or pattern in enabled_writes)
