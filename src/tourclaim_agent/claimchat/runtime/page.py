"""What the page gave the chat component, as server calls with the employee's token.

Runtime design §8, Spec E §1.2, §1.8, §1.9 and Tour_React src/pages/RaiseClaim/RaiseClaim.tsx:

- the tours (`tours`, `toursLoading`): GET TourListing/GetTourRequest/{empId}?toureType=1, keeping
  `statusId === 2 || (statusId === 9 && isAdvanceTaken)` (the "Raise Claim" tab);
- the drafts (`draftTourIds`, `draftsInfo`, `draftsLoading`): GET Claim/GetAllRaiseClaimDrafts?empId;
- `pageInfo`: describeRaiseClaimPage(tours, drafts), for the AI model;
- `loadTour(row, {quiet})`: GET Claim/RaiseClaimRequest/{tourId} + GET TourPlan/GetTourDetails/{tourId}
  (loadAssistantTour), cached per chat;
- `onLoadDraft(tourId)`: GET Claim/GetRaiseClaimDraft {empId, empTourPlanId};
- `onSaveDraft(row, {tourId, state}, {overwrite})`: saveDraftFromAssistant (POST Claim/SaveRaiseClaimDraft, then
  GET GetAllRaiseClaimDrafts again) -> 'saved' | 'exists' | 'failed';
- `onFillForm(row, draft)`: the bridge's `openForm {tourId, state}` (React fetches the new bill files and opens the
  form, as fillFormFromAssistant did); `onOpenClaim(row)`: `openForm {tourId}` without a hand-off.

The page's state (tours, drafts, the loadTour cache) is kept in the session (`session.page`), loaded once (the old
page loaded it on mount) and the drafts again after every save.

What the server cannot reproduce: the page's toasts ("Failed to load the tour. Please try again.", "Failed to save
draft", "Failed to retrieve drafts") are not shown; the outcomes are the same.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

from tourclaim_agent.claimchat.claim_payload import assistant_draft_save, assistant_save_target
from tourclaim_agent.claimchat.js_compat import jget, js_int, js_number, js_str, js_truthy, nullish
from tourclaim_agent.claimchat.load_assistant_tour import assistant_tour_from
from tourclaim_agent.claimchat.runtime.session import to_jsonable
from tourclaim_agent.clients.http import UserScopedClient

if TYPE_CHECKING:
    from tourclaim_agent.claimchat.runtime.turn import Turn

log = logging.getLogger(__name__)

# RaiseClaim.tsx TAB_FILTERS: which tours each tab shows (the tabs, their counts and the Claim Assistant use these)
TAB_FILTERS: list[tuple[str, str, Any]] = [
    (
        "raise-claim",
        "Raise Claim (approved tours waiting for a claim)",
        lambda r: jget(r, "statusId") == 2 or (jget(r, "statusId") == 9 and js_truthy(jget(r, "isAdvanceTaken"))),
    ),
    ("clarification", "Clarification (finance asked a question)", lambda r: jget(r, "statusId") == 11),
    ("pending-finance", "Pending With Finance (claim submitted)", lambda r: jget(r, "statusId") in (3, 8, 17, 25)),
    ("availed", "Availed (claim completed)", lambda r: jget(r, "statusId") == 12),
    ("not-availed", "Not Availed", lambda r: jget(r, "statusId") in (10, 7)),
]


def is_claimable(row: Any) -> bool:
    """The "Raise Claim" tab: approved, or with an advance taken."""
    return TAB_FILTERS[0][2](row)


def describe_raise_claim_page(rows: list[Any], drafts: list[Any]) -> str:
    """RaiseClaim.tsx:describeRaiseClaimPage: the Raise Claim page as the employee sees it, for the AI model."""

    def day(v: Any) -> str:
        return js_str(v)[:10] if js_truthy(v) else "?"

    def tour(r: Any) -> str:
        total = jget(r, "totalDays")
        status = jget(r, "status")
        days = f", {js_str(total)} day{'' if total == 1 else 's'}" if js_truthy(total) else ""
        state = f', status "{js_str(status)}"' if js_truthy(status) else ""
        source = jget(r, "source") if js_truthy(jget(r, "source")) else "?"
        destination = jget(r, "destination") if js_truthy(jget(r, "destination")) else "?"
        return (
            f"  • Tour #{js_str(jget(r, 'tourId'))}: {js_str(source)} → {js_str(destination)}, "
            f"starts {day(jget(r, 'tourStartDate'))}{days}{state}"
        )

    tabs = []
    for _tab, label, match in TAB_FILTERS:
        listed = [r for r in rows if match(r)]
        shown = [tour(r) for r in listed[:40]]
        if len(listed) > len(shown):
            shown.append(f"  • …and {len(listed) - len(shown)} more")
        tabs.append(f"{label}: {len(listed)}" + ("\n" + "\n".join(shown) if shown else ""))
    if drafts:
        lines = "\n".join(
            f"  • Tour #{js_str(jget(d, 'empTourPlanId'))}"
            + (f": {js_str(jget(d, 'draftName'))}" if js_truthy(jget(d, "draftName")) else "")
            for d in drafts[:40]
        )
        saved = f"Saved drafts (claims started but not submitted): {len(drafts)}\n{lines}"
    else:
        saved = "Saved drafts (claims started but not submitted): 0"
    return "\n".join(
        [f"Tabs on the Raise Claim page, with the tours in each ({len(rows)} tours in all):", *tabs, saved]
    )


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


class PageServices:
    """The Raise Claim page's callbacks for one chat (and the in-form chat's tour loading)."""

    def __init__(self, turn: Turn, client: UserScopedClient, emp_id: int | None) -> None:
        self._turn = turn
        self._c = client
        self.emp_id = emp_id

    @property
    def _p(self) -> dict[str, Any]:
        return self._turn.session.page

    # ── tours and drafts (the page's own loading) ──
    async def load(self, *, force: bool = False) -> None:
        """Loads the tours and the drafts once (the page did on mount), or again with `force`."""
        jobs = []
        if force or not self._p.get("toursLoaded"):
            jobs.append(self.load_tours())
        if force or not self._p.get("draftsLoaded"):
            jobs.append(self.refresh_drafts())
        await asyncio.gather(*jobs)

    async def load_tours(self) -> None:
        """GET TourListing/GetTourRequest/{empId} {toureType: 1} (raiseClaimSlice getTourRequest)."""
        tour_data: Any = None
        error = None
        try:
            response = await self._c.send_raw(
                "GET", f"TourListing/GetTourRequest/{js_str(self.emp_id)}", params={"toureType": 1}
            )
            if response.status_code >= 400:
                error = f"HTTP {response.status_code}"
            else:
                tour_data = jget(_json(response), "data")
        except Exception as exc:  # noqa: BLE001 - the page showed its error and an empty list
            error = str(exc) or type(exc).__name__
        self._p["tourData"] = tour_data if isinstance(tour_data, list) else None
        self._p["toursError"] = error
        self._p["toursLoaded"] = True

    @property
    def tour_data(self) -> list[Any]:
        """Every tour the page lists (all tabs)."""
        return list(self._p.get("tourData") or [])

    @property
    def tours(self) -> list[Any]:
        """claimableTours: the tours waiting for a claim."""
        return [r for r in self.tour_data if is_claimable(r)]

    @property
    def tours_loading(self) -> bool:
        """`loading || (tourData == null && !toursError)`."""
        return not self._p.get("toursLoaded") or (self._p.get("tourData") is None and not self._p.get("toursError"))

    async def refresh_drafts(self) -> bool:
        """GET Claim/GetAllRaiseClaimDrafts {empId} (getAllDrafts). A failure keeps the list as it was."""
        try:
            response = await self._c.send_raw("GET", "Claim/GetAllRaiseClaimDrafts", params={"empId": self.emp_id})
            body = _json(response)
            if response.status_code < 400 and jget(body, "statusCode") == 200:
                data = jget(body, "data")
                self._p["drafts"] = data if isinstance(data, list) and data else []
                self._p["draftsError"] = None
            else:
                self._p["draftsError"] = js_str(nullish(jget(body, "message"), "Failed to retrieve drafts"))
        except Exception as exc:  # noqa: BLE001
            self._p["draftsError"] = str(exc) or "Failed to retrieve drafts"
        self._p.setdefault("drafts", [])
        self._p["draftsLoaded"] = True
        return self._p["draftsError"] is None

    @property
    def drafts(self) -> list[Any]:
        """allDrafts"""
        return list(self._p.get("drafts") or [])

    @property
    def drafts_loading(self) -> bool:
        return not self._p.get("draftsLoaded")

    @property
    def draft_tour_ids(self) -> list[Any]:
        """draftTourIds = allDrafts.map(d => d.empTourPlanId)"""
        return [jget(d, "empTourPlanId") for d in self.drafts]

    @property
    def drafts_info(self) -> list[dict[str, Any]]:
        """draftsInfo: `{tourId: Number(d.empTourPlanId), lastSaved: d.lastSavedDate ?? d.createdDate}` per draft."""
        return [
            {
                "tourId": js_int(js_number(jget(d, "empTourPlanId"))),
                "lastSaved": nullish(jget(d, "lastSavedDate"), nullish(jget(d, "createdDate"), None)),
            }
            for d in self.drafts
        ]

    @property
    def page_info(self) -> str | None:
        """assistantPageInfo: undefined until the tours are listed."""
        data = self._p.get("tourData")
        return describe_raise_claim_page(data, self.drafts) if isinstance(data, list) else None

    def tour_row(self, tour_id: Any) -> Any:
        """The page's row of a tour (any tab), or None."""
        return next((r for r in self.tour_data if jget(r, "tourId") == tour_id), None)

    # ── the component's callbacks ──
    async def load_tour(self, row: Any, *, quiet: bool = False) -> dict[str, Any] | None:
        """loadAssistantTour: {tour, rules} or None; cached per chat (a failure is not cached)."""
        tour_id = jget(row, "tourId")
        cache = self._p.setdefault("tourCache", {})
        key = js_str(tour_id)
        if key in cache:
            return cache[key]
        try:
            claim, plan = await asyncio.gather(
                self._c.send_raw("GET", f"Claim/RaiseClaimRequest/{key}"),
                self._plan(key),
            )
            if claim.status_code >= 400:
                raise RuntimeError(f"RaiseClaimRequest answered {claim.status_code}")
            result = assistant_tour_from(tour_id, _json(claim), plan)
        except Exception:  # noqa: BLE001 - the old loader logged it and returned null (with a toast unless quiet)
            log.warning("could not load tour %s for the assistant", key, exc_info=True)
            return None
        cache[key] = to_jsonable(result)
        return cache[key]

    async def _plan(self, tour_id: str) -> Any:
        """GET TourPlan/GetTourDetails/{tourId}; null when it fails (the old `.catch(() => null)`)."""
        try:
            response = await self._c.send_raw("GET", f"TourPlan/GetTourDetails/{tour_id}")
        except Exception:  # noqa: BLE001
            return None
        return _json(response) if response.status_code < 400 else None

    async def load_draft(self, tour_id: Any) -> Any:
        """loadDraftForAssistant: the tour's saved draft as the server holds it (`data`), or None."""
        if not self.emp_id:
            return None
        try:
            body = await self._read_draft(tour_id)
        except Exception:  # noqa: BLE001
            log.warning("could not read the assistant draft of tour %s", tour_id, exc_info=True)
            return None
        return jget(body, "data") if jget(body, "statusCode") == 200 and js_truthy(jget(body, "data")) else None

    async def _read_draft(self, tour_id: Any) -> Any:
        """GET Claim/GetRaiseClaimDraft {empId, empTourPlanId}: the body, None for an HTTP 404; raises otherwise."""
        response = await self._c.send_raw(
            "GET", "Claim/GetRaiseClaimDraft", params={"empId": self.emp_id, "empTourPlanId": tour_id}
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RuntimeError(f"GetRaiseClaimDraft answered {response.status_code}")
        return _json(response)

    async def save_draft(self, row: Any, draft: dict[str, Any], *, overwrite: bool = False) -> str:
        """saveDraftFromAssistant: 'saved' | 'exists' | 'failed'. A saved draft is replaced only with `overwrite`."""
        if not self.emp_id:
            return "failed"
        tour_id = jget(row, "tourId")
        try:
            known = any(jget(d, "empTourPlanId") == tour_id for d in self.drafts)
            body = None if known else await self._read_draft(tour_id)
            target, draft_id = assistant_save_target(self.drafts, tour_id, overwrite, lambda: body)
            if target == "exists":
                return "exists"
            request = assistant_draft_save(
                draft["state"], emp_id=self.emp_id, tour_id=tour_id, draft_id=draft_id, overwrite=overwrite
            )
            parts: list[Any] = [("draftData", (None, request["draftDataText"]))]
            for part, file in request["files"]:
                data, type_ = await self._turn.ocr.file_bytes(file)
                parts.append((part, (js_str(file.get("name")), data, type_ or "application/octet-stream")))
            response = await self._c.send_raw("POST", "Claim/SaveRaiseClaimDraft", files=parts)
            saved = _json(response)
            if response.status_code >= 400 or jget(saved, "statusCode") != 200:
                raise RuntimeError(
                    f"SaveRaiseClaimDraft: {js_str(nullish(jget(saved, 'message'), 'Unexpected error'))}"
                )
        except Exception:  # noqa: BLE001 - as the old catch: any failure is 'failed'
            log.warning("could not save the assistant draft of tour %s", tour_id, exc_info=True)
            return "failed"
        await self.refresh_drafts()
        self._turn.drafts_changed = True
        return "saved"

    async def fill_form(self, row: Any, draft: dict[str, Any]) -> bool:
        """fillFormFromAssistant: React fetches the new bill files, hands the claim to the form and opens it."""
        try:
            result = await self._turn.bridge.call(
                "openForm", {"tourId": draft.get("tourId"), "state": draft.get("state")}
            )
        except Exception:  # noqa: BLE001 - startClaim's failure is `false`
            log.info("the form could not be opened from the assistant", exc_info=True)
            return False
        return bool(jget(result, "opened"))

    async def open_claim(self, row: Any) -> bool:
        """openClaimFromAssistant: opens the claim form as it is (no hand-off)."""
        try:
            result = await self._turn.bridge.call("openForm", {"tourId": jget(row, "tourId")})
        except Exception:  # noqa: BLE001
            log.info("the claim could not be opened from the assistant", exc_info=True)
            return False
        return bool(jget(result, "opened"))
