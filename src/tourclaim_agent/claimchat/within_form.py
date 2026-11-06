"""The Within Jurisdiction claim form's tours, as the form keeps them (type hints and constants only).

Port of the types in Tour_React src/pages/withinJurisdiction/WithinJurisdictionFormPage.tsx (TourData,
FullTourRequest) and of DECLARATION_TEXTS from src/components/WithinJuridiction/Declaration.tsx. The tours are plain
dicts with the TypeScript field names, because the bridge passes them as JSON between React and the agent.

Dates: the form holds `startDate` / `endDate` as a JavaScript Date (a tour made from a tour plan) or a string (a
loaded draft). A Date reaches the agent as JSON gives it, `toISOString()` ('2026-09-30T18:30:00.000Z' for local
midnight of 1 Oct in India), and the tours made here carry that same text, so `new Date(value)` in the browser gives
back exactly the Date the old code made. Leg dates are always 'YYYY-MM-DD' strings. A key the TypeScript object held
as `undefined` is left out, as JSON leaves it out.

There are no browser File objects here: a file is a dict with its name, size and type (never the bytes).

withinAssistant.ts and withinTourPlan.ts import only these types from the form page; the form's own validation
(validateTour) and calculations (DA, totals, overlaps) run in the browser and are not ported.
"""

from __future__ import annotations

from typing import Any, Final, Literal, NotRequired, TypedDict

TaxStructure = Literal["SGST_CGST", "IGST", "NOT_APPLICABLE"]


class UploadedFile(TypedDict, total=False):
    id: str
    name: str
    size: int | float
    type: str
    uploadDate: str


class AmountDetails(TypedDict, total=False):
    """A journey's bill (AmountDetails.tsx); the runtime extras are listed after the typed fields."""

    uploadedFiles: list[UploadedFile]
    billedAmount: int | float
    claimedAmount: int | float
    totalClaimedAmount: int | float
    invoiceNo: str
    invoiceDate: str
    taxStructure: TaxStructure
    # runtime extras the form adds
    cgst: Any
    sgst: Any
    igst: Any
    billCGST: Any
    billSGST: Any
    billIGST: Any
    approvedAmount: Any
    financeRemark: Any
    billNotRequired: Any


# "from" is a Python keyword, so the real shapes are declared with the functional syntax.
TourLeg = TypedDict(
    "TourLeg",
    {
        "id": str,
        "transportationId": NotRequired[int],
        "departureDate": str,
        "departureTime": str,
        "from": str,
        "arrivalDate": str,
        "arrivalTime": str,
        "to": str,
        "modeOfTravel": str,
        "personalVehicleType": NotRequired[str],
        "distance": NotRequired[str | int | float],
        "includeTravelCosts": bool,
        "attendanceId": NotRequired[int | None],
        "amountDetails": NotRequired[AmountDetails],
    },
)


class AllowedDateBounds(TypedDict):
    minStart: str
    maxStart: str
    maxEnd: str


TourDetails = TypedDict(
    "TourDetails",
    {
        "hasUsedOCR": NotRequired[bool],
        "transportationId": NotRequired[int],
        # a Date as JSON gives it (ISO text), a 'YYYY-MM-DD' string, or left out (undefined)
        "startDate": NotRequired[str],
        "startTime": str,
        "endDate": NotRequired[str],
        "endTime": str,
        "modeOfTravel": str,
        "personalVehicleType": NotRequired[str],
        "distance": NotRequired[str | int | float],
        "from": str,
        "to": str,
        "purpose": str,
        "includeTravelCosts": bool,
        "attendanceId": NotRequired[int | None],
        "tourLegs": list[TourLeg],
        "tourEnd": bool,
        "subTotal": int | float,
        "amountDetails": NotRequired[AmountDetails],
        # typed as `any` in the form: only tours after the first of a tour plan have it
        "allowedDateBounds": NotRequired[AllowedDateBounds],
    },
)


class AccommodationDetails(TypedDict):
    # runtime entries (Accommodation.tsx): {id, isNewUIEntry, location, cityClass, accommodationType, hotelName,
    # hotelCity, checkInDate, checkInTime, checkOutDate, checkOutTime, invoice: {...}, files: [...]}
    entries: list[dict[str, Any]]
    totalAmount: int | float


class DaEntry(TypedDict):
    id: int
    date: str
    sourceTime: str
    endTime: str
    daSlab: str
    totalTime: str
    percentageAdmissible: int | float
    amount: int | float
    daApplicable: bool
    isNewUIEntry: NotRequired[bool]
    approvedAmount: NotRequired[int | float]
    financeRemark: NotRequired[str]


class DaAllowanceDetails(TypedDict):
    entries: list[DaEntry]
    subTotal: int | float
    approvingAuthority: str
    uploadedFile: Any  # File | null in the browser: None here
    nominationFile: NotRequired[Any]


class TourData(TypedDict):
    id: str
    tourDetails: TourDetails
    accommodationDetails: NotRequired[AccommodationDetails]
    daAllowanceDetails: NotRequired[DaAllowanceDetails]
    declarationAccepted: bool
    isExpanded: bool


class FullTourRequest(TypedDict):
    tours: list[TourData]
    totalAmount: int | float
    claimApprovedAmount: NotRequired[int | float]
    status: Literal["draft", "submitted"]
    submittedAt: NotRequired[str]


# Declaration.tsx:DECLARATION_TEXTS: the declarations the employee agrees to (also read out by the claim assistant
# before it submits).
DECLARATION_TEXTS: Final = (
    "The claim has neither been furnished nor claimed earlier",
    "I am liable to repay the entire/part amount in case of any erroneous claim being raised and passed",
    "I am liable for disciplinary/administrative action if any of the above details furnished are found to be "
    "incorrect",
    "Timing on trains has been shown as per Railway Time Table & actual arrival of train",
    "Tour destination is more than 8 km from my place of work",
    "The information given in this form is correct and complete to the best of my knowledge and belief",
    "The due amount can be deducted from my salary",
)
