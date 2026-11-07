"""Checks what the model says to the worker, before the worker reads it.

Every number must already be somewhere the engine or the worker put it. Before the report,
the model must not use words that judge legality. The check reads digits and zh-TW wording
only. A reply in another language gets the number check and nothing else.
"""

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from labor_radar.agent.case import Case, Phase

LEGALITY_TERMS = (
    "違法", "違反", "不合法", "違規", "犯法", "少給", "積欠", "欠你", "欠薪", "應補", "可以請求", "可請求",
)

# Only list numbering at a line start is free: a model numbers its questions, and the number says nothing.
_LIST_MARKER = re.compile(r"(?m)^[ \t]*(?:[1-5][.)、]|[(（][1-5][)）])[ \t]*")

_TOKEN = re.compile(
    r"""(?P<ymd>\d{4}(?:-\d{1,2}-\d{1,2}|/\d{1,2}/\d{1,2}|年\s*\d{1,2}\s*月\s*\d{1,2}\s*[日號号]?))
    |(?P<md>(?<![\d/.])\d{1,2}/\d{1,2}(?![\d/])|\d{1,2}\s*月\s*\d{1,2}\s*[日號号]?)
    |(?P<time>\d{1,2}:\d{2}(?::\d{2})?)
    |(?P<art>第\s*\d+(?:-\d+)?\s*[條条項项款])
    |(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)""",
    re.X,
)

Token = tuple


def tokens(text: str, *, source: bool = False) -> set[Token]:
    """The facts a text asserts: dates as (month, day), times, article numbers, and plain numbers.
    A source text also vouches for the bare number of each article it cites."""
    text = unicodedata.normalize("NFKC", text)
    text = _LIST_MARKER.sub("", text)
    found: set[Token] = set()
    for match in _TOKEN.finditer(text):
        kind, raw = match.lastgroup, match.group()
        digits = [int(d) for d in re.findall(r"\d+", raw)]
        if kind == "ymd":
            found |= {("num", Decimal(digits[0])), ("md", digits[1], digits[2])}
        elif kind == "md" and digits[0] <= 12 and 1 <= digits[1] <= 31:
            found.add(("md", digits[0], digits[1]))
        elif kind == "md":
            found |= {("num", Decimal(d)) for d in digits}
        elif kind == "time":
            found.add(("time", f"{digits[0]:02d}:{digits[1]:02d}"))
        elif kind == "art":
            unit = raw[-1].replace("条", "條").replace("项", "項")
            number = re.search(r"\d+(?:-\d+)?", raw).group()
            found.add(("art", number, unit))
            if source:
                found |= {("num", _decimal(part)) for part in number.split("-")}
        else:
            found.add(("num", _decimal(raw)))
    return found


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", "")).normalize()
    except InvalidOperation:
        return Decimal(0)


def describe(token: Token) -> str:
    kind = token[0]
    if kind == "md":
        return f"{token[1]}/{token[2]}"
    if kind == "art":
        return f"第 {token[1]} {token[2]}"
    if kind == "num":
        return format(token[1], ",f")
    return str(token[1])


@dataclass(frozen=True)
class Problem:
    kind: Literal["number", "term", "empty"]
    token: str
    message: str


EMPTY = Problem("empty", "", "回覆是空的。")


def grounding(case: Case, extra: Iterable[str] = ()) -> set[Token]:
    """Numbers the reply may use: the facts, what the worker typed, the report, and tool output the
    engine produced. The assistant's own earlier replies are left out on purpose."""
    texts = [json.dumps(case.draft, ensure_ascii=False), case.user_text(),
             json.dumps(case.last_report, ensure_ascii=False) if case.last_report else "",
             f"shifts: {len(case.draft.get('shifts', []))}", *extra]
    return set().union(*(tokens(text, source=True) for text in texts))


def check(text: str, case: Case, phase: Phase, extra: Iterable[str] = ()) -> list[Problem]:
    problems = []
    known = grounding(case, extra)
    for token in sorted(tokens(text) - known, key=str):
        shown = describe(token)
        problems.append(Problem("number", shown, f"回覆提到「{shown}」，但這個數字不在資料、工人說的話或報告裡。"))
    if phase is not Phase.REPORT:
        for term in LEGALITY_TERMS:
            if term in text:
                problems.append(Problem("term", term, f"報告出來之前不能使用「{term}」這類判斷合法性的字眼。"))
    return problems
