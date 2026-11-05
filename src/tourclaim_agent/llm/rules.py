"""Deterministic, keyword-based intent extraction.

Used as the provider in development and tests, and as the fallback when an LLM reply
cannot be parsed. English only for now (DEC-9).
"""

from __future__ import annotations

import re

from tourclaim_agent.llm.intent import Goal, Intent, IntentContext, TourReference

_MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1}

# Checked in order; the first matching rule wins.
_GOAL_RULES: list[tuple[Goal, re.Pattern[str]]] = [
    (Goal.OUT_OF_SCOPE, re.compile(r"\b(approve|reject|sanction|pass the bill|someone else|on behalf)\b")),
    (Goal.SUPPLEMENTARY_CLAIM, re.compile(r"\bsupplementary\b")),
    (Goal.SAVE_DRAFT, re.compile(r"\bsave\b")),
    (
        Goal.CANCEL_CLAIM,
        re.compile(r"\b(cancel|discard|abandon|drop)\b.*\b(claim|draft|this|it)\b|^\s*(cancel|stop)\s*$"),
    ),
    (
        Goal.CLAIM_REVIEW,
        re.compile(
            r"\b(review|summary|summari[sz]e|check (my|the|this) (claim|bills?)|done|uploaded|finished|"
            r"what did you (read|find))\b"
        ),
    ),
    (Goal.ANSWER_CLARIFICATION, re.compile(r"\b(clarification|query|question from finance|reply to finance)\b")),
    (Goal.EDIT_CLAIM, re.compile(r"\b(edit|modify|change|correct)\b.*\bclaim\b")),
    (
        Goal.POLICY_QUESTION,
        re.compile(r"\b(entitle|eligible|eligibility|allowed to|policy|limit|how much da|da rate|rule)\w*"),
    ),
    (Goal.PENDING_ACTIONS, re.compile(r"\b(pending|to do|todo|action needed|need to do|anything for me)\b")),
    (Goal.CLAIMABLE_TOURS, re.compile(r"\b(can i claim|ready (to|for) claim|claimable|not claimed|unclaimed)\b")),
    (Goal.RAISE_CLAIM, re.compile(r"\b(raise|file|submit|make|start|create|new)\b.*\bclaim\b|\bclaim (my|the|for)\b")),
    (
        Goal.TOUR_STATUS,
        re.compile(r"\b(status|where is|stage|progress|update on|what happened|track|trajectory|stuck)\b"),
    ),
    (Goal.LIST_TOURS, re.compile(r"\b(my tours|list|show|all tours|tour history|which tours)\b")),
    (Goal.HELP, re.compile(r"\b(help|what can you do|how does this work)\b")),
    (Goal.GREETING, re.compile(r"^\s*(hi|hello|hey|namaste|good (morning|afternoon|evening))\b")),
]

_TOUR_ID = re.compile(r"(?:tour|claim|id|no\.?|number|#)\s*(?:no\.?|number|id)?\s*#?\s*(\d{1,9})\b")
# Words that are never place names. Whatever remains is treated as a possible place and
# matched against the source/destination of the employee's own tours by the resolver.
_NON_PLACE = set(
    """
    a about after again all am an and any anything are as at be been before being but by can claim claims
    clarification could current currently did do does done edit file finance for from get give go going got
    had has have hello help hey hi how i in is it its just know last latest let list me month my need new no
    not now number of on one or our please previous query question raise recent reply request right second
    see send sent show stage status still stuck submit tell than thanks that the their them then there this
    to today tour tours track trip trips update visit want was week went were what when where which who why
    will with would year yesterday you your
    start begin prepare create make ready draft save cancel discard stop review summary check bill bills
    ticket tickets hotel hotels journey journeys upload uploaded amount approved claimed pending form
    """.split()
)


def _place_words(text: str) -> str | None:
    words = [
        w
        for w in re.findall(r"[a-z]+", text)
        if len(w) >= 3 and w not in _NON_PLACE and w not in _MONTHS and w not in _ORDINALS
    ]
    return " ".join(words) or None


def _tour_reference(text: str) -> TourReference:
    ref = TourReference()
    if match := _TOUR_ID.search(text):
        ref.tour_id = int(match.group(1))
    for word in re.findall(r"[a-z]+", text):
        if word in _MONTHS and not (word == "may" and re.search(r"\bmay i\b", text)):
            ref.month = _MONTHS[word]
            break
    if match := re.search(r"\b(20\d{2})\b", text):
        ref.year = int(match.group(1))
    ref.latest = bool(re.search(r"\b(latest|last|recent|most recent|previous)\b", text))
    ref.place = _place_words(text)
    return ref


def _selection(text: str, option_count: int) -> int | None:
    if option_count <= 0:
        return None
    stripped = text.strip().rstrip(".")
    if re.fullmatch(r"\d{1,2}", stripped) and 1 <= int(stripped) <= option_count:
        return int(stripped)
    if match := re.search(r"\b(?:option|number|no\.?)\s*(\d{1,2})\b", text):
        value = int(match.group(1))
        return value if 1 <= value <= option_count else None
    for word, value in _ORDINALS.items():
        if re.search(rf"\b(the )?{word}( one)?\b", text) and len(text.split()) <= 4:
            return option_count if value == -1 else (value if value <= option_count else None)
    return None


class RuleBasedIntentExtractor:
    async def extract(self, message: str, context: IntentContext) -> Intent:
        text = " ".join(message.lower().split())
        selection = _selection(text, len(context.pending_options))
        if selection is not None:
            return Intent(goal=Goal.SELECT_OPTION, selection=selection)
        goal = next((g for g, pattern in _GOAL_RULES if pattern.search(text)), Goal.UNKNOWN)
        tour = _tour_reference(text)
        if goal is Goal.UNKNOWN and tour.tour_id:
            goal = Goal.TOUR_STATUS
        return Intent(goal=goal, tour=tour)
