"""Checks what the model says to the worker, before the worker reads it.

Every number must already be somewhere the engine or the worker put it. Before the report, an amount of
money must come from the facts or the worker's own words, and the model must not use words that judge
legality. The check reads digits, with 萬, 千, nghìn and the like as part of the number, and zh-TW
wording. A reply in another language gets the number check and nothing else.
"""

import copy
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from labor_radar.agent.case import FIELDS, Case

LEGALITY_TERMS: dict[str, tuple[str, ...]] = {
    "verdict": ("違法", "非法", "不合法", "違反", "違規", "違背", "犯法", "觸法", "不合規", "不符合規定", "不符規定"),
    "debt": ("少給", "少付", "少拿", "短少", "積欠", "欠你", "欠了你", "被欠", "欠薪", "欠款"),
    "claim": ("應補", "可以請求", "可請求", "有權請求", "有權要求", "討回", "追討", "拿回", "要回(?![答覆應去家頭])"),
    "duty": ("依法應", "沒有依法", "未依法"),
}
"""Patterns matched after `_plain` folds the text. Each group is one way a reply can state a conclusion
that only the report may state."""
_JUDGING = re.compile("|".join(pattern for group in LEGALITY_TERMS.values() for pattern in group))
_SIMPLIFIED = str.maketrans("违规触给积应补请权讨钱来没", "違規觸給積應補請權討錢來沒")

# Only list numbering at a line start is free: a model numbers its questions, and the number says nothing.
# A digit right after the dot makes it a decimal, as in "4.5 小時".
_LIST_MARKER = re.compile(r"(?m)^[ \t]*(?:[1-5][.)、](?!\d)|[(（][1-5][)）])[ \t]*")

_SCALES = {"萬": 10_000, "万": 10_000, "千": 1_000, "百": 100, "億": 100_000_000, "亿": 100_000_000,
           "k": 1_000, "nghìn": 1_000, "ngàn": 1_000, "triệu": 1_000_000, "ribu": 1_000, "juta": 1_000_000,
           "พัน": 1_000, "หมื่น": 10_000, "แสน": 100_000, "ล้าน": 1_000_000}
_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_UNIT = r"(?:[萬万千百億亿]|(?i:k(?![a-z])|nghìn|ngàn|triệu|ribu|juta)|พัน|หมื่น|แสน|ล้าน)"
_SCALED_PART = re.compile(rf"({_NUM})\s*({_UNIT})")
_ART_UNIT = {"條": "條", "条": "條", "項": "項", "项": "項", "款": "款"}
_TOKEN = re.compile(
    rf"""(?P<ymd>\d{{4}}(?:-\d{{1,2}}-\d{{1,2}}|/\d{{1,2}}/\d{{1,2}}|年\s*\d{{1,2}}\s*月\s*\d{{1,2}}\s*[日號号]?))
    |(?P<md>(?<![\d/.])\d{{1,2}}/\d{{1,2}}(?![\d/])|\d{{1,2}}\s*月\s*\d{{1,2}}\s*[日號号]?)
    |(?P<time>\d{{1,2}}:\d{{2}}(?::\d{{2}})?)
    |(?P<art>第\s*\d{{1,4}}(?:-\d{{1,3}})?\s*[條条項项款]|(?<=[法例則])\s*\d{{1,4}}(?:-\d{{1,3}})?\s*[條条])
    |(?P<article>(?i:điều|pasal)\s*\d{{1,4}}(?:-\d{{1,3}})?|มาตรา\s*\d{{1,4}}(?:-\d{{1,3}})?)
    |(?P<clause>(?i:khoản|ayat)\s*\(?\d{{1,3}}\)?|วรรค\s*\d{{1,3}})
    |(?P<scaled>{_NUM}\s*{_UNIT}(?:\s*{_NUM}\s*{_UNIT})*(?:(?<=[萬万千百])\d(?![\d.,]))?)
    |(?P<num>{_NUM})""",
    re.X,
)
_MONEY_AFTER = re.compile(r"\s*(?:[元塊块圓]|(?i:ntd|twd|đài tệ|dolar)|ดอลลาร์)")
_MONEY_BEFORE = re.compile(r"(?i:nt\$|ntd|twd|\$)\s*$")

Token = tuple


def _plain(text: str) -> str:
    """One spelling per word: full-width forms folded, spaces and invisible marks between characters
    dropped, and the simplified characters of the judging words mapped to traditional ones."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(c for c in text if not c.isspace() and unicodedata.category(c) != "Cf").translate(_SIMPLIFIED)


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", "")).normalize()
    except InvalidOperation:
        return Decimal(0)


def _scaled(raw: str) -> Decimal:
    """2萬5千 is 25,000 and 1.9萬 is 19,000. A lone digit right after a Chinese unit counts in the next
    unit down, as people say it: 2萬5 is 25,000."""
    total, unit = Decimal(0), Decimal(1)
    for number, word in _SCALED_PART.findall(raw):
        unit = Decimal(_SCALES[word.lower()])
        total += _decimal(number) * unit
    if raw[-1].isdigit():
        total += Decimal(raw[-1]) * unit / 10
    return total.normalize()


def _scan(text: str) -> Iterator[tuple[Token, bool]]:
    """Each fact the text asserts, and whether it is written as an amount of money. Only date and time
    parts, which the pattern caps at four digits, go through int()."""
    text = _LIST_MARKER.sub("", unicodedata.normalize("NFKC", text))
    for match in _TOKEN.finditer(text):
        kind, raw = match.lastgroup, match.group()
        digits = [int(d) for d in re.findall(r"\d+", raw)] if kind in ("ymd", "md", "time") else []
        if kind == "ymd":
            yield ("num", Decimal(digits[0])), False
            yield ("md", digits[1], digits[2]), False
        elif kind == "md" and digits[0] <= 12 and 1 <= digits[1] <= 31:
            yield ("md", digits[0], digits[1]), False
        elif kind == "md":
            yield from ((("num", Decimal(d)), False) for d in digits)
        elif kind == "time":
            yield ("time", f"{digits[0]:02d}:{digits[1]:02d}"), False
        elif kind in ("art", "article", "clause"):
            number = re.search(r"\d+(?:-\d+)?", raw).group()
            unit = _ART_UNIT[raw.rstrip()[-1]] if kind == "art" else "條" if kind == "article" else "項"
            yield ("art", number, unit), False
        else:
            money = bool(_MONEY_AFTER.match(text, match.end()) or _MONEY_BEFORE.search(text, 0, match.start()))
            yield ("num", _scaled(raw) if kind == "scaled" else _decimal(raw)), money


def tokens(text: str) -> set[Token]:
    """The facts a text asserts: dates as (month, day), times, article numbers, and plain numbers."""
    return {token for token, _ in _scan(text)}


def amounts(text: str) -> set[Token]:
    return {token for token, money in _scan(text) if money}


def describe(token: Token) -> str:
    kind = token[0]
    if kind == "md":
        return f"{token[1]}/{token[2]}"
    if kind == "art":
        return f"第 {token[1]} {token[2]}"
    if kind == "num":
        shown = format(token[1], ",f")
        return shown if len(shown) <= 24 else shown[:24] + "…"
    return str(token[1])


@dataclass(frozen=True)
class Problem:
    kind: Literal["number", "amount", "term", "empty"]
    token: str
    message: str


EMPTY = Problem("empty", "", "回覆是空的。")


def _typed_facts(draft: dict) -> str:
    """The draft without names and addresses: free text read from a photo may say anything."""
    data = copy.deepcopy(draft)
    for path, spec in FIELDS.items():
        parent, _, leaf = path.rpartition(".")
        node = data.get(parent) if parent else data
        if spec.free_text and isinstance(node, dict):
            node.pop(leaf, None)
    return json.dumps(data, ensure_ascii=False)


def _worker_facts(case: Case) -> list[str]:
    return [_typed_facts(case.draft), case.user_text()]


def grounding(case: Case, report: dict | None, extra: Iterable[str] = ()) -> set[Token]:
    """Numbers the reply may use: the facts, what the worker typed, the report, and tool output the
    engine produced. The assistant's own earlier replies are left out on purpose."""
    texts = [*_worker_facts(case), json.dumps(report, ensure_ascii=False) if report else "",
             f"shifts: {len(case.draft.get('shifts', []))}", *extra]
    return set().union(*(tokens(text) for text in texts))


def check(text: str, case: Case, report: dict | None, extra: Iterable[str] = ()) -> list[Problem]:
    """`report` is the engine's report on the current facts, or None while there is none. Only a report
    lets the reply judge legality or name an amount the worker did not give."""
    problems = []
    known = grounding(case, report, extra)
    for token in sorted(tokens(text) - known, key=str):
        shown = describe(token)
        problems.append(Problem("number", shown, f"回覆提到「{shown}」，但這個數字不在資料、工人說的話或報告裡。"))
    if report is not None:
        return problems
    owned = set().union(*(tokens(t) for t in _worker_facts(case)))
    for token in sorted((amounts(text) & known) - owned, key=str):
        shown = describe(token)
        problems.append(Problem("amount", shown, f"報告出來之前，回覆提到金額「{shown}」，但工人沒說過，資料表裡也沒有。"))
    for match in _JUDGING.finditer(_plain(text)):
        problems.append(Problem("term", match.group(), f"報告出來之前不能使用「{match.group()}」這類判斷合法性的字眼。"))
    return problems
