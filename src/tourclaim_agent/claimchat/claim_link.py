"""The address of a saved claim.

Port of Tour_React src/lib/claimLink.ts. It opens the same page the Raise Claim list opens, so a claim can be opened
in its own tab; the page takes the tour from the address when it has no navigation state (see ApproverPage). The
query string is built and read as the browser's URLSearchParams does (application/x-www-form-urlencoded).
There is no `window.location.origin` here, so the origin is always passed in.
"""

from __future__ import annotations

from typing import Any, Final, NotRequired, TypedDict
from urllib.parse import quote_plus, unquote_plus

from .js_compat import UNDEFINED, defined, is_nullish, jget, js_int, js_is_finite, js_number, js_str

CLAIM_PATH: Final = "/raise-claim-approver"


class ClaimLinkTour(TypedDict):
    tourId: int
    statusId: NotRequired[int | None]
    totalDays: NotRequired[int | None]


class ClaimLinkState(TypedDict):
    tourId: int | float
    statusId: NotRequired[int | float]
    totalDays: NotRequired[int | float]
    isDraft: bool


def _encode(text: str) -> str:
    """The urlencoded serializer: letters, digits and *-._ stay; a space is '+'; everything else is %XX (UTF-8)."""
    return quote_plus(text, safe="*").replace("~", "%7E")


def claim_link(tour: ClaimLinkTour | dict[str, Any], origin: str) -> str:
    """claimLink.ts:claimLink: e.g. https://host/raise-claim-approver?tourId=4522&isDraft=true&statusId=3&totalDays=6"""
    query = [("tourId", js_str(jget(tour, "tourId"))), ("isDraft", "true")]
    for key in ("statusId", "totalDays"):
        if not is_nullish(jget(tour, key)):
            query.append((key, js_str(jget(tour, key))))
    return f"{origin}{CLAIM_PATH}?" + "&".join(f"{_encode(k)}={_encode(v)}" for k, v in query)


def _parse(search: str) -> list[tuple[str, str]]:
    """new URLSearchParams(search): name/value pairs, a leading '?' dropped."""
    text = search[1:] if search.startswith("?") else search
    pairs = []
    for part in text.split("&"):
        if part == "":
            continue
        name, _, value = part.partition("=")
        pairs.append((unquote_plus(name, errors="replace"), unquote_plus(value, errors="replace")))
    return pairs


def state_from_claim_link(search: str) -> ClaimLinkState | None:
    """claimLink.ts:stateFromClaimLink: the navigation state the claim page reads (None: not a claim link)."""
    pairs = _parse(search)

    def get(key: str) -> str | None:
        return next((v for k, v in pairs if k == key), None)

    def has(key: str) -> bool:
        return any(k == key for k, _ in pairs)

    tour_id = js_number(get("tourId"))
    if not has("tourId") or not js_is_finite(tour_id) or tour_id != int(tour_id) or tour_id <= 0:
        return None

    def number(key: str) -> Any:
        value = js_number(get(key))
        return js_int(value) if has(key) and js_is_finite(value) else UNDEFINED

    return defined(  # type: ignore[return-value]
        tourId=js_int(tour_id),
        statusId=number("statusId"),
        totalDays=number("totalDays"),
        isDraft=get("isDraft") == "true",
    )
