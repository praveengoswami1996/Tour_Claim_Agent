"""The actions a typed message can trigger: port of Tour_React src/lib/claimChatbot/assistantActions.ts.

What the Claim Assistant's AI model is told: the actions a typed message can trigger at each point of the chat,
the open question, and what each assistant does. The chat flows attach the code that runs each action; the AI
evaluation (tools/ai_eval) uses the same lists, so it tests exactly what the employee gets.

The chat states are plain dictionaries with the TypeScript field names (OutsideChatState, WithinChatState).
"""

from __future__ import annotations

from typing import Final, Literal, NotRequired, TypedDict

from tourclaim_agent.claimchat.ai.ai_assistant import ChatAction
from tourclaim_agent.claimchat.claim_assistant import DA_RULES
from tourclaim_agent.claimchat.js_compat import js_str
from tourclaim_agent.claimchat.within_assistant import WITHIN_DA_RULES

# ─── Raise Claim (outside jurisdiction) ───────────────────────────────────────

OutsideStep = Literal[
    "start",
    "confirmTour",
    "pickTour",
    "bulkDrafts",
    "splitTours",
    "moreFiles",
    "gaps",
    "details",
    "stay",
    "leave",
    "confirm",
    "validate",
    "uploadChoice",
    "review",
    "submit",
]

OUTSIDE_STEPS: Final[tuple[str, ...]] = (
    "start",
    "confirmTour",
    "pickTour",
    "bulkDrafts",
    "splitTours",
    "moreFiles",
    "gaps",
    "details",
    "stay",
    "leave",
    "confirm",
    "validate",
    "uploadChoice",
    "review",
    "submit",
)

OUTSIDE_DOMAIN: Final = (
    "This chat helps a DFCCIL employee raise the expense claim for an approved tour (outside jurisdiction). The "
    "employee uploads tickets and hotel bills; they are read automatically and matched to one of their approved "
    "tours. The claim has journeys (from, to, departure and arrival date and time, travel mode: the main flights "
    "and trains, and the local conveyance around them such as cabs, taxis, autos, buses, metro, own or office "
    "vehicle, which are journeys too), overnight stays (city, check-in and check-out, hotel name, invoice number, "
    "type) and leave days. Missing details are collected one at a time in the chat. On the Raise Claim page the "
    "claim is built in the chat and then filled into the claim form when the employee asks; inside the claim form "
    "every change goes straight into the form, and the claim can be checked and submitted from the chat after the "
    "employee agrees to the declaration. A draft is the claim saved without submitting; it stays in the Raise "
    "Claim tab.\n"
    "Inside a claim form the chat only reads the employee's bills into that claim and says what it found in each "
    "file, what it added, what was already there, what could not be added and what does not belong to the claim; "
    "then it asks whether there are more bills. It never says what the claim is missing and never suggests what "
    "to upload, add or fix: checking and submitting are done on the claim form.\n"
    "What you can do: read bills, find the tour, fill in and correct journeys, stays and leave from what the "
    "employee types, remove entries, show the claim, save a draft, fill the form (on the Raise Claim page), undo "
    "the last change and start over. You cannot enter amounts or attach bill files to the form: those are done on "
    "the form, which works out DA and totals.\n"
    "\n"
    "DA RULES:\n"
    f"{DA_RULES}"
)

OUTSIDE_STEP_QUESTIONS: Final[dict[str, str]] = {
    "confirmTour": "Are the uploaded bills for the suggested tour?",
    "pickTour": "Which tour is this claim for?",
    "bulkDrafts": "Save a draft for each tour found in the bills, or work on one tour here?",
    "splitTours": "Which tour shall we work on first?",
    "moreFiles": "Do you have more tickets or bills to upload?",
    "gaps": "Fill the missing details here in the chat, or on the form?",
    "stay": "Did you stay anywhere overnight?",
    "leave": "Were you on leave on any day of the tour?",
    "validate": "Sort out what the form will flag here, or open the form as it is?",
    "uploadChoice": "Add the new bills to the existing claim, or replace the existing journeys and stays?",
    "submit": "Shall I submit the claim now?",
}


class YesNo(TypedDict):
    """An open yes / no question, with its button labels."""

    question: str
    yesLabel: str
    noLabel: str


class ReviewAsk(TypedDict):
    """The review question being asked (review step, inside the form)."""

    question: str
    blocking: bool
    resolutions: list[Literal["chat", "upload", "form", "confirm"]]
    yes: NotRequired[Literal["upload", "confirm", "details"] | None]
    no: NotRequired[Literal["confirm", "remove"] | None]
    newJourney: NotRequired[bool | None]  # journey details in the answer are a new journey
    oddPlace: NotRequired[bool | None]  # a place that doesn't look like a place: the answer corrects it


class OutsideChatState(TypedDict):
    """assistantActions.ts › OutsideChatState."""

    step: OutsideStep
    inForm: bool
    noTours: bool
    yesNo: NotRequired[YesNo | None]  # an open yes / no question, with its button labels
    resetAsk: bool  # "start over?" is waiting for an answer
    suggestedTourId: NotRequired[int | None]  # the tour suggested for the bills (confirmTour)
    hasTour: bool  # a tour is confirmed
    hasBulk: bool  # "save a draft for each tour?" is open
    hasSplit: bool  # "which tour first?" is open
    hasMissing: bool  # the claim still has missing details (validate step)
    pendingQuestion: NotRequired[str | None]  # the question for one missing detail (details step)
    canUndo: bool
    hasSavedTours: bool  # saved claims offered to continue
    review: NotRequired[ReviewAsk | None]


def outside_open_question(s: OutsideChatState) -> str | None:
    """assistantActions.ts › outsideOpenQuestion."""
    if s.get("yesNo") is not None:
        return s["yesNo"]["question"]  # type: ignore[index]
    if s.get("resetAsk"):
        return "Shall I clear the claim and start over?"
    if s.get("step") == "details" and s.get("pendingQuestion"):
        return s.get("pendingQuestion")
    if s.get("step") == "review" and s.get("review"):
        return s["review"]["question"]  # type: ignore[index]
    return OUTSIDE_STEP_QUESTIONS.get(s.get("step"))  # type: ignore[arg-type]


TOUR_ID_ARG: Final = '{"tourId": <number>}'


def outside_actions(s: OutsideChatState) -> list[ChatAction]:
    """assistantActions.ts › outsideActions: the actions on offer right now, in order.

    The first one, "talk", is also what an unreadable answer becomes.
    """
    actions: list[ChatAction] = []

    def add(name: str, description: str, args: str | None = None) -> None:
        actions.append(ChatAction(name, description, args))

    add("talk", "a question, greeting, thanks, complaint, help request, anything else, or not sure")
    if s.get("noTours"):
        return actions
    yes_no = s.get("yesNo")
    if yes_no is not None:
        add("yes", f'agrees ("{yes_no["yesLabel"]}")')
        add("no", f'declines ("{yes_no["noLabel"]}")')
    if s.get("resetAsk"):
        add("confirm_start_over", "yes, clear the claim and start over")
        add("keep_claim", "no, keep the claim as it is")
    step = s.get("step")
    in_form = s.get("inForm")
    if step in ("confirmTour", "pickTour") and not in_form:
        if step == "confirmTour" and s.get("suggestedTourId") is not None:
            add("confirm_tour", f"yes, the claim is for the suggested tour #{js_str(s.get('suggestedTourId'))}")
            add("choose_another_tour", "no, it is a different tour (none named)")
        add("pick_tour", "names one of the TOURS ON OFFER in CONTEXT", TOUR_ID_ARG)
    if step == "bulkDrafts" and s.get("hasBulk"):
        add("save_drafts", "yes, save the bills into a draft for each tour")
        add("dont_save", "no, do not save the drafts")
        add("work_on_one_tour", "work on one tour here in the chat instead")
    if step == "splitTours" and s.get("hasSplit"):
        add("pick_tour", "names which of the TOURS ON OFFER in CONTEXT to work on first", TOUR_ID_ARG)
    if step == "moreFiles":
        add("have_more_files", "yes, has more tickets or bills to upload")
        add("no_more_files", "no, nothing more to upload")
    if step == "gaps":
        add("fill_here", "fill the missing details here in the chat")
        add("fill_on_form", "leave them for the form (on the form, myself, manually, later)")
    if step == "validate":
        if s.get("hasMissing"):
            add("fill_here", "sort them out here in the chat")
        add("open_form_anyway", "open the claim form as it is")
    if step == "uploadChoice":
        add("add_to_claim", "add the new bills to the existing claim (add, merge, keep both, do not replace)")
        add("replace_claim_data", "replace the existing journeys and stays with the new bills")
        add("dont_add", "neither: do not add the new bills now (not now, cancel)")
    if step == "submit" and in_form:
        add("confirm_submit", "a plain yes: agrees to the declaration and wants the claim submitted now")
        add("dont_submit_yet", "not yet, or wants to change something first")
    if step == "stay":
        add("no_stay", "no overnight stay")
    if step == "leave":
        add("no_leave", "no leave on any day")
    if step == "details" and s.get("pendingQuestion"):
        add("skip_question", "skip the open question (fill it on the form, does not know, never mind)")
    review = s.get("review")
    if step == "review" and review is not None:
        # what a plain yes / no means depends on the question, so each answer says which one it is
        r = review
        resolutions = r.get("resolutions") or []
        if "upload" in resolutions:
            add(
                "yes_have_it",
                "answers YES (yes, haan, ji, I have it): has the ticket or bill (or more bills) and will attach it now",
            )
        if not r.get("blocking") and "confirm" in resolutions:
            if r.get("oddPlace"):
                description = (
                    'says the value shown is in fact correct, in words like "it is correct" or "keep it". An answer '
                    'that names a place (like "Mumbai") is NOT this: it is "edit"'
                )
            elif r.get("yes") == "confirm":
                description = "answers YES: it is right and part of this tour, keep it as it is"
            elif r.get("yes") == "details":
                description = (
                    "answers NO (no, none, nahi, no leave): nothing to add, keep the claim as it is. A plain "
                    '"yes" is NOT this'
                )
            else:
                description = (
                    "answers NO: does not have it, it was not needed (came back or went by office car, own car, a "
                    "cab or a colleague with no ticket), nothing to claim, or lost it: keep the claim as it is"
                )
            add("yes_keep_it" if r.get("yes") == "confirm" else "no_keep_as_is", description)
        if r.get("no") == "remove":
            add(
                "no_remove_it",
                "answers NO: it is not part of this tour, a mistake, or not theirs: remove that entry",
            )
        if r.get("blocking") or "form" in resolutions:
            add(
                "fixed_it",
                'says it is now fixed, done or entered on the claim form ("done", "ho gaya", "corrected it")',
            )
            add(
                "fix_on_form_later",
                'will do it on the claim form ("I\'ll fill it on the form", "later on the form", "ok I\'ll enter '
                'it", "will do"), or wants to leave it for now',
            )
    if step == "review" and review is not None and review.get("oddPlace"):
        add(
            "edit",
            'names the right place (even a single word like "Mumbai" or "Delhi airport"): an edit of that same '
            'journey\'s "from" or "to" (or that stay\'s location), with the row number from the question. Never a '
            "new journey",
        )
    elif step == "review" and review is not None and review.get("newJourney"):
        add(
            "edit",
            "describes the ride or journey asked about (when, from where to where, how): it is a NEW journey, so "
            'every edit uses row 0 and never an existing journey\'s number. Questions about it ("which bills?", '
            '"what is this?") are "talk", not "no"',
        )
    else:
        add(
            "edit",
            "gives or corrects claim details (journeys, stays, leave), or asks to remove an entry: list them in "
            '"edits"',
        )
    # inside a claim form the chat only reads bills into the claim: it does not go through what is left, or submit
    if not in_form:
        add("show_progress", "what is left, what is pending, any errors, is the claim ready")
    else:
        add(
            "ask_if_ready",
            "asks whether anything is missing, pending or left, whether the claim is complete, correct or ready, "
            "what else to upload or add, or wants to submit or check it",
        )
    add(
        "show_claim",
        "wants to see the claim being built in this chat (its journeys, stays and leave); not for questions about "
        "other claims, tours or drafts",
    )
    add("explain_da", "asks how DA (daily allowance) is worked out or its rules (not drafts, not bills)")
    add("tour_info", "asks which tour this claim is for")
    add("upload_help", "wants to upload or asks how to attach bills")
    add("save_draft", "save the claim as a draft")
    if not s.get("resetAsk"):
        add(
            "start_over",
            "clear this chat's whole claim (journeys, stays, leave) and start again; never for deleting drafts, "
            "other claims or anything else",
        )
    if s.get("canUndo"):
        add("undo", "undo the last change")
    if not in_form:
        add("fill_form", "wants the claim form filled or opened, or says they are done")
        if s.get("hasTour") and step not in ("confirmTour", "pickTour", "splitTours", "bulkDrafts", "uploadChoice"):
            add("change_tour", "the claim is for a different tour")
    if s.get("hasSavedTours"):
        add("open_saved_claim", "wants to continue one of the saved claims listed in CONTEXT", TOUR_ID_ARG)
    return actions


# ─── Within Jurisdiction ──────────────────────────────────────────────────────

WITHIN_DOMAIN: Final = (
    "This chat helps a DFCCIL employee fill a Within Jurisdiction tour claim on the claim form. The claim is a "
    "list of tours; each tour has a main journey (leg 0) and optional legs. The employee uploads their monthly "
    "tour plan, which creates one tour per date; missing details (purpose, places, times, travel mode, vehicle "
    "type and distance for a personal vehicle) are collected one at a time in the chat. A tour must be ended "
    "before its DA is worked out; the chat ends all tours at once when nothing is missing. The chat can check the "
    "claim, save a draft, and submit once the employee agrees to the declarations.\n"
    "What you can do: read the tour plan, fill in and correct purpose, places, times, travel mode, vehicle type "
    "and distance, end the tours, say what is left, show the tours, save a draft, check and submit, undo the last "
    "change and start over. You cannot change dates, add or delete tours or legs, enter amounts, add hotel stays "
    "or travel bills: those are done on the form (Add Accommodation, Add Travel Costs), which works out DA and "
    "totals. Ended tours cannot be changed from the chat.\n"
    "\n"
    "DA RULES:\n"
    f"{WITHIN_DA_RULES}"
)


class WithinChatState(TypedDict):
    """assistantActions.ts › WithinChatState."""

    yesNo: NotRequired[YesNo | None]
    resetAsk: bool
    declare: bool  # the declarations were read out and "submit now?" is open
    pendingQuestion: NotRequired[str | None]  # the question for one missing detail
    canUndo: bool


def within_open_question(s: WithinChatState) -> str | None:
    """assistantActions.ts › withinOpenQuestion."""
    if s.get("yesNo") is not None:
        return s["yesNo"]["question"]  # type: ignore[index]
    if s.get("resetAsk"):
        return "Shall I clear every tour on the form and start over?"
    if s.get("declare"):
        return "Do you agree to all the declarations and want the claim submitted now?"
    return s.get("pendingQuestion")


def within_actions(s: WithinChatState) -> list[ChatAction]:
    """assistantActions.ts › withinActions."""
    actions: list[ChatAction] = []

    def add(name: str, description: str, args: str | None = None) -> None:
        actions.append(ChatAction(name, description, args))

    add("talk", "a question, greeting, thanks, complaint, help request, anything else, or not sure")
    yes_no = s.get("yesNo")
    if yes_no is not None:
        add("yes", f'agrees ("{yes_no["yesLabel"]}")')
        add("no", f'declines ("{yes_no["noLabel"]}")')
    if s.get("resetAsk"):
        add("confirm_start_over", "yes, clear every tour and start over")
        add("keep_tours", "no, keep the tours as they are")
    if s.get("declare"):
        add("agree_and_submit", "a plain yes: agrees to all the declarations and wants the claim submitted now")
        add("dont_submit_yet", "not yet, does not agree, or wants to change something first")
    if s.get("pendingQuestion"):
        add(
            "answer_question",
            'the message IS the answer to the open question (never "yes", "no", a greeting or a question). Put it in '
            '"value" in the form the question needs: a distance as a number of km; a vehicle as "Two Wheeler" '
            '(bike, scooter, scooty, motorcycle) or "Four Wheeler" (car); a mode as one of Rail, Public Transport, '
            "Public Transport Beyond Entitled, Company Provided Vehicle (office or company vehicle), Personal "
            "Vehicle; a time as HH:mm; a place or purpose as the employee wrote it",
            '{"value": "<the answer>"}',
        )
        add("skip_question", "skip the open question (fill it on the form, does not know, never mind)")
    add("edit", 'gives or corrects tour details other than the open question: list them in "edits"')
    add("show_progress", "what is left, what is pending, any errors, is the claim ready")
    add("show_tours", "wants to see the tours or the claim")
    add("end_tours", "end the tours (mark them ended)")
    add("check_and_submit", "wants to submit, or says the claim is done or ready")
    add("hold_submit", "says not to submit yet")
    add("save_draft", "save the claim as a draft")
    add("explain_da", "asks how DA (daily allowance) is worked out")
    add("upload_help", "wants to upload a tour plan or asks how")
    if not s.get("resetAsk"):
        add(
            "start_over",
            "clear every tour on the form and start again; never for adding, deleting or changing one tour",
        )
    if s.get("canUndo"):
        add("undo", "undo the last change")
    return actions


# ─── Notes the chats send the AI model for common actions (shared, so the evaluation sends the same) ──────────

# "talk": the employee's message needs a direct answer; nothing in the claim changed.
TALK_NOTE: Final = (
    "Nothing in the claim was changed by this message. Respond to what the employee said: answer a question from "
    "the claim, the tour, the page and the rules you know; if it is about anything other than this tour claim, say "
    "you can only help with the claim; if you cannot tell what they mean, ask one short question."
)

# "talk" inside a claim form: the chat only reads bills into the claim, and never says what it is missing.
FORM_TALK_NOTE: Final = (
    f"{TALK_NOTE} Inside this claim form you only read the employee's bills into the claim: never point out what "
    "the claim is missing, and never suggest what to upload, add or fix. Submitting is done on the claim form."
)

# inside a claim form, "is anything missing?", "can I submit?": always this, never the model's own view of the claim
FORM_READY_REPLY: Final = "I only read your bills into this claim. You can check it and submit it on the claim form."

OUTSIDE_START_OVER_ASK: Final = (
    "The employee asked to start over. NOTHING HAS BEEN CLEARED YET. If they confirm, every journey, stay and leave "
    'entry in this claim will be cleared (they can say "undo" afterwards). Ask whether they want to clear the claim '
    "and start over."
)

WITHIN_START_OVER_ASK: Final = (
    "The employee asked to start over. NOTHING HAS BEEN CLEARED YET. If they confirm, every tour on the form will "
    'be cleared (they can say "undo" afterwards). Ask whether they want to clear the tours and start over.'
)
