"""JavaScript regular expressions for the evaluation checks, matched as JavaScript matches them.

The checks in Tour_React scripts/ai-eval/cases.ts are JavaScript regex literals (no `u` flag). The same source
works in Python's `re` once the few differences are bridged: `\\d`, `\\w` and `\\b` are ASCII only (re.ASCII), `\\s`
is JavaScript's whitespace set, `.` stops at any line terminator, and `$` (without `m`) is the very end of the text.
"""

from __future__ import annotations

import re

# JavaScript's \s: WhiteSpace and LineTerminator
JS_WS = "\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff"
_WS_RE = re.compile(f"[{JS_WS}]+")


def _translate(source: str, multiline: bool) -> str:
    out: list[str] = []
    i = 0
    in_class = False
    while i < len(source):
        ch = source[i]
        if ch == "\\":
            nxt = source[i + 1]
            if nxt == "s":
                out.append(JS_WS if in_class else f"[{JS_WS}]")
            elif nxt == "S":
                if in_class:
                    raise ValueError(f"\\S inside a class is not supported: {source}")
                out.append(f"[^{JS_WS}]")
            elif nxt == "/":
                out.append("/")
            else:
                out.append(source[i : i + 2])
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
                out.append(ch)
            elif ch == "[":
                out.append("\\[")
            else:
                out.append(ch)
        elif ch == "[":
            in_class = True
            out.append(ch)
            if source[i + 1 : i + 2] == "^":
                out.append("^")
                i += 1
        elif ch == ".":
            out.append("[^\\n\\r\\u2028\\u2029]")
        elif ch == "$" and not multiline:
            out.append("\\Z")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class JsRe:
    """A JavaScript regex literal `/source/flags`: `test()` as RegExp.prototype.test, str() as `${re}`."""

    def __init__(self, source: str, flags: str = "") -> None:
        self.source = source
        self.flags = flags
        py_flags = re.ASCII
        if "i" in flags:
            py_flags |= re.IGNORECASE
        if "m" in flags:
            py_flags |= re.MULTILINE
        self.pattern = re.compile(_translate(source, "m" in flags), py_flags)

    def test(self, text: str) -> bool:
        return self.pattern.search(text) is not None

    def replace_all(self, text: str, repl: str) -> str:
        """text.replace(/re/g, repl) with a plain replacement text."""
        return self.pattern.sub(lambda _m: repl, text)

    def __str__(self) -> str:
        return f"/{self.source}/{self.flags}"

    __repr__ = __str__


def R(source: str, flags: str = "") -> JsRe:  # noqa: N802 (reads like a regex literal)
    return JsRe(source, flags)


def js_split_ws(text: str) -> list[str]:
    """text.split(/\\s+/)"""
    return _WS_RE.split(text)
