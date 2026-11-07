"""Which questions the agent asks, and when the conversation moves on. Expected values come from the law
and the engine's gates, checked against what each answer does to the findings."""

import pytest
from conftest import agent_case, make

from labor_radar.agent import questions
from labor_radar.agent.case import Case, Phase, gaps, materialize
from labor_radar.engine import assess
from labor_radar.intake import parse_record
from labor_radar.rules.core import Verdict

LONG_DAY = [("2026-10-12", "08:00", "20:00", 60)]
WEEK = ("2026-10-12", "2026-10-18")


def verdicts(case, rule_id, **answers):
    assessment = assess(parse_record(materialize(case, answers)))
    return {f.verdict for r in assessment.results if r.rule.id == rule_id for f in r.findings}


def asked(case):
    return [i.question.id for i in questions.analyze(case).ask]


def test_shift_system_decides_whether_the_nine_hour_gap_counts(sample):
    case = agent_case(sample, shift_system="unknown")
    assert verdicts(case, "shift_rest", **{"employment.shift_system": "rotating"}) == {Verdict.VIOLATION}
    assert verdicts(case, "shift_rest", **{"employment.shift_system": "flexible"}) == {Verdict.NEEDS_HUMAN}
    assert verdicts(case, "shift_rest", **{"employment.shift_system": "fixed"}) == set()
    [impact] = questions.analyze(case).ask
    assert impact.question.id == "shift_system"
    assert {"shift_rest", "rest_break"} <= set(impact.rules)
    assert questions.phase(case) is Phase.CLARIFY


def test_overtime_consent_is_not_asked_when_no_month_nears_the_cap(sample):
    case = agent_case(sample, overtime_consent="unknown")
    assert [q.id for q in questions.analyze(case).not_asked] == ["overtime_consent"]
    assert questions.phase(case) is Phase.CONFIRM


def test_consent_is_asked_once_a_month_passes_46_overtime_hours():
    """16 days of 11 hours is 48 hours over the daily 8: past 46, within the 54 that consent allows."""
    shifts = [(f"2026-10-{d:02d}", "08:00", "20:00", 60) for d in range(1, 32, 2)]
    case = agent_case(make(shifts, "2026-10-01", "2026-10-31", overtime_consent="unknown"))
    assert verdicts(case, "monthly_overtime_cap", **{"employment.overtime_consent": "no"}) == {Verdict.VIOLATION}
    assert verdicts(case, "monthly_overtime_cap", **{"employment.overtime_consent": "yes"}) == {Verdict.NEEDS_HUMAN}
    assert "overtime_consent" in asked(case)


def test_open_gates_do_not_hide_each_other():
    case = agent_case(make(LONG_DAY, *WEEK, wage=180, relationship="unknown", special_hours_arrangement="unknown",
                           responsibility_system="unknown", shift_system="fixed", week_starts_on="monday"))
    assert asked(case) == ["relationship", "special_hours_arrangement", "responsibility_system"]


def test_the_larger_amount_difference_is_asked_first(sample):
    case = agent_case(sample, relationship="unknown", responsibility_system="unknown", shift_system="unknown")
    assert asked(case)[:2] == ["relationship", "responsibility_system"]
    impacts = {i.question.id: i for i in questions.analyze(case).ask}
    assert impacts["relationship"].spread > impacts["responsibility_system"].spread > impacts["shift_system"].spread


def test_an_answered_question_is_not_asked_again_even_if_the_answer_was_dont_know(sample):
    case = agent_case(sample, shift_system="unknown")
    case.answered.append("shift_system")
    assert asked(case) == []
    assert questions.phase(case) is Phase.CONFIRM


def test_asking_changes_nothing_in_the_case(sample):
    case = agent_case(sample, shift_system="unknown")
    before = case.model_dump()
    questions.analyze(case)
    questions.describe(case, "zh-TW")
    assert case.model_dump() == before


def test_week_start_matters_when_seven_days_are_worked_in_a_row():
    days = [f"2026-10-{d:02d}" for d in range(12, 19)]
    case = agent_case(make([(d, "09:00", "13:00") for d in days], days[0], days[-1], week_starts_on="unknown"))
    assert asked(case) == ["week_starts_on"]


def test_what_changes_names_the_rules_and_the_amount_from_the_engine(sample):
    case = agent_case(sample, relationship="unknown", responsibility_system="unknown")
    top = questions.describe(case, "zh-TW")["questions"][0]
    assert top["id"] == "relationship"
    assert "「最低工資」" in top["what_changes"]
    impact = questions.analyze(case).ask[0]
    assert f"{impact.spread:,}" in top["what_changes"]
    assert [o["value"] for o in top["options"]] == ["employee", "contractor"]


@pytest.mark.parametrize("shifts, needs", [
    ([], ["shifts"]),
    ([("2026-10-12", "09:00", "13:00")], []),
])
def test_phase_is_gather_until_the_required_facts_exist(shifts, needs):
    case = agent_case(make(shifts, *WEEK))
    assert gaps(case) == needs
    assert (questions.phase(case) is Phase.GATHER) == bool(needs)


def test_a_draft_the_engine_would_reject_stays_in_gather_with_the_reason():
    case = agent_case(make([("2026-10-12", "09:00", "13:00"), ("2026-10-12", "12:00", "15:00")], *WEEK))
    assert questions.phase(case) is Phase.GATHER
    assert any("overlaps" in problem for problem in gaps(case))


def test_confirmation_is_only_the_last_step(sample):
    case = agent_case(sample)
    assert questions.phase(case) is Phase.CONFIRM
    assert questions.phase(case.model_copy(update={"confirmed": True})) is Phase.REPORT
    open_question = agent_case(sample, shift_system="unknown").model_copy(update={"confirmed": True})
    assert questions.phase(open_question) is Phase.CLARIFY


def test_a_client_cannot_skip_the_required_facts_by_claiming_confirmation():
    case = Case(confirmed=True)
    assert questions.phase(case) is Phase.GATHER
