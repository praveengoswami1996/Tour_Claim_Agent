"""The OCR result (the JSON the old /extract returned) and how the server's results are put back together.

Port of Tour_React src/lib/ocr/ocrResults.ts. The server reads each file on its own, so a read comes back as one
result per file; the claim mapping (map_ocr_to_rows, the claim form's auto-fill) works on one result for all the
files, as /extract gave it. Results are plain dicts with the OCR's field names.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, NotRequired, TypedDict

from .js_compat import UNDEFINED, defined, jget, js_truthy, nullish, opt_trim

OcrClaimResult = dict[str, Any]
"""{tourDetails?: {startDate, startTime, endDate, endTime, from, to, modeOfTravel, distance, amountDetails,
tourLegs: [{departureDate, departureTime, arrivalDate, arrivalTime, from, to, modeOfTravel, distance,
amountDetails}]}, accommodationDetails?: {totalAmount?, entries: [{location, cityClass, checkInDate, checkInTime,
checkOutDate, checkOutTime, accommodationType, hotelName, invoice, sourceFileName}]}, meta?: {per_file_results:
[{filename, success, bill_type?, error?}]}}; amountDetails / invoice: {billedAmount, claimedAmount,
totalClaimedAmount, claimedAmountWithTax, invoiceNo, invoiceDate, taxStructure, cgst, sgst, igst, billCGST,
billSGST, billIGST, sourceFileName}."""


class FileResult(TypedDict):
    """One file's result, with the name its file goes by in the claim."""

    fileName: str
    # the server could not read it (Failed), or the OCR said so for the file
    failed: bool
    response: NotRequired[OcrClaimResult | None]


def _has_tour(t: Any) -> bool:
    return js_truthy(t) and (
        js_truthy(opt_trim(jget(t, "from")))
        or js_truthy(opt_trim(jget(t, "to")))
        or js_truthy(jget(t, "startDate"))
        or js_truthy(jget(t, "endDate"))
    )


def combine_ocr_results(files: list[FileResult]) -> OcrClaimResult:
    """ocrResults.ts:combineOcrResults.

    One result for all the files, as /extract used to give it: the first file's journey is the main tour, every
    other journey a leg, the stays one after another. Every journey and stay is tied to the file it came from by
    name (the OCR may leave sourceFileName empty, but each result here is for one file), so names must be unique.
    """
    legs: list[dict[str, Any]] = []
    stays: list[dict[str, Any]] = []
    per_file: list[dict[str, Any]] = []
    main: dict[str, Any] | None = None

    for item in files:
        file_name = item["fileName"]
        response = item.get("response")
        per_file_results = nullish(jget(jget(response, "meta"), "per_file_results"), [])
        ocr_failed = len(per_file_results) > 0 and all(not js_truthy(jget(r, "success")) for r in per_file_results)
        if js_truthy(item["failed"]) or ocr_failed or not js_truthy(response):
            per_file.append({"filename": file_name, "success": False})
            continue
        assert isinstance(response, dict)
        first = per_file_results[0] if per_file_results else UNDEFINED
        per_file.append(defined(filename=file_name, success=True, bill_type=jget(first, "bill_type")))

        def own(a: Any, file_name: str = file_name) -> dict[str, Any]:
            return {**(nullish(a, {})), "sourceFileName": file_name}

        tour = jget(response, "tourDetails")
        if _has_tour(tour):
            head = {**tour, "amountDetails": own(jget(tour, "amountDetails"))}
            if main is None:
                main = {**head, "tourLegs": []}
            else:
                legs.append(
                    defined(
                        departureDate=jget(head, "startDate"),
                        departureTime=jget(head, "startTime"),
                        arrivalDate=jget(head, "endDate"),
                        arrivalTime=jget(head, "endTime"),
                        **{"from": jget(head, "from")},
                        to=jget(head, "to"),
                        modeOfTravel=jget(head, "modeOfTravel"),
                        distance=jget(head, "distance"),
                        amountDetails=head["amountDetails"],
                    )
                )
        for leg in nullish(jget(tour, "tourLegs"), []):
            legs.append({**leg, "amountDetails": own(jget(leg, "amountDetails"))})
        for acc in nullish(jget(jget(response, "accommodationDetails"), "entries"), []):
            invoice = jget(acc, "invoice")
            stay = {**acc, "sourceFileName": file_name}
            if js_truthy(invoice):
                stay["invoice"] = {**invoice, "sourceFileName": file_name}
            elif invoice is UNDEFINED:
                stay.pop("invoice", None)
            else:
                stay["invoice"] = invoice
            stays.append(stay)

    # no file had a main journey, but some had legs: the first leg leads
    if main is None and len(legs) > 0:
        first_leg, rest = legs[0], legs[1:]
        main = defined(
            startDate=jget(first_leg, "departureDate"),
            startTime=jget(first_leg, "departureTime"),
            endDate=jget(first_leg, "arrivalDate"),
            endTime=jget(first_leg, "arrivalTime"),
            **{"from": jget(first_leg, "from")},
            to=jget(first_leg, "to"),
            modeOfTravel=jget(first_leg, "modeOfTravel"),
            distance=jget(first_leg, "distance"),
            amountDetails=jget(first_leg, "amountDetails"),
            tourLegs=rest,
        )
    elif main is not None:
        main = {**main, "tourLegs": legs}

    return {
        **({"tourDetails": main} if main is not None else {}),
        "accommodationDetails": {"entries": stays},
        "meta": {"per_file_results": per_file},
    }


_EXTENSION = re.compile(r"(\.[^.]*)?\Z")


def uniquely_named(files: list[dict[str, Any]], taken: Iterable[str] = ()) -> list[dict[str, Any]]:
    """ocrResults.ts:uniquelyNamed.

    The files with names that are unique among them (a second "ticket.pdf" becomes "ticket (2).pdf"). A renamed
    file is a copy of the file dict with the new name; the others are returned as they are.
    """
    used = set(taken)
    named: list[dict[str, Any]] = []
    for f in files:
        name = f["name"]
        n = 2
        while name in used:
            name = _EXTENSION.sub(lambda m, n=n: f" ({n}){m.group(1) or ''}", f["name"], count=1)
            n += 1
        used.add(name)
        named.append(f if name == f["name"] else {**f, "name": name})
    return named
