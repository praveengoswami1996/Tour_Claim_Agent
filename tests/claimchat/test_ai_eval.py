"""The AI evaluation (tools/ai_eval), offline: the gateway messages and the checks match the recorded cases.

C_eval_cases.json recorded, for each of the 749 cases of Tour_React scripts/ai-eval, the exact message the
TypeScript sent to the gateway (understand cases), which actions pass the check, and a readable form of each narrate
check. The key parity check: understand_message() rebuilds every recorded gateway message character for character.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "ai_eval"))

import cases as eval_cases  # noqa: E402
import run_eval  # noqa: E402
from jsregex import R, js_split_ws  # noqa: E402

from tourclaim_agent.claimchat.ai.ai_assistant import Understood, prompts, understand_message  # noqa: E402

RECORDED = json.loads(run_eval.CASES_FILE.read_text(encoding="utf-8"))
UNDERSTAND = [c for c in RECORDED if c["kind"] == "understand"]


def test_case_counts():
    assert len(RECORDED) == 749
    assert len(UNDERSTAND) == 589


def test_every_gateway_message_is_rebuilt_exactly():
    mismatched = []
    for c in UNDERSTAND:
        a = run_eval.understand_args(c)
        built = understand_message(
            a["message"],
            a["actions"],
            open_question=a["open_question"],
            context=a["context"],
            recent_chat=a["recent_chat"],
        )
        if built != c["userMessage"]:
            mismatched.append(c["id"])
    assert mismatched == []


def test_system_prompts():
    assert run_eval.prompts_for("outside") == {
        "understand": prompts()["understand_outside"],
        "narrate": prompts()["narrate_outside"],
    }
    assert run_eval.prompts_for("within")["understand"] == prompts()["understand_within"]


def test_checks_line_up_with_the_recorded_cases():
    pairs = eval_cases.attach_checks(RECORDED)
    assert len(pairs) == 749


def test_checks_pass_the_same_plain_actions_and_tour_ids():
    """passingPlainActions / passingTourIds were worked out by the TypeScript checks."""
    for c, k in eval_cases.attach_checks(RECORDED):
        if c["kind"] != "understand":
            continue
        offered = [a["name"] for a in c["actions"]]
        plain = [n for n in offered if k.check(Understood(n, {}, [])) is None]
        assert plain == c["passingPlainActions"], c["id"]
        ids = [
            t
            for t in (4521, 4522, 4530, 4533)
            if any(
                a in offered and k.check(Understood(a, {"tourId": t}, [])) is None
                for a in ("pick_tour", "open_saved_claim")
            )
        ]
        assert ids == c["passingTourIds"], c["id"]


def test_narrate_checks_read_as_recorded():
    for c, k in eval_cases.attach_checks(RECORDED):
        if c["kind"] == "narrate":
            assert k.check.desc == c["check"], c["id"]


def by_id(case_id):
    return next(k for c, k in eval_cases.attach_checks(RECORDED) if c["id"] == case_id)


def test_edit_checks_run_the_claim_validation():
    check = by_id("corrections-304").check  # "journey 2 arrives at 6 pm"
    good = Understood("edit", {}, [{"section": "transport", "row": 2, "field": "arrivalTime", "value": "18:00"}])
    assert check(good) is None
    wrong = Understood("edit", {}, [{"section": "transport", "row": 2, "field": "arrivalTime", "value": "17:00"}])
    assert check(wrong) == 'journey 2 arrival: expected "18:00", got "17:00"'
    assert check(Understood("talk")) == "expected edit, got talk"


def test_within_answer_checks_use_answer_gap():
    case = next(c for c in RECORDED if c["group"] == "within-answer" and c["message"] == "24 km")
    check = by_id(case["id"]).check
    assert check(Understood("answer_question", {"value": "24"})) is None
    assert check(Understood("answer_question", {"value": "25"})) == "distance not 24"
    assert check(Understood("answer_question", {"value": "far"})).startswith('the answer "far" was refused: ')


def test_asks():
    assert eval_cases.asks("Is that right?") is None
    assert eval_cases.asks("Is that right?\n• Tap [Yes] below") is None
    assert eval_cases.asks("Done.") == "does not end by asking the question"


def test_reply_basics():
    assert run_eval.reply_basics("  ") == "empty reply"
    assert run_eval.reply_basics("EMPLOYEE SAID: hi") == "leaked the message format"
    assert run_eval.reply_basics('Here:\n{"a": 1}') == "JSON or code in the reply"
    assert run_eval.reply_basics("x" * 2501) == "too long (2501 chars)"
    assert run_eval.reply_basics("Hello!") is None


@pytest.mark.parametrize(
    ("source", "flags", "text", "matches"),
    [
        (r"\b10\b|ten", "i", "You have 10 tours", True),
        (r"\?\s*\**\s*$", "", "ok?\n", True),
        (r"\?\s*\**\s*$", "", "ok?\nmore", False),
        ("a$", "", "a\n", False),  # JavaScript $ is the very end (Python's would match before a final newline)
        ("a.b", "", "a\nb", False),
        ("a.b", "", "a\rb", False),  # JavaScript . stops at \r too
        (r"\d", "", "٣", False),  # \d is ASCII only
        (r"a\sb", "", "a b", True),  # \s is JavaScript's whitespace
        (r'```|^\s*[{[]\s*"', "m", 'x\n["a"]', True),
        (r"TJ\/45\/2026", "i", "tj/45/2026", True),
    ],
)
def test_js_regex(source, flags, text, matches):
    assert R(source, flags).test(text) is matches


def test_js_regex_text_and_split():
    assert str(R("draft", "i")) == "/draft/i"
    assert js_split_ws(" a b  c") == ["", "a", "b", "c"]


def test_js_slice_counts_utf16_units():
    assert run_eval.js_slice("👍abc", 3) == "👍a"
    assert run_eval.js_len("👍") == 2


def test_cases_are_chosen_like_eval_only():
    chosen = run_eval.load_cases("^corrections", None, 2)
    assert [c["id"] for c, _ in chosen] == ["corrections-304", "corrections-305"]
    assert all(c["kind"] == "narrate" for c, _ in run_eval.load_cases(None, "narrate", None))
