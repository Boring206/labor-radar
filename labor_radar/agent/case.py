"""What the agent knows about one worker. The client holds it between turns, so it is untrusted input.

The draft is a partial RecordIn. `materialize` fills the rest with stated defaults, and
`intake.parse_record` stays the only way from here to a WorkRecord.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from labor_radar.i18n import t
from labor_radar.intake import IntakeError, ShiftIn, parse_record
from labor_radar.model import WEEKDAYS, Answer, PayBasis, Relationship, ShiftSystem

HISTORY_CAP = 20
MAX_SHIFTS = 200
"""Shifts one case may hold. A pay period runs a month at most, so this leaves room for several. phase()
re-runs the engine for each answer of each open question, and at 200 shifts that takes about a second."""
UNSET_NAME = "未提供"
SHIFT_KEY = "shift:{date}T{start}"


class Source(StrEnum):
    PHOTO = "photo"
    USER = "user"
    DEFAULT = "default"


class Phase(StrEnum):
    GATHER = "gather"
    CLARIFY = "clarify"
    CONFIRM = "confirm"
    REPORT = "report"


def _text(limit: int) -> Callable[[Any], str]:
    def parse(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        if len(value.strip()) > limit:
            raise ValueError(f"must be at most {limit} characters")
        return value.strip()
    return parse


def _choice(values: tuple[str, ...]) -> Callable[[Any], str]:
    def parse(value: Any) -> str:
        if not isinstance(value, str) or value not in values:
            raise ValueError(f"must be exactly one of: {', '.join(values)}; got {value!r}")
        return value
    return parse


def _number(low: int, high: int, *, low_open: bool) -> Callable[[Any], int | float]:
    def parse(value: Any) -> int | float:
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            raise ValueError("must be a number")
        try:
            number = Decimal(str(value).replace(",", "").strip())
        except InvalidOperation:
            raise ValueError(f"must be a number; got {value!r}") from None
        if not number.is_finite() or number < low or number > high or (low_open and number == low):
            bound = f"greater than {low}" if low_open else f"at least {low}"
            raise ValueError(f"must be {bound} and at most {high}")
        return int(number) if number == number.to_integral_value() else float(number)
    return parse


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in ("true", "false"):
        return value == "true"
    raise ValueError(f"must be true or false; got {value!r}")


def _day(value: Any) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"must be a date written YYYY-MM-DD; got {value!r}") from None


def _values(enum: type[StrEnum]) -> tuple[str, ...]:
    return tuple(member.value for member in enum)


@dataclass(frozen=True)
class FieldSpec:
    path: str
    parse: Callable[[Any], Any]
    required: bool = False
    default: Any = None
    """What `materialize` writes when the draft has no value. None leaves the key out."""
    assumed: bool = False
    """The default is a guess about the worker, so the confirm table flags it."""
    free_text: bool = False
    """Words rather than a typed value. Read from a photo, they may hold any number, so they vouch for none."""


ANSWERS = _values(Answer)
_SPECS = (
    FieldSpec("worker.name", _text(100), default=UNSET_NAME, free_text=True),
    FieldSpec("worker.address", _text(200), free_text=True),
    FieldSpec("worker.phone", _text(40), free_text=True),
    FieldSpec("employer.name", _text(100), default=UNSET_NAME, free_text=True),
    FieldSpec("employer.address", _text(200), free_text=True),
    FieldSpec("employer.representative", _text(100), free_text=True),
    FieldSpec("city", _text(20), free_text=True),
    FieldSpec("employment.pay_basis", _choice(_values(PayBasis)), required=True),
    FieldSpec("employment.agreed_wage", _number(0, 1_000_000, low_open=True), required=True),
    FieldSpec("employment.agreed_monthly_hours", _number(0, 744, low_open=True)),
    FieldSpec("employment.relationship", _choice(_values(Relationship)), default="unknown"),
    FieldSpec("employment.domestic_worker", _flag, default=False, assumed=True),
    FieldSpec("employment.shift_system", _choice(_values(ShiftSystem)), default="unknown"),
    FieldSpec("employment.special_hours_arrangement", _choice(ANSWERS), default="unknown"),
    FieldSpec("employment.responsibility_system", _choice(ANSWERS), default="unknown"),
    FieldSpec("employment.overtime_consent", _choice(ANSWERS), default="unknown"),
    FieldSpec("employment.week_starts_on", _choice((*WEEKDAYS, "unknown")), default="unknown"),
    FieldSpec("period.start", _day),
    FieldSpec("period.end", _day),
    FieldSpec("payslips.gross_pay", _number(0, 10_000_000, low_open=False)),
    FieldSpec("payslips.self_reported", _flag, default=False),
)
FIELDS: dict[str, FieldSpec] = {spec.path: spec for spec in _SPECS}
REQUIRED = ("employment.pay_basis", "employment.agreed_wage", "shifts")


def get_path(data: dict, path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def put_path(data: dict, path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    node = data
    for part in parents:
        child = node.get(part)
        if part == "payslips":
            node[part] = child or [{}]
            child = node[part][0]
        elif not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[leaf] = value


def normalize_shift(raw: Any) -> dict:
    """The one shape a draft shift takes. Raises ValueError with the reason."""
    if not isinstance(raw, dict):
        raise ValueError("a shift must be an object with date, start, end, break_minutes")
    try:
        shift = ShiftIn.model_validate(raw)
    except ValidationError as error:
        raise ValueError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())) from None
    return {"date": shift.date.isoformat(), "start": shift.start.strftime("%H:%M"),
            "end": shift.end.strftime("%H:%M"), "break_minutes": shift.break_minutes}


def shift_key(shift: dict) -> str:
    return SHIFT_KEY.format(date=shift["date"], start=shift["start"])


def _draft_problems(draft: Any) -> list[str]:
    if not isinstance(draft, dict):
        return ["draft must be an object"]
    problems = []
    for key, value in draft.items():
        if key == "shifts":
            if not isinstance(value, list) or len(value) > MAX_SHIFTS:
                problems.append(f"draft.shifts must be a list of at most {MAX_SHIFTS} shifts")
                continue
            for index, raw in enumerate(value):
                try:
                    normalize_shift(raw)
                except ValueError as error:
                    problems.append(f"draft.shifts[{index}]: {error}")
        elif key == "payslips":
            if not (isinstance(value, list) and len(value) <= 1 and all(isinstance(p, dict) for p in value)):
                problems.append("draft.payslips must hold at most one object")
                continue
            for name, item in (value[0] if value else {}).items():
                problems += _leaf_problems(f"payslips.{name}", item)
        elif isinstance(value, dict):
            for name, item in value.items():
                problems += _leaf_problems(f"{key}.{name}", item)
        else:
            problems += _leaf_problems(key, value)
    return problems


def _leaf_problems(path: str, value: Any) -> list[str]:
    spec = FIELDS.get(path)
    if spec is None:
        return [f"draft.{path} is not a known field"]
    try:
        spec.parse(value)
    except ValueError as error:
        return [f"draft.{path}: {error}"]
    return []


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(_Strict):
    source: Source
    quote: str = Field("", max_length=300)


class Turn(_Strict):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=6000)
    tapped: bool = False
    """The worker tapped an option instead of typing. The app wrote the text, so nothing is quoted from it."""


class Case(_Strict):
    draft: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Provenance] = Field(default_factory=dict)
    answered: list[str] = Field(default_factory=list, max_length=30)
    confirmed: bool = False
    """Set only by the confirm endpoint. Every tool that changes a fact clears it."""
    history: list[Turn] = Field(default_factory=list, max_length=HISTORY_CAP)
    reported: bool = False
    """run_check has shown the engine's report on these facts. The report itself is never sent: the server
    computes it again from the facts, so nothing the client holds can stand in for it."""

    @field_validator("draft")
    @classmethod
    def _known_draft(cls, draft: dict) -> dict:
        if problems := _draft_problems(draft):
            raise ValueError("; ".join(problems))
        return {**draft, **({"shifts": [normalize_shift(s) for s in draft["shifts"]]} if "shifts" in draft else {})}

    @field_validator("provenance")
    @classmethod
    def _known_provenance(cls, provenance: dict) -> dict:
        for key in provenance:
            if key not in FIELDS and not key.startswith("shift:"):
                raise ValueError(f"provenance key {key!r} is not a known field")
        return provenance

    @field_validator("answered")
    @classmethod
    def _answered_ids(cls, answered: list[str]) -> list[str]:
        if not all(isinstance(a, str) and a.isidentifier() and len(a) <= 40 for a in answered):
            raise ValueError("answered holds question ids")
        return list(dict.fromkeys(answered))

    def changed(self) -> "Case":
        """The same facts after a change: whatever the worker confirmed or read no longer holds."""
        return self.model_copy(update={"confirmed": False, "reported": False})

    def user_text(self) -> str:
        return "\n".join(turn.text for turn in self.history if turn.role == "user")

    def typed_now(self) -> str:
        """What the worker typed in the message being answered, the only words a fact may quote."""
        last = next((turn for turn in reversed(self.history) if turn.role == "user"), None)
        return last.text if last is not None and not last.tapped else ""


def facts_state(case: Case) -> dict:
    """Everything a confirmation or a report depends on, to tell whether a tool changed it."""
    return case.model_dump(include={"draft", "provenance", "answered"})


def with_turn(case: Case, role: Literal["user", "assistant"], text: str, *, tapped: bool = False) -> Case:
    history = [*case.history, Turn(role=role, text=text[:6000], tapped=tapped)][-HISTORY_CAP:]
    return case.model_copy(update={"history": history})


def _derived_period(draft: dict) -> dict | None:
    days = sorted(s["date"] for s in draft.get("shifts", []))
    return {"start": days[0], "end": days[-1]} if days else None


def materialize(case: Case, overrides: dict[str, Any] | None = None) -> dict:
    """A full RecordIn dict, with defaults for what the worker has not said.
    `overrides` maps field paths to values, for asking what an answer would change.
    A payslip without its gross pay says nothing yet and is left out. A gross pay the worker typed counts
    as their own record unless they said it came from a payslip."""
    data = copy.deepcopy(case.draft)
    for path, value in (overrides or {}).items():
        put_path(data, path, value)
    for spec in _SPECS:
        if get_path(data, spec.path) is None and spec.default is not None and not spec.path.startswith("payslips"):
            put_path(data, spec.path, spec.default)
    data.setdefault("shifts", [])
    period = data.get("period") or {}
    derived = _derived_period(data)
    if derived:
        data["period"] = {**derived, **period}
    slips = [slip for slip in data.pop("payslips", []) if "gross_pay" in slip]
    origin = source_of(case, "payslips.gross_pay")
    typed = origin.source is Source.USER and not any(word in origin.quote for word in PAYSLIP_WORDS)
    for slip in slips:
        slip.setdefault("self_reported", typed)
        slip["period"] = dict(data.get("period", {}))
    if slips:
        data["payslips"] = slips
    return data


def gaps(case: Case) -> list[str]:
    """What stops the engine from running: missing required facts, then whatever intake rejects."""
    data = materialize(case)
    missing = [p for p in REQUIRED if not (get_path(data, p) if p != "shifts" else data["shifts"])]
    if missing:
        return missing
    try:
        parse_record(data)
    except IntakeError as error:
        return error.problems
    return []


PAYSLIP_WORDS = ("薪資單", "薪資條", "薪水單", "薪資明細", "薪轉明細")


def source_of(case: Case, key: str) -> Provenance:
    return case.provenance.get(key) or Provenance(source=Source.DEFAULT)


@dataclass(frozen=True)
class FactRow:
    path: str
    label: str
    value: str
    source: Source
    quote: str
    assumed: bool = False
    missing: bool = False

    def as_dict(self) -> dict:
        return {"path": self.path, "label": self.label, "value": self.value, "source": self.source.value,
                "quote": self.quote, "assumed": self.assumed, "missing": self.missing}


_OPTION_KEYS = {
    "employment.pay_basis": "ui.pay_basis", "employment.relationship": "ui.relationship",
    "employment.shift_system": "ui.shift_system", "employment.special_hours_arrangement": "ui.answer",
    "employment.responsibility_system": "ui.answer", "employment.overtime_consent": "ui.answer",
    "employment.week_starts_on": "ui.weekday",
}


def display(lang: str, path: str, value: Any) -> str:
    if value is None:
        return t(lang, "agent.value.missing")
    if isinstance(value, bool):
        return t(lang, "agent.value.yes" if value else "agent.value.no")
    prefix = _OPTION_KEYS.get(path)
    return t(lang, f"{prefix}.{value}") if prefix else str(value)


def fact_table(case: Case, lang: str) -> list[FactRow]:
    """Every fact the check will use, with where it came from. Defaults show as defaults."""
    data = materialize(case)
    rows = []
    for spec in _SPECS:
        if spec.path.startswith("payslips") and not case.draft.get("payslips"):
            continue
        value = get_path(data, spec.path)
        stated = get_path(case.draft, spec.path) is not None
        if value is None and not spec.required:
            continue
        prov = source_of(case, spec.path) if stated else Provenance(source=Source.DEFAULT)
        shown = t(lang, "agent.value.unset_name") if value == UNSET_NAME and spec.path.endswith(".name") \
            else display(lang, spec.path, value)
        rows.append(FactRow(spec.path, t(lang, f"fact.{spec.path}"), shown, prov.source, prov.quote,
                            assumed=spec.assumed and not stated, missing=value is None))
    if not case.draft.get("shifts"):
        rows.append(FactRow("shifts", t(lang, "fact.shifts"), t(lang, "agent.value.missing"),
                            Source.DEFAULT, "", missing=True))
    for shift in sorted(case.draft.get("shifts", []), key=lambda s: (s["date"], s["start"])):
        prov = source_of(case, shift_key(shift))
        rest = t(lang, "agent.shift_break", minutes=shift["break_minutes"]) if shift["break_minutes"] else ""
        rows.append(FactRow(shift_key(shift), t(lang, "fact.shifts"),
                            f"{shift['date']} {shift['start']}–{shift['end']}{rest}", prov.source, prov.quote))
    return rows
