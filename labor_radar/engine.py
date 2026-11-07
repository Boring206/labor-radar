"""Run every rule over one record and settle what is owed."""

from dataclasses import dataclass, replace
from datetime import date
from fractions import Fraction

from labor_radar import fmt
from labor_radar.model import NTD, PayBasis, WorkRecord
from labor_radar.rules import ledger as ledger_mod
from labor_radar.rules.core import Applicability, Calc, Finding, Gate, Rule, Verdict
from labor_radar.rules.ledger import Ledger
from labor_radar.rules.registry import RULES, cycle_params


@dataclass(frozen=True)
class RuleResult:
    rule: Rule
    gates: dict[Gate, Applicability]
    findings: tuple[Finding, ...]

    @property
    def applies(self) -> bool:
        return Applicability.NOT_APPLICABLE not in self.gates.values()


@dataclass(frozen=True)
class Claim:
    base: Calc | None
    components: tuple[Finding, ...]
    possible: tuple[Finding, ...]
    paid: NTD | None
    paid_assumed: bool

    @property
    def required(self) -> NTD | None:
        if self.base is None:
            return None
        return NTD(self.base.amount + sum((f.amount for f in self.components), Fraction(0)))

    @property
    def shortfall(self) -> NTD | None:
        if self.required is None or self.paid is None:
            return None
        return NTD(max(Fraction(0), self.required - self.paid))

    @property
    def possible_extra(self) -> NTD:
        return NTD(sum((f.amount for f in self.possible), Fraction(0)))


@dataclass(frozen=True)
class Assessment:
    record: WorkRecord
    ledger: Ledger
    results: tuple[RuleResult, ...]
    claim: Claim

    def findings(self, verdict: Verdict) -> tuple[tuple[Rule, Finding], ...]:
        return tuple((r.rule, f) for r in self.results for f in r.findings if f.verdict is verdict)


def _in_effect(rule: Rule, day: date) -> bool:
    return rule.effective_from <= day and (rule.effective_to is None or day <= rule.effective_to)


def _run(rule: Rule, ledger: Ledger) -> RuleResult:
    gates = rule.applicability(ledger.employment)
    if Applicability.NOT_APPLICABLE in gates.values():
        return RuleResult(rule, gates, ())
    uncertain = tuple(g for g, a in gates.items() if a is Applicability.UNCERTAIN)
    findings = []
    period = ledger.record.period
    if not (_in_effect(rule, period.start) and _in_effect(rule, period.end)):
        findings.append(Finding(rule.id, Verdict.NEEDS_HUMAN, "outside_effective", (),
                                {"since": fmt.day(rule.effective_from)}))
    for finding in rule.check(ledger):
        if finding.dates and not any(_in_effect(rule, d) for d in finding.dates):
            continue
        if uncertain and finding.verdict is Verdict.VIOLATION:
            finding = replace(finding, verdict=Verdict.NEEDS_HUMAN, uncertain_gates=uncertain)
        findings.append(finding)
    return RuleResult(rule, gates, tuple(findings))


def _base_pay(ledger: Ledger) -> Calc | None:
    e = ledger.employment
    if e.pay_basis is PayBasis.HOURLY:
        total = sum((d.hours for d in ledger.worked_days()), Fraction(0))
        return Calc("calc.base.hourly", {"hours": fmt.hours(total), "agreed": fmt.money(e.agreed_wage)},
                    NTD(total * e.agreed_wage))
    months = ledger.record.period.whole_months()
    if months is None:
        return None
    return Calc("calc.base.monthly", {"months": len(months), "agreed": fmt.money(e.agreed_wage)},
                NTD(len(months) * e.agreed_wage))


def _settle(results: list[RuleResult], ledger: Ledger) -> tuple[list[RuleResult], Claim]:
    base = _base_pay(ledger)
    paying = [r for r in results if r.rule.pays]
    components = tuple(f for r in paying for f in r.findings if f.verdict is Verdict.VIOLATION and f.amount > 0)
    possible = tuple(f for r in paying for f in r.findings if f.verdict is Verdict.NEEDS_HUMAN and f.amount > 0)
    slips = ledger.record.payslips
    if slips:
        paid, assumed = NTD(sum((s.gross_pay for s in slips), Fraction(0))), False
    else:
        paid, assumed = (base.amount if base else None), True
    claim = Claim(base, components, possible, paid, assumed)
    if claim.shortfall is None or claim.shortfall > 0:
        return results, claim
    settled = [
        replace(r, findings=tuple(
            replace(f, verdict=Verdict.NOTE) if f.verdict is Verdict.VIOLATION and f.amount > 0 else f
            for f in r.findings
        ))
        for r in results
    ]
    return settled, claim


CYCLE_RULE = "rest_day_pay"
WORKED_REGULAR_LEAVE = ("regular_leave", "worked_pay")


def _assess(record: WorkRecord, week_start: int | None) -> Assessment:
    ledger = ledger_mod.build(record, week_start)
    results, claim = _settle([_run(rule, ledger) for rule in RULES], ledger)
    return Assessment(record, ledger, tuple(results), claim)


def _owed(assessment: Assessment) -> tuple[Fraction, Fraction]:
    return sum((f.amount for f in assessment.claim.components), Fraction(0)), assessment.claim.possible_extra


def _cycle_findings(assessment: Assessment) -> tuple[Finding, ...]:
    """Findings that depend on which day is the 休息日 or 例假, and so on the cycle start."""
    return tuple(
        f for r in assessment.results for f in r.findings
        if r.rule.id == CYCLE_RULE or (r.rule.id, f.variant.removesuffix("_assumed")) == WORKED_REGULAR_LEAVE
    )


def assess(record: WorkRecord) -> Assessment:
    """A worker who does not know the 7-day cycle start gets the weekday that owes least,
    like the shortest-day rule for 休息日: the amount holds whichever day the cycle starts.
    When another start would find 休息日 or 例假 work that this one does not, a note says so."""
    if record.employment.week_starts_on is not None:
        return _assess(record, None)
    options = [_assess(record, start) for start in range(7)]
    chosen = min(options, key=_owed)
    if _cycle_findings(chosen) or not any(_cycle_findings(o) for o in options):
        return chosen
    note = Finding(CYCLE_RULE, Verdict.NOTE, "week_start_varies", params=cycle_params(chosen.ledger))
    results = tuple(replace(r, findings=(*r.findings, note)) if r.rule.id == CYCLE_RULE else r
                    for r in chosen.results)
    return replace(chosen, results=results)
