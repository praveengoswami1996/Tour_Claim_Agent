"""Runs the Claim Assistant AI evaluation against the live gateway: port of Tour_React scripts/ai-eval/run.ts.

The cases are the 749 recorded in Project-Context/agent/old_assistant_parity/C_eval_cases.json (understand and
narrate); each is sent through the agent's own AI layer (claimchat.ai: understand(), narrate(), the action lists
and prompts) and graded by the checks ported from cases.ts (tools/ai_eval/cases.py), exactly as run.ts graded them.

Usage (from the repository root):
    .venv\\Scripts\\python.exe tools\\ai_eval\\run_eval.py [--limit N] [--kind understand|narrate] [--only REGEX]
        [--concurrency N] [--out ai-eval-report]

The gateway base URL and key come from the agent's settings (TCA_LLM_BASE_URL / TCA_LLM_API_KEY in .env). The key
is never printed. --out names the report: <out>.md and <out>.json (a trailing .md or .json is dropped).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
from cases import CaseCheck, attach_checks  # noqa: E402
from jsregex import R  # noqa: E402

from tourclaim_agent.claimchat.ai.ai_assistant import AssistantEvent, Understood, narrate, prompts, understand  # noqa: E402
from tourclaim_agent.claimchat.ai.assistant_actions import (  # noqa: E402
    outside_actions,
    outside_open_question,
    within_actions,
    within_open_question,
)
from tourclaim_agent.claimchat.ai.llm_client import LlmClient  # noqa: E402

CASES_FILE = ROOT / "Project-Context" / "agent" / "old_assistant_parity" / "C_eval_cases.json"
ATTEMPTS = 5
TIMEOUT_SECONDS = 90.0


def prompts_for(assistant: str) -> dict[str, str]:
    """run.ts › PROMPTS: understandPrompt(CLAIM_ / WITHIN_EDIT_FORMAT), narratorPrompt(OUTSIDE_ / WITHIN_DOMAIN)."""
    p = prompts()
    return {"understand": p[f"understand_{assistant}"], "narrate": p[f"narrate_{assistant}"]}


_LEAK = R(r"WHAT HAPPENED|EMPLOYEE SAID:|OPEN QUESTION:|BUTTONS:|\bdata:\s*[{[]")
_CODE = R(r'```|^\s*[{[]\s*"', "m")


def js_len(text: str) -> int:
    """text.length (UTF-16 units)"""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def js_slice(text: str, end: int) -> str:
    """text.slice(0, end) (UTF-16 units)"""
    units = text.encode("utf-16-le", "surrogatepass")[: 2 * end]
    return units.decode("utf-16-le", "surrogatepass")


def reply_basics(r: str) -> str | None:
    """run.ts › replyBasics: every reply the employee reads must pass these too."""
    if not r.strip():
        return "empty reply"
    if _LEAK.test(r):
        return "leaked the message format"
    if _CODE.test(r):
        return "JSON or code in the reply"
    if js_len(r) > 2500:
        return f"too long ({js_len(r)} chars)"
    return None


def understood_json(u: Understood) -> str:
    return json.dumps({"action": u.action, "args": u.args, "edits": u.edits}, ensure_ascii=False, separators=(",", ":"))


def understand_args(c: dict[str, Any]) -> dict[str, Any]:
    """What run.ts passes to understand() for a case: the actions and open question come from the chat state."""
    state = c["state"]
    outside = c["assistant"] == "outside"
    return {
        "system_prompt": prompts_for(c["assistant"])["understand"],
        "message": c["message"],
        "actions": outside_actions(state) if outside else within_actions(state),
        "open_question": outside_open_question(state) if outside else within_open_question(state),
        "context": c["context"],
        "recent_chat": c.get("recentChat"),
    }


def narrate_args(c: dict[str, Any]) -> dict[str, Any]:
    i = c["input"]
    events = [AssistantEvent(e["note"], e.get("data"), e.get("tone"), has_data="data" in e) for e in i["events"]]
    return {
        "system_prompt": prompts_for(c["assistant"])["narrate"],
        "events": events,
        "conversation_id": None,
        "employee_said": i.get("employeeSaid"),
        "open_question": i.get("openQuestion"),
        "already_shown": i.get("alreadyShown"),
        "claim": i.get("claim"),
        "page": i.get("page"),
    }


def _result(c: dict[str, Any], inp: str, reason: str | None, got: str, t0: float) -> dict[str, Any]:
    """run.ts › Result, keys in the same order (a missing reason is left out, as JSON.stringify leaves undefined)."""
    out: dict[str, Any] = {k: c[k] for k in ("id", "group", "polarity", "kind", "assistant")}
    out["input"] = inp
    out["pass"] = reason is None
    if reason is not None:
        out["reason"] = reason
    out["got"] = got
    out["ms"] = round((time.monotonic() - t0) * 1000)
    return out


async def run_one(llm: LlmClient, c: dict[str, Any], check: CaseCheck) -> dict[str, Any]:
    """run.ts › runOne: up to 5 attempts, 5 s × attempt apart (a failed check counts as a failed attempt, as there)."""
    t0 = time.monotonic()
    attempt = 1
    while True:
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                if c["kind"] == "understand":
                    u = await understand(llm, **understand_args(c))
                    reason = check.check(u)
                    return _result(c, c["message"], reason, understood_json(u), t0)
                result = await narrate(llm, **narrate_args(c))
            # curly quotes read the same as straight ones
            reply = re.sub("[’‘]", "'", result.reply)
            reply = re.sub("[“”]", '"', reply)
            reason = reply_basics(reply) or check.check(reply)
            said = c["input"].get("employeeSaid")
            inp = (
                said
                if said is not None
                else js_slice(c["input"]["events"][0]["note"], 120)
                if c["input"]["events"]
                else ""
            )
            return _result(c, inp, reason, reply, t0)
        except Exception as error:  # noqa: BLE001 - any failure is retried, as run.ts did
            if attempt >= ATTEMPTS:
                message = getattr(error, "message", None) or str(error) or type(error).__name__
                inp = c["message"] if c["kind"] == "understand" else ""
                return _result(c, inp, f"gateway error: {message}", "", t0)
            await asyncio.sleep(5 * attempt)
            attempt += 1


def _rate(passed: int, total: int) -> str:
    return f"{100 * passed / total:.1f}%"


def report(results: list[dict[str, Any]]) -> str:
    """run.ts › the markdown report."""

    def by(key: str) -> str:
        groups: dict[str, list[int]] = {}
        for r in results:
            v = groups.setdefault(r[key], [0, 0])
            v[1] += 1
            if r["pass"]:
                v[0] += 1
        return "\n".join(f"| {k} | {p}/{t} | {_rate(p, t)} |" for k, (p, t) in groups.items())

    passed = sum(1 for r in results if r["pass"])
    fails = [r for r in results if not r["pass"]]
    median = sorted(r["ms"] for r in results)[len(results) // 2]
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    parts = [
        "# Claim Assistant AI evaluation",
        f"{now} · {passed}/{len(results)} passed ({_rate(passed, len(results))}) · median {median} ms per case",
        f"## By kind\n| kind | passed | rate |\n|---|---|---|\n{by('kind')}",
        f"## By polarity\n| polarity | passed | rate |\n|---|---|---|\n{by('polarity')}",
        f"## By assistant\n| assistant | passed | rate |\n|---|---|---|\n{by('assistant')}",
        f"## By group\n| group | passed | rate |\n|---|---|---|\n{by('group')}",
        f"## Failures ({len(fails)})",
        *(
            f"- **{f['id']}** ({f['polarity']}) `{js_slice(_one_line(f['input']), 140)}` → {f.get('reason')}\n"
            f"  - got: `{js_slice(_one_line(f['got']), 300)}`"
            for f in fails
        ),
    ]
    return "\n\n".join(parts)


def _one_line(text: str) -> str:
    return text.replace("\n", " ")


def write_report(out: str, results: list[dict[str, Any]]) -> None:
    """<out>.md and <out>.json, as run.ts wrote them."""
    Path(f"{out}.md").write_text(report(results), encoding="utf-8", newline="\n")
    Path(f"{out}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def load_cases(only: str | None, kind: str | None, limit: int | None) -> list[tuple[dict[str, Any], CaseCheck]]:
    pairs = attach_checks(json.loads(CASES_FILE.read_text(encoding="utf-8")))
    pattern = re.compile(only) if only else None
    chosen = [
        (c, k)
        for c, k in pairs
        if (pattern is None or pattern.search(c["group"]) or pattern.search(c["id"]))
        and (kind is None or c["kind"] == kind)
    ]
    return chosen[:limit] if limit is not None else chosen


def gateway_client(http: httpx.AsyncClient) -> LlmClient:
    from tourclaim_agent.config import Settings

    settings = Settings(_env_file=ROOT / ".env")  # type: ignore[call-arg]
    if not settings.llm_base_url:
        raise SystemExit("TCA_LLM_BASE_URL is not set (.env)")
    return LlmClient(
        http, base_url=settings.llm_base_url, api_key=settings.llm_api_key.get_secret_value(), timeout=TIMEOUT_SECONDS
    )


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Claim Assistant AI evaluation (live gateway)")
    ap.add_argument("--limit", type=int, default=None, help="run only the first N chosen cases")
    ap.add_argument("--kind", choices=["understand", "narrate"], default=None)
    ap.add_argument("--only", default=None, help="regex on the group or id (run.ts EVAL_ONLY)")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--out", default="ai-eval-report", help="report path: <out>.md and <out>.json")
    args = ap.parse_args(argv)

    chosen = load_cases(args.only, args.kind, args.limit)
    if not chosen:
        print("No cases chosen.")
        return 1
    n_u = sum(1 for c, _ in chosen if c["kind"] == "understand")
    print(
        f"Running {len(chosen)} cases ({n_u} understand, {len(chosen) - n_u} narrate), {args.concurrency} at a time",
        flush=True,
    )
    results: list[dict[str, Any] | None] = [None] * len(chosen)
    queue = iter(range(len(chosen)))
    done = 0

    async with httpx.AsyncClient() as http:
        llm = gateway_client(http)

        async def worker() -> None:
            nonlocal done
            for i in queue:
                c, k = chosen[i]
                results[i] = await run_one(llm, c, k)
                done += 1
                if done % 25 == 0 or done == len(chosen):
                    failing = sum(1 for r in results if r and not r["pass"])
                    print(f"  {done}/{len(chosen)} done, {failing} failing", flush=True)

        await asyncio.gather(*(worker() for _ in range(max(1, args.concurrency))))

    final = [r for r in results if r is not None]
    out = re.sub(r"\.(md|json)$", "", args.out)
    write_report(out, final)
    passed = sum(1 for r in final if r["pass"])
    print(f"\n{passed}/{len(final)} passed. Report: {out}.md")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
