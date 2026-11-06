"""The Reference every bill upload carries, saying where it was sent from; only that place goes through it.

Port of Tour_React src/lib/ocr/ocrReferences.ts:
- the Claim Assistant on the Raise Claim page:      outside-chat:unassigned (it works out each bill's tour itself)
- the Claim Assistant inside one claim form:         claim-chat:tour-<id>
- the claim form's own "Process & Auto-Fill":        claim-form:tour-<id>
- the Within Jurisdiction tour plan:                 within-plan
Uploads made before the chats had their own tags were sent as outside-chat:tour-<id> from inside a claim form:
they belong to that claim, never to the Raise Claim page.
"""

from __future__ import annotations

import re
from typing import Final

from .js_compat import js_int, js_number, js_str

RAISE_CLAIM_CHAT_REF: Final = "outside-chat:unassigned"


def claim_chat_ref(tour_id: int | str) -> str:
    """ocrReferences.ts:claimChatRef."""
    return f"claim-chat:tour-{js_str(tour_id)}"


def claim_form_ref(tour_id: int | str) -> str:
    """ocrReferences.ts:claimFormRef."""
    return f"claim-form:tour-{js_str(tour_id)}"


_LEGACY_CLAIM_CHAT = re.compile(r"^outside-chat:tour-([0-9]+)\Z")
_CLAIM_CHAT = re.compile(r"^claim-chat:tour-([0-9]+)\Z")
_CLAIM_FORM = re.compile(r"^claim-form:tour-([0-9]+)\Z")


def is_raise_claim_chat_ref(ref: str | None) -> bool:
    """ocrReferences.ts:isRaiseClaimChatRef: sent from the Claim Assistant on the Raise Claim page."""
    return bool(ref) and ref.startswith("outside-chat:") and not _LEGACY_CLAIM_CHAT.search(ref)


def is_claim_chat_ref(ref: str | None, tour_id: int | str) -> bool:
    """ocrReferences.ts:isClaimChatRef: sent from the Claim Assistant inside the claim form of this tour."""
    return bool(ref) and (ref == claim_chat_ref(tour_id) or ref == f"outside-chat:tour-{js_str(tour_id)}")


def claim_tour_of(ref: str | None) -> int | float | None:
    """ocrReferences.ts:claimTourOf.

    The tour a claim's upload is for (Claim Assistant inside the form, or the form's own upload); None for the others.
    """
    if ref is None:
        return None
    m = _CLAIM_CHAT.search(ref) or _LEGACY_CLAIM_CHAT.search(ref) or _CLAIM_FORM.search(ref)
    return js_int(js_number(m.group(1))) if m else None
