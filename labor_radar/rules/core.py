"""The shapes every rule shares: findings, gates, and the rule record itself."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from fractions import Fraction
from typing import TYPE_CHECKING

from labor_radar.model import NTD, Answer, Employment, Relationship, ShiftSystem
from labor_radar.rules.law import Citation

if TYPE_CHECKING:
    from labor_radar.rules.ledger import Ledger


class Verdict(StrEnum):
    VIOLATION = "violation"
    NEEDS_HUMAN = "needs_human"
    NOTE = "note"


@dataclass(frozen=True)
class Calc:
    template: str
    params: Mapping[str, object]
    amount: NTD


@dataclass(frozen=True)
class Finding:
    rule_id: str
    verdict: Verdict
    variant: str
    dates: tuple[date, ...] = ()
    params: Mapping[str, object] = field(default_factory=dict)
    calcs: tuple[Calc, ...] = ()
    uncertain_gates: tuple["Gate", ...] = ()

    @property
    def amount(self) -> NTD:
        return NTD(sum((c.amount for c in self.calcs), Fraction(0)))


class Applicability(StrEnum):
    APPLIES = "applies"
    UNCERTAIN = "uncertain"
    NOT_APPLICABLE = "not_applicable"


class Gate(StrEnum):
    """A fact about the job that can switch a rule off. Each one is a nuance the
    engine does not decide: when it is uncertain, a verdict becomes needs_human."""

    RELATIONSHIP = "relationship"
    DOMESTIC_WORKER = "domestic_worker"
    RESPONSIBILITY_SYSTEM = "responsibility_system"
    SPECIAL_HOURS = "special_hours"
    SHIFT_SYSTEM = "shift_system"
    BREAK_EXCEPTION = "break_exception"


def _answer_no_applies(answer: Answer) -> Applicability:
    return Applicability.APPLIES if answer is Answer.NO else Applicability.UNCERTAIN


GATES: dict[Gate, Callable[[Employment], Applicability]] = {
    Gate.RELATIONSHIP: lambda e: (
        Applicability.APPLIES if e.relationship is Relationship.EMPLOYEE else Applicability.UNCERTAIN
    ),
    Gate.DOMESTIC_WORKER: lambda e: (
        Applicability.NOT_APPLICABLE if e.domestic_worker else Applicability.APPLIES
    ),
    Gate.RESPONSIBILITY_SYSTEM: lambda e: _answer_no_applies(e.responsibility_system),
    Gate.SPECIAL_HOURS: lambda e: _answer_no_applies(e.special_hours_arrangement),
    # §34 binds only a 輪班制. A flexible part-time roster may not be one, so it stays uncertain.
    Gate.SHIFT_SYSTEM: lambda e: {
        ShiftSystem.ROTATING: Applicability.APPLIES,
        ShiftSystem.FLEXIBLE: Applicability.UNCERTAIN,
        ShiftSystem.UNKNOWN: Applicability.UNCERTAIN,
        ShiftSystem.FIXED: Applicability.NOT_APPLICABLE,
    }[e.shift_system],
    # §35 但書 lets a 輪班制 employer arrange the break inside working time, where a schedule
    # would not show it. Read the same way as §34: a flexible roster is taken as not rotating.
    Gate.BREAK_EXCEPTION: lambda e: {
        ShiftSystem.ROTATING: Applicability.UNCERTAIN,
        ShiftSystem.UNKNOWN: Applicability.UNCERTAIN,
        ShiftSystem.FLEXIBLE: Applicability.APPLIES,
        ShiftSystem.FIXED: Applicability.APPLIES,
    }[e.shift_system],
}


def reason_key(gate: Gate, employment: Employment) -> str:
    """The catalog key explaining why `gate` left a rule uncertain for this job."""
    if gate is Gate.SHIFT_SYSTEM and employment.shift_system is ShiftSystem.FLEXIBLE:
        return "gate.shift_system.flexible"
    return f"gate.{gate.value}"

UNIVERSAL_GATES = frozenset({Gate.RELATIONSHIP, Gate.DOMESTIC_WORKER})


@dataclass(frozen=True)
class Rule:
    id: str
    citations: tuple[Citation, ...]
    effective_from: date
    gates: frozenset[Gate]
    evidence: tuple[str, ...]
    pays: bool
    check: Callable[["Ledger"], Iterable[Finding]]
    effective_to: date | None = None

    def applicability(self, employment: Employment) -> dict[Gate, Applicability]:
        return {gate: GATES[gate](employment) for gate in sorted(self.gates | UNIVERSAL_GATES)}
