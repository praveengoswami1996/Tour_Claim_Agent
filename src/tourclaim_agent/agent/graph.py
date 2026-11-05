"""The conversation workflow (LangGraph).

One graph invocation = one employee message. State is checkpointed per workflow
(thread), so a conversation can be resumed later (FRS STA-01/02). Per-request
dependencies (the user's API clients, identity, LLM) are passed as runtime context and
are never written to the checkpoint, so tokens never reach persisted state (IDN-08).

Journeys: list tours, claimable tours, pending actions and tour status (J4), plus
preparing an outside-jurisdiction claim (J1, see claim_flow.py). Other write goals are
recognised and answered with a pointer to the app screen.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Checkpointer

from tourclaim_agent.agent import bill_flow, claim_flow
from tourclaim_agent.agent.core import AgentContext, AgentState, intent_of
from tourclaim_agent.agent.core import backend_problem as _backend_problem
from tourclaim_agent.agent.core import reply as _reply
from tourclaim_agent.agent.events import (
    ChoiceOption,
    ChoicesEvent,
    StepCard,
    TextEvent,
    TourCard,
    TourListEvent,
    TourStatusEvent,
)
from tourclaim_agent.agent.resolver import has_reference, resolve_tours, tour_label
from tourclaim_agent.clients.errors import BackendError
from tourclaim_agent.domain.models import TourSummary
from tourclaim_agent.domain.statuses import Holder
from tourclaim_agent.llm.intent import Goal, Intent, IntentContext, TourReference

log = logging.getLogger(__name__)

__all__ = ["AgentContext", "AgentState", "build_graph"]

MAX_LISTED_TOURS = 10
MAX_CHOICES = 5
MAX_STEPS_SHOWN = 5


# --------------------------------------------------------------------------- nodes


async def understand(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    message = next((m["text"] for m in reversed(state.get("messages", [])) if m["role"] == "user"), "")
    pending = state.get("pending_choice") or {}
    options: list[dict[str, Any]] = pending.get("options", [])
    intent = await ctx.intents.extract(
        message,
        IntentContext(pending_options=[o["label"] for o in options], today_iso=ctx.today.isoformat()),
    )
    chosen_choice: dict[str, Any] | None = None
    if intent.goal is Goal.SELECT_OPTION and intent.selection and options:
        chosen = options[intent.selection - 1]
        intent = Intent(goal=Goal(pending["goal"]), tour=TourReference(tour_id=chosen["tour_id"]))
        chosen_choice = pending
    elif intent.goal in (Goal.SELECT_OPTION, Goal.ASSIGN_BILLS):
        # ASSIGN_BILLS only ever comes from answering the "which tour?" question.
        intent = Intent(goal=Goal.UNKNOWN)
    elif intent.goal is Goal.TOUR_STATUS and not has_reference(intent.tour) and state.get("focus_tour_id"):
        intent.tour = TourReference(tour_id=state["focus_tour_id"])
    if state.get("claim") and (
        intent.goal is Goal.UNKNOWN or (intent.goal is Goal.RAISE_CLAIM and not has_reference(intent.tour))
    ):
        # While a claim is being prepared, vague messages are about that claim.
        intent = Intent(goal=Goal.CLAIM_REVIEW)
    # An unanswered "which tour are these bills for?" stays open: those bills are still waiting.
    keep = pending if pending.get("goal") == Goal.ASSIGN_BILLS.value and chosen_choice is None else None
    return {
        "intent": intent.model_dump(mode="json"),
        "pending_choice": keep,
        "chosen_choice": chosen_choice,
        "events": [],
    }


def start_route(state: AgentState) -> str:
    return "register_bills" if state.get("new_bills") else "process_bills"


def after_bills(state: AgentState) -> str:
    return "end" if state.get("turn") == "check" else "understand"


async def finish(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Ask a question raised by bill processing during this turn, unless the reply asked its own."""
    queued = state.get("queued_choice")
    if queued and not state.get("pending_choice"):
        return {"pending_choice": queued, "queued_choice": None}
    return {"queued_choice": None}


def route(state: AgentState) -> str:
    goal = Goal((state.get("intent") or {}).get("goal", Goal.UNKNOWN))
    return {
        Goal.LIST_TOURS: "list_tours",
        Goal.CLAIMABLE_TOURS: "claimable_tours",
        Goal.PENDING_ACTIONS: "pending_actions",
        Goal.TOUR_STATUS: "tour_status",
        Goal.RAISE_CLAIM: "start_claim",
        Goal.CLAIM_REVIEW: "review_claim",
        Goal.SAVE_DRAFT: "save_draft",
        Goal.CANCEL_CLAIM: "cancel_claim",
        Goal.ASSIGN_BILLS: "assign_bills",
        Goal.BILL_STATUS: "bill_status",
        Goal.ANSWER_CLARIFICATION: "not_yet_available",
        Goal.EDIT_CLAIM: "not_yet_available",
        Goal.SUPPLEMENTARY_CLAIM: "not_yet_available",
        Goal.POLICY_QUESTION: "policy_question",
        Goal.OUT_OF_SCOPE: "out_of_scope",
    }.get(goal, "help")


_intent = intent_of


async def list_tours(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    try:
        tours = await ctx.tours.list_all_tours(ctx.employee.emp_id)
    except BackendError as exc:
        return _backend_problem(exc)
    ref = _intent(state).tour
    if has_reference(ref):
        tours = resolve_tours(tours, ref)
    if not tours:
        return _reply(TextEvent(text="I couldn't find any tours matching that."))
    needing_action = sum(1 for t in tours if t.status_info.action_needed)
    shown = tours[:MAX_LISTED_TOURS]
    summary = f"You have {len(tours)} tour{'s' if len(tours) != 1 else ''}"
    summary += f"; {needing_action} need{'s' if needing_action == 1 else ''} your action." if needing_action else "."
    if len(tours) > len(shown):
        summary += f" Showing the {len(shown)} most recent."
    return _reply(
        TextEvent(text=summary),
        TourListEvent(title="Your tours", tours=[TourCard.from_tour(t) for t in shown]),
    )


async def claimable_tours(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    try:
        tours = await ctx.tours.list_claimable_tours(ctx.employee.emp_id)
    except BackendError as exc:
        return _backend_problem(exc)
    if not tours:
        return _reply(TextEvent(text="You have no approved tours waiting for a claim."))
    return _reply(
        TextEvent(
            text=f"{len(tours)} approved tour{'s are' if len(tours) != 1 else ' is'} ready for a claim. "
            'Say "start a claim for tour <number>" and I\'ll help you prepare it.'
        ),
        TourListEvent(title="Ready to claim", tours=[TourCard.from_tour(t) for t in tours[:MAX_LISTED_TOURS]]),
    )


async def pending_actions(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    try:
        tours = await ctx.tours.list_all_tours(ctx.employee.emp_id)
        claimable = await ctx.tours.list_claimable_tours(ctx.employee.emp_id)
    except BackendError as exc:
        return _backend_problem(exc)
    needing = [t for t in tours if t.status_info.action_needed]
    events: list[Any] = []
    if needing:
        events.append(TextEvent(text=f"{len(needing)} tour{'s need' if len(needing) != 1 else ' needs'} your action."))
        events.append(TourListEvent(title="Needs your action", tours=[TourCard.from_tour(t) for t in needing]))
    if claimable:
        events.append(
            TextEvent(
                text=f"{len(claimable)} approved tour{'s are' if len(claimable) != 1 else ' is'} "
                "waiting for you to raise a claim."
            )
        )
        events.append(TourListEvent(title="Ready to claim", tours=[TourCard.from_tour(t) for t in claimable]))
    if not events:
        events.append(TextEvent(text="Nothing needs your action right now."))
    return _reply(*events)


def _status_sentence(tour: TourSummary) -> str:
    info = tour.status_info
    when = f", starting {tour.start_date.strftime('%d %b %Y')}" if tour.start_date else ""
    sentence = f"Tour {tour.tour_id} ({tour.route}{when}) is {info.employee_text}."
    if tour.recipient_name and info.holder in (Holder.APPROVER, Holder.FINANCE):
        sentence += f" It is currently with {tour.recipient_name}."
    if info.action_needed and tour.send_back_remark:
        sentence += f" Remarks so far: {tour.send_back_remark.strip()}"
    return sentence


async def tour_status(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    ref = _intent(state).tour
    try:
        tours = await ctx.tours.list_all_tours(ctx.employee.emp_id)
    except BackendError as exc:
        return _backend_problem(exc)
    if not tours:
        return _reply(TextEvent(text="You don't have any tours yet."))

    if ref.tour_id is not None:
        candidates = resolve_tours(tours, ref)
        if not candidates:
            return _reply(TextEvent(text=f"I couldn't find tour {ref.tour_id} among your tours."))
    elif has_reference(ref):
        candidates = resolve_tours(tours, ref)
        if not candidates:
            return _reply(TextEvent(text="I couldn't find a tour matching that. Which tour do you mean?"))
    else:
        candidates = tours

    if len(candidates) > 1:
        offered = candidates[:MAX_CHOICES]
        options = [ChoiceOption(number=i, tour_id=t.tour_id, label=tour_label(t)) for i, t in enumerate(offered, 1)]
        return _reply(
            ChoicesEvent(prompt="Which tour do you mean?", options=options),
            TextEvent(text="Which tour do you mean? Reply with its number."),
            pending_choice={"goal": Goal.TOUR_STATUS.value, "options": [o.model_dump() for o in options]},
        )

    tour = candidates[0]  # From the employee's own list, so ownership is already established.
    try:
        trajectory = await ctx.tours.get_trajectory(tour.tour_id)
    except BackendError as exc:
        return _backend_problem(exc)
    steps = [StepCard.from_step(s) for s in (trajectory.steps if trajectory else [])][-MAX_STEPS_SHOWN:]
    return _reply(
        TextEvent(text=_status_sentence(tour)),
        TourStatusEvent(tour=TourCard.from_tour(tour), steps=steps),
        focus_tour_id=tour.tour_id,
    )


_NOT_YET = {
    Goal.ANSWER_CLARIFICATION: "Replying to finance through me is not switched on yet. "
    "Please answer from the Clarification tab in the app. Ask me 'what is pending' to see which tours need a reply.",
    Goal.EDIT_CLAIM: "Editing claims through me is not switched on yet. Please use the claim screen in the app.",
    Goal.SUPPLEMENTARY_CLAIM: "Supplementary claims through me are not switched on yet. "
    "Please use the claim screen in the app.",
}


async def not_yet_available(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    goal = _intent(state).goal
    return _reply(TextEvent(text=_NOT_YET.get(goal, "That is not available yet.")))


async def policy_question(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    # FRS POL-01/02: answer only from an approved policy source. None is connected yet.
    return _reply(
        TextEvent(
            text="I can't answer policy or entitlement questions yet, because I'm not connected to an approved "
            "policy source. Please check with your unit's finance or HR team."
        )
    )


async def out_of_scope(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    return _reply(
        TextEvent(
            text="I can only help with your own tours and claims. Approvals and actions for other employees "
            "are done from the approver and finance screens in the app."
        )
    )


async def help_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    name = ctx.employee.name
    greeting = f"Hello{', ' + name if name else ''}. " if _intent(state).goal is Goal.GREETING else ""
    bills = (
        "Upload your tickets and hotel bills and I'll find the tour and save them into its draft."
        if ctx.auto_save
        else "Upload your tickets and hotel bills and I'll find the tour they belong to."
    )
    return _reply(
        TextEvent(
            text=greeting + bills + " Or ask about your tours, for example:\n"
            '• "Show my tours"\n'
            '• "What is pending for me?"\n'
            '• "Which tours can I claim?"\n'
            '• "Where is my Mumbai claim?" or "Status of tour 1234"\n'
            '• "Are my bills read?"'
        )
    )


# --------------------------------------------------------------------------- graph

_NODES = {
    "list_tours": list_tours,
    "claimable_tours": claimable_tours,
    "pending_actions": pending_actions,
    "tour_status": tour_status,
    "start_claim": claim_flow.start_claim,
    "review_claim": claim_flow.review_claim,
    "save_draft": claim_flow.save_draft,
    "cancel_claim": claim_flow.cancel_claim,
    "assign_bills": bill_flow.assign_bills,
    "bill_status": bill_flow.bill_status_node,
    "not_yet_available": not_yet_available,
    "policy_question": policy_question,
    "out_of_scope": out_of_scope,
    "help": help_node,
}


def build_graph(checkpointer: Checkpointer | None = None):
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("understand", understand)
    graph.add_node("register_bills", bill_flow.register_bills)
    graph.add_node("process_bills", bill_flow.process_bills)
    graph.add_node("finish", finish)
    for name, node in _NODES.items():
        graph.add_node(name, node)

    graph.add_conditional_edges(
        START, start_route, {"process_bills": "process_bills", "register_bills": "register_bills"}
    )
    graph.add_conditional_edges("process_bills", after_bills, {"understand": "understand", "end": END})
    graph.add_conditional_edges("understand", route, {name: name for name in _NODES})
    graph.add_edge("register_bills", END)
    for name in _NODES:
        graph.add_edge(name, "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)
