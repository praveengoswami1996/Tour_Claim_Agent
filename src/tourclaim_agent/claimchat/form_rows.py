"""The claim form's rows, as the Raise Claim page keeps them (type hints only).

Port of the row types in Tour_React src/pages/RaiseClaim/sections/: TransportationSection.tsx (TransportationRow),
AccommodationSection.tsx (AccommodationRow), LeaveSection.tsx (LeaveRow) and TransportationAmountSection.tsx
(TransportUploadedFile). The rows are plain dicts with the TypeScript field names, because the bridge passes them
as JSON between React and the agent. Amounts stay strings where the form keeps strings.

There are no browser File objects here: a file on a row is a dict with its name, size and type, plus `fileId`,
`isRetained` and `tempFilePath` for a saved draft's file, or `documentId` for a bill the server holds (the OCR
upload). A transport row's `invoiceDate` (a JavaScript Date in the form) is kept as 'YYYY-MM-DD'.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

TaxStructure = Literal["IGST", "SGST_CGST", "NA"]


class RowFile(TypedDict, total=False):
    """A bill file on a row (rowOrder.ts RowFile, TransportationAmountSection.tsx TransportUploadedFile)."""

    id: str
    name: str
    size: int | float
    type: str
    uploadDate: str
    fileId: str
    isRetained: bool
    tempFilePath: str
    filePath: str
    fileName: str
    documentId: Any


class TransportationRow(TypedDict):
    """TransportationSection.tsx TransportationRow (note `TimeOfTravelDestiNation`, with a capital N)."""

    id: str
    remark: NotRequired[str]
    DateOfTravelSource: str
    TimeOfTravelSource: str
    source: str
    ModeOfTravel: str
    DateOfTravelDestination: str
    TimeOfTravelDestiNation: str
    approvedAmount: NotRequired[int | float | str]
    destination: str
    distanceKm: str
    invoiceNo: NotRequired[str]
    invoiceDate: NotRequired[str]
    travelCost: str
    cgst: str
    igst: str
    sgst: str
    BillSGSTAmount: str
    BillCGSTAmount: str
    BillIGSTAmount: str
    taxStructure: TaxStructure
    totalBilledAmount: str
    claimedAmount: str
    isPaidUsingCard: bool
    billNotRequired: bool
    uploadedFiles: list[RowFile]
    includeTravelCosts: bool
    remarks: NotRequired[str]
    financeRemark: NotRequired[str]


class AccommodationRow(TypedDict):
    """AccommodationSection.tsx AccommodationRow."""

    id: str
    checkInDate: str
    checkInTime: str
    checkOutDate: str
    checkOutTime: str
    location: str
    classOfCity: str
    ownArrangement: Literal["Yes", "No"]
    freeProvidedByCompany: Literal["Yes", "No"]
    companyPaid: Literal["Yes", "No"]
    hotelName: str
    billed: str
    tax: str
    totalBilled: str
    claimed: str
    billFiles: list[RowFile]
    selectedType: NotRequired[str]
    invoiceNo: NotRequired[str]
    invoiceDate: NotRequired[str]
    BillSGSTAmount: str
    BillCGSTAmount: str
    BillIGSTAmount: str
    taxType: NotRequired[TaxStructure]
    taxStructure: NotRequired[TaxStructure]
    ClaimedIGSTAmount: int | float
    ClaimedSGSTAmount: int | float
    ClaimedCGSTAmount: int | float
    remarks: NotRequired[str]
    approvedAmount: NotRequired[str]
    financeRemark: NotRequired[str]
    isPaidUsingCard: NotRequired[bool]
    accommodationId: NotRequired[int]
    validationErrors: NotRequired[dict[str, bool]]


class LeaveRow(TypedDict):
    """LeaveSection.tsx LeaveRow."""

    id: str
    startDate: str
    endDate: str
    daDeductable: Literal["Yes", "No"]
    amount: str
