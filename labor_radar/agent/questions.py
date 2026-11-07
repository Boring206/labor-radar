"""Which questions are worth asking, found by changing the answer and re-running the engine.

A question is decisive when different answers give a different set of findings or a different
amount. The rest are never asked. No model is involved, so the choice can be tested and shown.
"""

import json
from dataclasses import dataclass
from functools import lru_cache

from labor_radar import fmt
from labor_radar.agent.case import Case, Phase, gaps, get_path, materialize
from labor_radar.engine import Assessment, assess
from labor_radar.i18n import t
from labor_radar.intake import parse_record
from labor_radar.model import WEEKDAYS
from labor_radar.rules.registry import RULES

TOP = 3
UNKNOWN = "unknown"
RULE_ORDER = {rule.id: index for index, rule in enumerate(RULES)}


@dataclass(frozen=True)
class Question:
    id: str
    path: str
    options: tuple[str, ...]
    text_key: str
    option_key: str
    assume: str | None = None
    """The answer that lets the engine reach a verdict. While it is open, the rules this question gates
    can only say needs_human, which hides what the other questions would change."""


QUESTIONS: tuple[Question, ...] = (
    Question("relationship", "employment.relationship", ("employee", "contractor"),
             "ui.relationship", "ui.relationship", assume="employee"),
    Question("shift_system", "employment.shift_system", ("rotating", "flexible", "fixed"),
             "ui.shift_system", "ui.shift_system"),
    Question("special_hours_arrangement", "employment.special_hours_arrangement", ("no", "yes"),
             "ui.special_hours", "ui.answer", assume="no"),
    Question("responsibility_system", "employment.responsibility_system", ("no", "yes"),
             "ui.responsibility_system", "ui.answer", assume="no"),
    Question("overtime_consent", "employment.overtime_consent", ("no", "yes"),
             "ui.overtime_consent", "ui.answer"),
    Question("week_starts_on", "employment.week_starts_on", WEEKDAYS,
             "ui.week_starts_on", "ui.weekday"),
)
BY_ID = {q.id: q for q in QUESTIONS}
BY_PATH = {q.path: q for q in QUESTIONS}


@dataclass(frozen=True)
class Impact:
    question: Question
    rules: tuple[str, ...]
    spread: int
    """Largest NTD difference two answers make to the estimated shortfall, or to the amount that
    still needs a human."""


@dataclass(frozen=True)
class Analysis:
    ask: tuple[Impact, ...]
    not_asked: tuple[Question, ...]


def _outcome(assessment: Assessment) -> tuple[dict[str, frozenset], int, int]:
    by_rule: dict[str, set] = {}
    for result in assessment.results:
        for finding in result.findings:
            by_rule.setdefault(result.rule.id, set()).add((finding.verdict.value, finding.variant))
    claim = assessment.claim
    shortfall = int(fmt.round_ntd(claim.shortfall)) if claim.shortfall is not None else 0
    return {k: frozenset(v) for k, v in by_rule.items()}, shortfall, int(fmt.round_ntd(claim.possible_extra))


def _probe(case: Case, question: Question, background: dict[str, str]) -> Impact | None:
    outcomes = [_outcome(assess(parse_record(materialize(case, background | {question.path: option}))))
                for option in question.options]
    findings = [o[0] for o in outcomes]
    shortfalls, possibles = [o[1] for o in outcomes], [o[2] for o in outcomes]
    if all(o == outcomes[0] for o in outcomes):
        return None
    rule_ids = {rid for f in findings for rid in f}
    changed = sorted((rid for rid in rule_ids if len({f.get(rid, frozenset()) for f in findings}) > 1),
                     key=RULE_ORDER.__getitem__)
    return Impact(question, tuple(changed), max(max(shortfalls) - min(shortfalls), max(possibles) - min(possibles)))


def _impact(case: Case, question: Question, open_questions: list[Question]) -> Impact | None:
    """What the question changes as things stand, and with the other open gates answered the clean way."""
    clean = {q.path: q.assume for q in open_questions if q is not question and q.assume}
    impacts = [i for background in ({}, clean) if (i := _probe(case, question, background))]
    if not impacts:
        return None
    rules = {rid for i in impacts for rid in i.rules}
    return Impact(question, tuple(sorted(rules, key=RULE_ORDER.__getitem__)), max(i.spread for i in impacts))


@lru_cache(maxsize=64)
def _analyze(draft_json: str, answered: tuple[str, ...]) -> Analysis:
    case = Case.model_construct(draft=json.loads(draft_json), provenance={}, answered=list(answered),
                                confirmed=False, history=[], last_report=None)
    if gaps(case):
        return Analysis((), ())
    impacts, quiet = [], []
    open_questions = [q for q in QUESTIONS
                      if q.id not in answered and get_path(case.draft, q.path) in (None, UNKNOWN)]
    for question in open_questions:
        impact = _impact(case, question, open_questions)
        if impact:
            impacts.append(impact)
        else:
            quiet.append(question)
    impacts.sort(key=lambda i: (-i.spread, -len(i.rules), QUESTIONS.index(i.question)))
    return Analysis(tuple(impacts), tuple(quiet))


def analyze(case: Case) -> Analysis:
    return _analyze(json.dumps(case.draft, sort_keys=True), tuple(sorted(case.answered)))


def phase(case: Case) -> Phase:
    if gaps(case):
        return Phase.GATHER
    if analyze(case).ask:
        return Phase.CLARIFY
    return Phase.REPORT if case.confirmed else Phase.CONFIRM


def question_text(lang: str, question: Question) -> str:
    return t(lang, question.text_key)


def option_label(lang: str, question: Question, option: str) -> str:
    return t(lang, f"{question.option_key}.{option}")


def what_changes(lang: str, impact: Impact) -> str:
    titles = "、".join(f"「{t(lang, f'rule.{rid}.title')}」" for rid in impact.rules)
    note = t(lang, "agent.changes", rules=titles)
    if impact.spread:
        note += t(lang, "agent.changes.amount", amount=f"{impact.spread:,}")
    return note


def describe(case: Case, lang: str) -> dict:
    """The pending questions as the model and the UI read them."""
    analysis = analyze(case)
    return {
        "questions": [
            {"id": i.question.id, "field": i.question.path, "question": question_text(lang, i.question),
             "options": [{"value": o, "label": option_label(lang, i.question, o)} for o in i.question.options],
             "what_changes": what_changes(lang, i)}
            for i in analysis.ask[:TOP]
        ],
        "not_asked": [{"id": q.id, "question": question_text(lang, q)} for q in analysis.not_asked],
        "not_asked_reason": t(lang, "agent.not_asked") if analysis.not_asked else "",
    }
