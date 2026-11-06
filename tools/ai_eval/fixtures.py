"""Fixtures for the Claim Assistant AI evaluation: port of Tour_React scripts/ai-eval/fixtures.ts (the parts the
checks need: tours, claims and the tour plan). The CONTEXT texts the cases send are taken from C_eval_cases.json,
which recorded them exactly as the TypeScript built them.
"""

from __future__ import annotations

from typing import Any

from tourclaim_agent.claimchat.claim_assistant import (
    initial_claim_state,
    new_accommodation_row,
    new_transport_row,
)
from tourclaim_agent.claimchat.within_tour_plan import map_tour_plan_to_tours

TOURS: list[dict[str, Any]] = [
    {"tourId": 4521, "source": "Noida", "destination": "Mumbai", "dates": "05 Oct 2026 – 10 Oct 2026"},
    {"tourId": 4522, "source": "Delhi", "destination": "Jaipur", "dates": "18 Jan 2026 – 20 Jan 2026"},
    {"tourId": 4530, "source": "Lucknow", "destination": "Kolkata", "dates": "02 Mar 2026 – 06 Mar 2026"},
    {"tourId": 4533, "source": "Noida", "destination": "Ahmedabad", "dates": "10 Aug 2026 – 14 Aug 2026"},
]

RULES: dict[str, Any] = {"allowAir": True, "minDate": "2026-10-05", "maxDate": "2026-10-11"}


def journey(**j: Any) -> dict[str, Any]:
    """fixtures.ts › journey"""
    return {**new_transport_row(), "ModeOfTravel": "select", **j}


def stay(**a: Any) -> dict[str, Any]:
    """fixtures.ts › stay"""
    return {**new_accommodation_row(), **a}


def claim_with(journeys: list[dict[str, Any]], stays: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """fixtures.ts › claimWith"""
    stays = stays or []
    return {
        **initial_claim_state(),
        "transportationRows": journeys,
        "accommodationRows": stays if stays else initial_claim_state()["accommodationRows"],
        "includeAccommodation": len(stays) > 0,
    }


def full_claim() -> dict[str, Any]:
    """fixtures.ts › fullClaim: three journeys and one stay, some details still missing."""
    return claim_with(
        [
            journey(
                source="Noida",
                destination="Mumbai",
                DateOfTravelSource="2026-10-05",
                TimeOfTravelSource="10:30",
                DateOfTravelDestination="2026-10-05",
                TimeOfTravelDestiNation="13:00",
                ModeOfTravel="air",
            ),
            journey(
                source="Mumbai",
                destination="Pune",
                DateOfTravelSource="2026-10-07",
                TimeOfTravelSource="09:00",
                DateOfTravelDestination="2026-10-07",
                TimeOfTravelDestiNation="",
                ModeOfTravel="rail",
            ),
            journey(
                source="Pune",
                destination="Noida",
                DateOfTravelSource="2026-10-09",
                TimeOfTravelSource="18:00",
                DateOfTravelDestination="2026-10-09",
                TimeOfTravelDestiNation="20:10",
                ModeOfTravel="air",
            ),
        ],
        [
            stay(
                location="Mumbai",
                checkInDate="2026-10-05",
                checkInTime="14:00",
                checkOutDate="2026-10-07",
                checkOutTime="08:00",
                hotelName="",
                invoiceNo="",
            )
        ],
    )


def _leg(frm: str, to: str, d: str, dep: str, arr: str, mode: str, vehicle: str = "") -> dict[str, Any]:
    return {
        "from": frm,
        "to": to,
        "departureDate": d,
        "departureTime": dep,
        "arrivalDate": d,
        "arrivalTime": arr,
        "modeOfTravel": mode,
        "personalVehicleType": vehicle,
        "distance": 0,
        "includeTravelCosts": True,
    }


def plan() -> dict[str, Any]:
    """fixtures.ts › plan: 1 Oct (1 journey), 2 Oct (2), 3 Oct (3, personal vehicle), 4 Oct (2, two wheeler)."""
    return {
        "meta": {"per_file_results": [{"filename": "Oct_Tour_Plan.pdf", "success": True, "bill_type": "tour_plan"}]},
        "tourDetails": {
            "purpose": "Inspect; Review; Audit; Meeting",
            "startDate": "2026-10-01",
            "startTime": "09:00",
            "from": "Delhi",
            "endDate": "2026-10-01",
            "endTime": "11:00",
            "to": "Ghaziabad",
            "modeOfTravel": "Company Provided Vehicle",
            "personalVehicleType": "",
            "distance": 0,
            "includeTravelCosts": True,
            "tourLegs": [
                _leg("Delhi", "Faridabad", "2026-10-02", "08:30", "10:00", "Rail"),
                _leg("Faridabad", "Palwal", "2026-10-02", "14:00", "15:15", "Public Transport"),
                _leg("Delhi", "Noida", "2026-10-03", "07:45", "09:15", "Personal Vehicle", "Four Wheeler"),
                _leg("Noida", "Greater Noida", "2026-10-03", "13:00", "14:00", "Personal Vehicle", "Four Wheeler"),
                _leg("Greater Noida", "Dadri", "2026-10-03", "16:30", "17:15", "Personal Vehicle", "Four Wheeler"),
                _leg("Delhi", "Gurugram", "2026-10-04", "09:30", "11:00", "Personal Vehicle", "Two Wheeler"),
                _leg("Gurugram", "Manesar", "2026-10-04", "14:30", "15:30", "Personal Vehicle", "Two Wheeler"),
            ],
        },
    }


def plan_tours() -> list[dict[str, Any]]:
    """fixtures.ts › planTours"""
    return map_tour_plan_to_tours(plan())["tours"]  # type: ignore[return-value]
