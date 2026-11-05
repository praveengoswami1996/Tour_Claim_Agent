"""Typed, read-only client for the .NET Tour API (allow-listed endpoints only)."""

from __future__ import annotations

import json
from typing import Any

from tourclaim_agent.clients.http import FilePart, UserScopedClient
from tourclaim_agent.domain.claim import ClaimPrefill, ExistingDraft
from tourclaim_agent.domain.models import TourKind, TourSummary, TourTrajectory


def _sort_key(tour: TourSummary) -> int:
    day = tour.start_date or tour.request_date or tour.end_date
    return day.toordinal() if day else 0


class TourApiClient:
    def __init__(self, client: UserScopedClient) -> None:
        self._c = client

    async def list_tours(self, emp_id: int, kind: TourKind) -> list[TourSummary]:
        """GET TourListing/GetTourRequest/{empId}?touretype=1|2. Newest first."""
        envelope = await self._c.get_envelope(
            f"TourListing/GetTourRequest/{emp_id}",
            params={"touretype": int(kind)},
            not_found_ok=True,
        )
        if envelope is None or not envelope.data:
            return []
        tours = [TourSummary.model_validate(item) for item in envelope.data]
        for tour in tours:
            tour.kind = kind
        return tours

    async def list_all_tours(self, emp_id: int) -> list[TourSummary]:
        """Both outside- and within-jurisdiction tours, newest start date first."""
        tours = await self.list_tours(emp_id, TourKind.OUTSIDE_JURISDICTION)
        tours += await self.list_tours(emp_id, TourKind.WITHIN_JURISDICTION)
        unique = {t.tour_id: t for t in tours}
        return sorted(unique.values(), key=_sort_key, reverse=True)

    async def list_claimable_tours(self, emp_id: int) -> list[TourSummary]:
        """GET TourListing/GetTourRaiseClaimList/{empId}: tours whose latest status is 2."""
        envelope = await self._c.get_envelope(f"TourListing/GetTourRaiseClaimList/{emp_id}", not_found_ok=True)
        if envelope is None or not envelope.data:
            return []
        unique = {t.tour_id: t for t in (TourSummary.model_validate(i) for i in envelope.data)}
        return list(unique.values())

    async def get_trajectory(self, tour_id: int) -> TourTrajectory | None:
        """GET TourListing/GetTourTrajectory/{tourId}. Caller must check ownership first."""
        envelope = await self._c.get_envelope(f"TourListing/GetTourTrajectory/{tour_id}", not_found_ok=True)
        if envelope is None or not envelope.data:
            return None
        return TourTrajectory.model_validate(envelope.data)

    async def get_claim_prefill(self, tour_id: int) -> ClaimPrefill:
        """GET Claim/RaiseClaimRequest/{tourId}. Caller must check ownership first."""
        envelope = await self._c.get_envelope(f"Claim/RaiseClaimRequest/{tour_id}")
        assert envelope is not None
        return ClaimPrefill.model_validate(envelope.data or {})

    async def get_outside_draft(self, emp_id: int, tour_id: int) -> ExistingDraft | None:
        """GET Claim/GetRaiseClaimDraft?empId=&empTourPlanId=. None when there is no draft."""
        envelope = await self._c.get_envelope(
            "Claim/GetRaiseClaimDraft", params={"empId": emp_id, "empTourPlanId": tour_id}, not_found_ok=True
        )
        if envelope is None or not envelope.data:
            return None
        return ExistingDraft.model_validate(envelope.data)

    async def save_outside_draft(self, draft_data: dict[str, Any], files: list[FilePart]) -> int:
        """POST Claim/SaveRaiseClaimDraft (multipart). Returns the draft ID.

        Disabled on the allow-list until verified against UAT (TCA_ENABLED_WRITE_ENDPOINTS).
        """
        envelope = await self._c.post_envelope(
            "Claim/SaveRaiseClaimDraft",
            data={"draftData": json.dumps(draft_data, ensure_ascii=False)},
            files=files or None,
        )
        data = envelope.data or {}
        draft_id = data.get("draftId") or data.get("DraftId")
        if not draft_id:
            raise ValueError("draft saved but no draftId returned")
        return int(draft_id)

    async def pending_task_count(self, emp_id: int) -> int:
        """GET TourListing/GetTotalPendingTaskCount/{empId}/USER (statuses 11 and 13)."""
        envelope = await self._c.get_envelope(f"TourListing/GetTotalPendingTaskCount/{emp_id}/USER", not_found_ok=True)
        if envelope is None or envelope.data is None:
            return 0
        try:
            return int(envelope.data)
        except (TypeError, ValueError):
            return 0
