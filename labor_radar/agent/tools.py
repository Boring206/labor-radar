"""The only way the model touches the case. Each tool states the phases it may run in.

A tool never raises on bad arguments. It returns {"error": ...} so the model can correct itself.
`ALLOWED` is the policy: the model is shown only the tools of the current phase, and a call to
any other tool is refused here too.
"""

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from labor_radar import extraction, paperwork, referrals, report
from labor_radar.agent import questions
from labor_radar.agent.case import (
    FIELDS,
    MAX_SHIFTS,
    Case,
    Phase,
    Provenance,
    Source,
    facts_state,
    gaps,
    get_path,
    materialize,
    normalize_shift,
    put_path,
    shift_key,
)
from labor_radar.engine import assess
from labor_radar.i18n import t
from labor_radar.intake import parse_record
from labor_radar.rules import law
from labor_radar.rules.registry import RULES

SOURCES = Path(law.__file__).parent / "sources.md"
MAX_FACTS = 40
MAX_SHIFTS_PER_FACT = 100
MAX_UNREADABLE = 10
VIEW_ITEMS = 3
VIEW_DATES = 10


@dataclass(frozen=True)
class Attachment:
    data: bytes = field(repr=False)
    mime: str
    name: str = ""


@dataclass(frozen=True)
class Grounded:
    text: str
    facts: dict | None
    """The facts the text was derived from, or None when it does not depend on them, like the law."""


@dataclass
class Ctx:
    lang: str
    attachment: Attachment | None = None
    today: date = field(default_factory=date.today)
    grounded: list[Grounded] = field(default_factory=list)
    """Text a tool produced from the engine or the law. The guard accepts its numbers."""

    def ground(self, text: str, case: Case | None = None) -> None:
        """Text derived from `case` vouches for its numbers only while those facts hold."""
        self.grounded.append(Grounded(text, facts_state(case) if case is not None else None))

    def grounding(self, case: Case) -> list[str]:
        facts = facts_state(case)
        return [g.text for g in self.grounded if g.facts is None or g.facts == facts]


Handler = Callable[[Case, dict, Ctx], tuple[Case, dict]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Handler
    needs_attachment: bool = False

    @property
    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _error(case: Case, message: str, **extra: Any) -> tuple[Case, dict]:
    return case, {"error": message, **extra}


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def _quote_problem(quote: Any, case: Case) -> str | None:
    if not isinstance(quote, str) or not quote.strip():
        return "quote is required: copy the words of the worker's message that state this fact"
    if len(quote) > 300 or _squash(quote) not in _squash(case.user_text()):
        return "quote must be copied exactly from something the worker wrote; do not paraphrase or translate it"
    return None


def _add_shifts(case: Case, value: Any, quote: str) -> str | None:
    if not isinstance(value, list) or not value or len(value) > MAX_SHIFTS_PER_FACT:
        return f"value must be a list of 1 to {MAX_SHIFTS_PER_FACT} shifts like {{date, start, end, break_minutes}}"
    try:
        shifts = [normalize_shift(raw) for raw in value]
    except ValueError as error:
        return str(error)
    if problem := _too_many_shifts(case, shifts):
        return problem
    for shift in shifts:
        put_shift(case, shift, Provenance(source=Source.USER, quote=quote))
    return None


def _too_many_shifts(case: Case, added: list[dict]) -> str | None:
    total = len({shift_key(s) for s in [*case.draft.get("shifts", []), *added]})
    if total > MAX_SHIFTS:
        return (f"a case holds at most {MAX_SHIFTS} shifts and this would make {total}; nothing from this list "
                "was stored. Ask the worker about one pay period at a time")
    return None


def put_shift(case: Case, shift: dict, provenance: Provenance) -> None:
    key = shift_key(shift)
    others = [s for s in case.draft.get("shifts", []) if shift_key(s) != key]
    case.draft["shifts"] = [*others, shift]
    case.provenance[key] = provenance


def _remove_shifts(case: Case, value: Any) -> str | None:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        return "value must be a list of dates written YYYY-MM-DD"
    kept = [s for s in case.draft.get("shifts", []) if s["date"] not in value]
    if len(kept) == len(case.draft.get("shifts", [])):
        return f"no shift is recorded on {', '.join(value)}"
    for shift in case.draft["shifts"]:
        if shift["date"] in value:
            case.provenance.pop(shift_key(shift), None)
    case.draft["shifts"] = kept
    return None


def _apply_fact(case: Case, item: Any) -> str | None:
    if not isinstance(item, dict):
        return "each fact must be an object with path, value and quote"
    path, value, quote = item.get("path"), item.get("value"), item.get("quote")
    if problem := _quote_problem(quote, case):
        return problem
    if path == "shifts":
        return _add_shifts(case, value, quote)
    if path == "shifts.remove":
        return _remove_shifts(case, value)
    spec = FIELDS.get(path) if isinstance(path, str) else None
    if spec is None:
        return f"unknown path {path!r}; the paths are: {', '.join([*FIELDS, 'shifts', 'shifts.remove'])}"
    try:
        parsed = spec.parse(value)
    except ValueError as error:
        return f"{path} {error}"
    put_path(case.draft, path, parsed)
    case.provenance[path] = Provenance(source=Source.USER, quote=quote.strip())
    question = questions.BY_PATH.get(path)
    if question and question.id not in case.answered:
        case.answered.append(question.id)
    return None


def _outcome(before: Case, after: Case, result: dict, lang: str) -> tuple[Case, dict]:
    """The result a fact-changing tool returns: what the case now holds and what is still open, so the
    model need not ask again."""
    if facts_state(before) != facts_state(after):
        after = after.changed()
    result["phase"] = questions.phase(after).value
    if problems := gaps(after):
        result["gaps"] = problems
    else:
        result["questions"] = questions.describe(after, lang)["questions"]
    return after, result


def set_facts(case: Case, args: dict, ctx: Ctx) -> tuple[Case, dict]:
    facts = args.get("facts")
    if not isinstance(facts, list) or not 0 < len(facts) <= MAX_FACTS:
        return _error(case, f"facts must be a list of 1 to {MAX_FACTS} items {{path, value, quote}}")
    new = case.model_copy(deep=True)
    applied, rejected = [], []
    for item in facts:
        path = item.get("path") if isinstance(item, dict) else None
        if problem := _apply_fact(new, item):
            value = item.get("value") if isinstance(item, dict) else item
            rejected.append({"path": path, "value": json.dumps(value, ensure_ascii=False)[:120], "error": problem})
        else:
            applied.append(path)
    result: dict[str, Any] = {"applied": applied, "rejected": rejected}
    if not applied:
        result["error"] = "no fact was stored"
        return case, result
    result["stored_as"] = {p: _label(new, p) for p in applied if p in questions.BY_PATH}
    return _outcome(case, new, result, ctx.lang)


def _label(case: Case, path: str) -> str:
    """The wording of the option a stored answer stands for, so the model can see whether it picked the right one."""
    question = questions.BY_PATH[path]
    return questions.option_label("zh-TW", question, get_path(case.draft, path))


def _said_by_worker(case: Case, path: str) -> bool:
    stated = case.provenance.get(path)
    return stated is not None and stated.source is Source.USER


def _photo_set(case: Case, values: dict[str, Any], conflicts: list, rejected: list) -> None:
    """Values a photo shows together, such as a pay type and its wage, are stored together or not at all.
    A value the registry refuses drops the group. If the worker said otherwise for any of them, the
    worker's values stay and the photo's become conflicts."""
    parsed = {}
    for path, value in values.items():
        try:
            parsed[path] = FIELDS[path].parse(value)
        except ValueError as error:
            rejected.append({"path": path, "value": json.dumps(value)[:120], "error": f"{path} {error}"})
            return
    differs = {path: value for path, value in parsed.items() if get_path(case.draft, path) != value}
    if any(_said_by_worker(case, path) for path in differs):
        conflicts += [{"path": path, "kept": get_path(case.draft, path), "photo": value} for path, value in differs.items()]
        return
    for path, value in differs.items():
        put_path(case.draft, path, value)
        case.provenance[path] = Provenance(source=Source.PHOTO)


def read_document(case: Case, args: dict, ctx: Ctx) -> tuple[Case, dict]:
    if ctx.attachment is None:
        return _error(case, "no file is attached to this message")
    try:
        draft = extraction.extract(ctx.attachment.data, ctx.attachment.mime, ctx.today.year)
    except extraction.ExtractionError as error:
        return _error(case, str(error))
    new = case.model_copy(deep=True)
    conflicts: list = []
    rejected: list = []
    shifts = []
    for raw in draft.shifts:
        try:
            shifts.append(normalize_shift({"date": raw.date.isoformat(), "start": raw.start.strftime("%H:%M"),
                                           "end": raw.end.strftime("%H:%M"), "break_minutes": raw.break_minutes or 0}))
        except ValueError as error:
            rejected.append({"path": "shifts", "value": raw.date.isoformat(), "error": str(error)})
    if problem := _too_many_shifts(new, shifts):
        rejected.append({"path": "shifts", "value": f"{len(shifts)} shifts", "error": problem})
        shifts = []
    for shift in shifts:
        existing = new.provenance.get(shift_key(shift))
        if existing and existing.source is Source.USER:
            continue
        put_shift(new, shift, Provenance(source=Source.PHOTO))
    wage = (("hourly", draft.hourly_wage) if draft.hourly_wage
            else ("monthly", draft.monthly_wage) if draft.monthly_wage else None)
    if wage:
        _photo_set(new, {"employment.pay_basis": wage[0], "employment.agreed_wage": wage[1]}, conflicts, rejected)
    if draft.gross_pay is not None:
        period = ({"period.start": draft.pay_period_start.isoformat(), "period.end": draft.pay_period_end.isoformat()}
                  if draft.pay_period_start and draft.pay_period_end else {})
        _photo_set(new, {"payslips.gross_pay": draft.gross_pay, **period}, conflicts, rejected)
    for path, name in (("worker.name", draft.worker_name), ("employer.name", draft.employer_name)):
        if name and name.strip():
            _photo_set(new, {path: name.strip()[:100]}, conflicts, rejected)
    result = {
        "document_type": draft.document_type,
        "shifts_in_document": len(draft.shifts),
        "hourly_wage": draft.hourly_wage, "monthly_wage": draft.monthly_wage, "gross_pay": draft.gross_pay,
        "unreadable": [u[:200] for u in draft.unreadable[:MAX_UNREADABLE]],
        "conflicts": conflicts,
        "rejected": rejected,
        "reminder": "the worker's message may state facts the document does not show; store them with set_facts "
                    "before you reply",
    }
    return _outcome(case, new, result, ctx.lang)


def next_questions(case: Case, args: dict, ctx: Ctx) -> tuple[Case, dict]:
    problems = gaps(case)
    result: dict[str, Any] = {"phase": questions.phase(case).value, "gaps": problems}
    if not problems:
        result |= questions.describe(case, ctx.lang)
        result["question_count"] = len(result["questions"])
    result["optional_missing"] = [p for p in ("payslips.gross_pay", "city") if get_path(case.draft, p) is None]
    without_spread = [{k: v for k, v in q.items() if k != "what_changes"} for q in result.get("questions", [])]
    ctx.ground(json.dumps(result | {"questions": without_spread}, ensure_ascii=False), case)
    return case, result


def _item(i: dict) -> dict:
    keep = {k: i[k] for k in ("rule_id", "title", "verdict", "explanation", "amount", "why_human")}
    dates = {"dates": i["dates"][:VIEW_DATES]} | ({"more_dates": len(i["dates"]) - VIEW_DATES}
                                                  if len(i["dates"]) > VIEW_DATES else {})
    return keep | dates | {"citations": [c["label"] for c in i["citations"]],
                           "calculation": [f"{c['text']} = {c['amount']}" for c in i["calcs"]]}


def _listed(items: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """The first few findings of each rule, and how many more each rule has. The engine reports one finding
    per day, and the view goes into the model's prompt at every step."""
    shown: list[dict] = []
    more: Counter[str] = Counter()
    for item in items:
        if sum(s["rule_id"] == item["rule_id"] for s in shown) < VIEW_ITEMS:
            shown.append(_item(item))
        else:
            more[item["rule_id"]] += 1
    return shown, dict(more)


def compact_view(view: dict) -> dict:
    """What the model sees of a report: the findings, the claim and the citations, not the whole page."""
    claim = view["claim"]
    groups = {group: _listed(view[group]) for group in ("violations", "needs_human", "notes")}
    not_listed = {group: more for group, (_, more) in groups.items() if more}
    return {
        "summary": view["summary"],
        "violation_count": len(view["violations"]),
        **{group: shown for group, (shown, _) in groups.items()},
        **({"not_listed": not_listed} if not_listed else {}),
        "claim": ({k: claim[k] for k in ("complete", "required", "paid_label", "paid", "shortfall", "settled",
                                         "possible_extra", "rate_note")}
                  if claim["complete"] else {"complete": False, "message": claim["message"]}),
        "not_checked": view["not_checked"],
        "disclaimer": view["disclaimer"],
    }


def report_for(case: Case, lang: str) -> dict | None:
    """The report the model may cite: the engine's findings on the current facts, once run_check has shown
    them. Computed from the facts each time it is needed."""
    if not case.reported or questions.phase(case) is not Phase.REPORT:
        return None
    return compact_view(report.render(assess(parse_record(materialize(case))), lang))


def run_check(case: Case, args: dict, ctx: Ctx) -> tuple[Case, dict]:
    reported = case.model_copy(update={"reported": True})
    return reported, report_for(reported, ctx.lang)


@cache
def _law_text() -> dict[str, str]:
    """Quoted article text from sources.md, keyed by the heading, e.g. 勞動基準法第24條."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in SOURCES.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
        elif current is not None and line.startswith(">"):
            current.append(line[1:].strip())
    return {heading: "\n".join(lines) for heading, lines in sections.items()}


def explain_rule(case: Case, args: dict, ctx: Ctx) -> tuple[Case, dict]:
    rules = {rule.id: rule for rule in RULES}
    rule = rules.get(args.get("rule_id")) if isinstance(args.get("rule_id"), str) else None
    if rule is None:
        return _error(case, f"rule_id must be one of: {', '.join(rules)}")
    articles = [{"label": c.label, "url": c.url, "text": _law_text().get(f"{c.law}第{c.flno}條", "")}
                for c in rule.citations]
    result = {"rule_id": rule.id, "title": t(ctx.lang, f"rule.{rule.id}.title"), "articles": articles}
    ctx.ground(json.dumps(result, ensure_ascii=False))
    return case, result


def draft_mediation(case: Case, args: dict, ctx: Ctx) -> tuple[Case, dict]:
    view = report_for(case, ctx.lang)
    if view is None:
        return _error(case, "run_check first: there is no report yet")
    if not view["violation_count"]:
        return _error(case, "the report found no item to put in an application, so there is nothing to draft")
    text = paperwork.mediation_draft(assess(parse_record(materialize(case))))
    ctx.ground(text, case)
    return case, {"mediation_draft": text, "note": "the page shows this draft under the report; do not paste it again"}


def find_help(case: Case, args: dict, ctx: Ctx) -> tuple[Case, dict]:
    city = args.get("city", "")
    if not isinstance(city, str) or len(city) > 20:
        return _error(case, "city must be a short place name such as 高雄市")
    result = {"referrals": referrals.for_city(ctx.lang, city.strip())}
    ctx.ground(json.dumps(result, ensure_ascii=False))
    return case, result


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "OBJECT", "properties": properties, "required": list(required)}


_PATHS = [*FIELDS, "shifts", "shifts.remove"]
_VALUE_GUIDE = " ".join(
    f"{q.path} ({questions.question_text('zh-TW', q)}) " + ", ".join(
        f"{o} = {questions.option_label('zh-TW', q, o)}" for o in (*q.options, "unknown")) + "."
    for q in questions.QUESTIONS)
TOOLS: dict[str, Tool] = {tool.name: tool for tool in (
    Tool("read_document",
         "Read the schedule, clock record or payslip photo attached to this message and add what it shows to "
         "the facts. It only transcribes. Call it once when a file is attached.",
         _object({}), read_document, needs_attachment=True),
    Tool("set_facts",
         "Store facts the worker stated. quote must be copied exactly from the worker's own words. Enum "
         "fields accept only their listed values. A shift is {date: YYYY-MM-DD, start: HH:MM, end: HH:MM, "
         "break_minutes}; an end earlier than the start means the shift ends the next day. For path "
         "'shifts' value is a list of shifts to add; for 'shifts.remove' it is a list of dates. Use the value "
         "'unknown' when the worker says they do not know. Pick the option whose wording matches what the "
         "worker said, not the one that sounds closest to a legal term. "
         "Other values: employment.pay_basis hourly|monthly; employment.agreed_wage a number in NTD; "
         "employment.domestic_worker true|false; payslips.gross_pay the pay before deductions. "
         + _VALUE_GUIDE,
         _object({"facts": {"type": "ARRAY", "items": _object({
             "path": {"type": "STRING", "enum": _PATHS},
             "value": {"description": "a string, number, boolean or list, as the path requires"},
             "quote": {"type": "STRING", "description": "the worker's own words, copied exactly"},
         }, ("path", "value", "quote"))}}, ("facts",)), set_facts),
    Tool("next_questions",
         "List what is still missing and the questions whose answer would change the result. Ask only "
         "these, at most two per turn.", _object({}), next_questions),
    Tool("run_check",
         "Run the rule engine on the confirmed facts and return the findings, the amounts and the citations.",
         _object({}), run_check),
    Tool("explain_rule", "Return the article text behind one rule of the report.",
         _object({"rule_id": {"type": "STRING", "enum": [r.id for r in RULES]}}, ("rule_id",)), explain_rule),
    Tool("draft_mediation",
         "Fill in the 勞資爭議調解申請書 from the report. Only after run_check found at least one item.",
         _object({}), draft_mediation),
    Tool("find_help", "Return the hotlines and offices the worker can contact.",
         _object({"city": {"type": "STRING", "description": "the worker's city, if known"}}), find_help),
)}

ALLOWED: dict[Phase, tuple[str, ...]] = {
    Phase.GATHER: ("read_document", "set_facts", "next_questions"),
    Phase.CLARIFY: ("read_document", "set_facts", "next_questions"),
    Phase.CONFIRM: ("read_document", "set_facts", "next_questions"),
    Phase.REPORT: ("read_document", "set_facts", "run_check", "explain_rule", "draft_mediation", "find_help"),
}


def available(case: Case, ctx: Ctx) -> list[Tool]:
    return [TOOLS[name] for name in ALLOWED[questions.phase(case)]
            if ctx.attachment is not None or not TOOLS[name].needs_attachment]


def call(case: Case, name: str, args: Any, ctx: Ctx) -> tuple[Case, dict]:
    tool = TOOLS.get(name)
    if tool is None:
        return _error(case, f"unknown tool {name!r}")
    phase = questions.phase(case)
    if name not in ALLOWED[phase]:
        return _error(case, f"{name} is not available in the {phase.value} phase; available now: "
                            f"{', '.join(ALLOWED[phase])}")
    if not isinstance(args, dict):
        return _error(case, "arguments must be an object")
    try:
        return tool.handler(case, args, ctx)
    except Exception as error:  # what the model or a photo sends must never end the turn with a 500
        return _error(case, f"{name} could not use this input ({type(error).__name__}); nothing was changed")
