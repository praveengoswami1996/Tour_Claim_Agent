"""assistant_actions.py: port of Tour_React src/lib/claimChatbot/assistantActions.ts.

The full comparison with the TypeScript (every combination of the chat-state fields, run under Node) is in
tools/parity_diff/actions_gen.py; these tests pin the strings and the main shapes of the lists.
"""

import json
from pathlib import Path

import pytest

from tourclaim_agent.claimchat.ai import assistant_actions as aa
from tourclaim_agent.claimchat.ai.ai_assistant import ChatAction, prompts

CASES = Path(__file__).resolve().parents[2] / "Project-Context" / "agent" / "old_assistant_parity" / "C_eval_cases.json"


def outside(**o):
    return {
        "step": "start",
        "inForm": False,
        "noTours": False,
        "resetAsk": False,
        "hasTour": False,
        "hasBulk": False,
        "hasSplit": False,
        "hasMissing": False,
        "canUndo": False,
        "hasSavedTours": False,
        **o,
    }


def within(**o):
    return {"resetAsk": False, "declare": False, "canUndo": False, **o}


def names(actions):
    return [a.name for a in actions]


@pytest.mark.parametrize(
    "name",
    [
        "OUTSIDE_DOMAIN",
        "WITHIN_DOMAIN",
        "TALK_NOTE",
        "FORM_TALK_NOTE",
        "FORM_READY_REPLY",
        "OUTSIDE_START_OVER_ASK",
        "WITHIN_START_OVER_ASK",
        "OUTSIDE_STEP_QUESTIONS",
    ],
)
def test_strings_are_the_ones_the_typescript_produced(name):
    assert getattr(aa, name) == prompts()[name]


def test_domains_end_with_the_da_rules():
    assert aa.OUTSIDE_DOMAIN.endswith("\n\nDA RULES:\n" + prompts()["DA_RULES"])
    assert aa.WITHIN_DOMAIN.endswith("\n\nDA RULES:\n" + prompts()["WITHIN_DA_RULES"])


def test_no_tours_offers_only_talk():
    assert names(
        aa.outside_actions(outside(noTours=True, yesNo={"question": "q", "yesLabel": "Y", "noLabel": "N"}))
    ) == ["talk"]


def test_talk_is_always_first_and_described_the_same():
    talk = ChatAction("talk", "a question, greeting, thanks, complaint, help request, anything else, or not sure")
    assert aa.outside_actions(outside())[0] == talk
    assert aa.within_actions(within())[0] == talk


def test_confirm_tour_on_the_raise_claim_page():
    acts = aa.outside_actions(outside(step="confirmTour", suggestedTourId=4521))
    assert names(acts)[:4] == ["talk", "confirm_tour", "choose_another_tour", "pick_tour"]
    assert acts[1].description == "yes, the claim is for the suggested tour #4521"
    assert acts[3].args == '{"tourId": <number>}'
    # suggestedTourId 0 is still a tour (!= null in the TypeScript)
    assert "confirm_tour" in names(aa.outside_actions(outside(step="confirmTour", suggestedTourId=0)))
    assert "confirm_tour" not in names(aa.outside_actions(outside(step="confirmTour", suggestedTourId=None)))
    # inside the form the tour is fixed
    assert "pick_tour" not in names(aa.outside_actions(outside(step="confirmTour", suggestedTourId=4521, inForm=True)))


def test_yes_no_labels_go_into_the_descriptions():
    acts = aa.outside_actions(outside(yesNo={"question": "Save?", "yesLabel": "Yes, save it", "noLabel": "Not now"}))
    assert acts[1] == ChatAction("yes", 'agrees ("Yes, save it")')
    assert acts[2] == ChatAction("no", 'declines ("Not now")')
    assert aa.outside_open_question(outside(yesNo={"question": "Save?", "yesLabel": "Y", "noLabel": "N"})) == "Save?"


def test_the_full_list_on_the_raise_claim_page():
    assert names(aa.outside_actions(outside(step="confirm", hasTour=True, canUndo=True, hasSavedTours=True))) == [
        "talk",
        "edit",
        "show_progress",
        "show_claim",
        "explain_da",
        "tour_info",
        "upload_help",
        "save_draft",
        "start_over",
        "undo",
        "fill_form",
        "change_tour",
        "open_saved_claim",
    ]


def test_inside_the_form_the_chat_only_reads_bills():
    acts = names(aa.outside_actions(outside(step="submit", inForm=True, hasTour=True)))
    assert acts[:3] == ["talk", "confirm_submit", "dont_submit_yet"]
    assert "ask_if_ready" in acts
    assert "show_progress" not in acts
    assert "fill_form" not in acts
    assert "change_tour" not in acts


def test_start_over_is_not_offered_while_it_is_asked():
    acts = names(aa.outside_actions(outside(resetAsk=True)))
    assert acts[1:3] == ["confirm_start_over", "keep_claim"]
    assert "start_over" not in acts
    assert aa.outside_open_question(outside(resetAsk=True)) == "Shall I clear the claim and start over?"


def review(**r):
    return {"question": "Do you have the return ticket?", "blocking": False, "resolutions": ["upload", "confirm"], **r}


def test_review_answers_depend_on_the_question():
    acts = aa.outside_actions(outside(step="review", inForm=True, review=review()))
    assert names(acts)[:3] == ["talk", "yes_have_it", "no_keep_as_is"]
    assert acts[2].description.startswith("answers NO: does not have it")
    keep = aa.outside_actions(outside(step="review", inForm=True, review=review(yes="confirm", no="remove")))
    assert names(keep)[:4] == ["talk", "yes_have_it", "yes_keep_it", "no_remove_it"]
    leave = aa.outside_actions(
        outside(step="review", inForm=True, review=review(resolutions=["confirm"], yes="details"))
    )
    assert leave[1].description.endswith('A plain "yes" is NOT this')
    blocking = names(aa.outside_actions(outside(step="review", inForm=True, review=review(blocking=True))))
    assert "no_keep_as_is" not in blocking
    assert blocking[2:4] == ["fixed_it", "fix_on_form_later"]
    assert aa.outside_open_question(outside(step="review", review=review())) == "Do you have the return ticket?"


def test_review_edit_wording():
    odd = aa.outside_actions(outside(step="review", inForm=True, review=review(oddPlace=True)))
    edit = next(a for a in odd if a.name == "edit")
    assert edit.description.startswith("names the right place")
    assert next(a for a in odd if a.name == "no_keep_as_is").description.startswith("says the value shown is in fact")
    new = aa.outside_actions(outside(step="review", inForm=True, review=review(newJourney=True)))
    assert next(a for a in new if a.name == "edit").description.startswith("describes the ride or journey")
    plain = aa.outside_actions(outside(step="confirm"))
    assert next(a for a in plain if a.name == "edit").description == (
        'gives or corrects claim details (journeys, stays, leave), or asks to remove an entry: list them in "edits"'
    )


def test_step_questions():
    assert aa.outside_open_question(outside(step="moreFiles")) == "Do you have more tickets or bills to upload?"
    assert aa.outside_open_question(outside(step="confirm")) is None
    assert aa.outside_open_question(outside(step="details")) is None
    assert aa.outside_open_question(outside(step="details", pendingQuestion="What time?")) == "What time?"


def test_within_lists_and_questions():
    assert names(aa.within_actions(within(canUndo=True))) == [
        "talk",
        "edit",
        "show_progress",
        "show_tours",
        "end_tours",
        "check_and_submit",
        "hold_submit",
        "save_draft",
        "explain_da",
        "upload_help",
        "start_over",
        "undo",
    ]
    asked = aa.within_actions(within(pendingQuestion="How many km?"))
    assert names(asked)[1:3] == ["answer_question", "skip_question"]
    assert asked[1].args == '{"value": "<the answer>"}'
    assert names(aa.within_actions(within(declare=True)))[1:3] == ["agree_and_submit", "dont_submit_yet"]
    assert aa.within_open_question(within(declare=True)) == (
        "Do you agree to all the declarations and want the claim submitted now?"
    )
    assert aa.within_open_question(within(resetAsk=True, declare=True)) == (
        "Shall I clear every tour on the form and start over?"
    )
    assert aa.within_open_question(within(pendingQuestion="How many km?")) == "How many km?"


def test_the_lists_recorded_for_every_eval_case():
    """C_eval_cases.json recorded the actions the TypeScript offered for each understand case's state."""
    cases = [c for c in json.loads(CASES.read_text(encoding="utf-8")) if c["kind"] == "understand"]
    assert len(cases) == 589
    for c in cases:
        built = aa.outside_actions(c["state"]) if c["assistant"] == "outside" else aa.within_actions(c["state"])
        recorded = [ChatAction(a["name"], a["description"], a.get("args")) for a in c["actions"]]
        assert built == recorded, c["id"]
        question = (
            aa.outside_open_question(c["state"]) if c["assistant"] == "outside" else aa.within_open_question(c["state"])
        )
        assert question == c.get("openQuestion"), c["id"]
