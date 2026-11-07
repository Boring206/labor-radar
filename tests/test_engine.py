"""Gates, settlement against the payslip, the recorded sample, and the boundary."""

import json
import re
from pathlib import Path

import pytest
from conftest import make, run

from labor_radar import report
from labor_radar.i18n import CATALOGS, REFERENCE, is_draft
from labor_radar.intake import IntakeError, parse_record
from labor_radar.rules import law
from labor_radar.rules.core import Verdict
from labor_radar.rules.registry import RULES

WEEK = ("2026-10-12", "2026-10-18")
LONG_DAY = [("2026-10-12", "08:00", "20:00", 60)]


def test_contractor_claim_turns_every_violation_into_needs_human():
    a = run(make(LONG_DAY, *WEEK, wage=180, relationship="contractor"))
    assert a.findings(Verdict.VIOLATION) == ()
    assert {f.rule_id for _, f in a.findings(Verdict.NEEDS_HUMAN)} >= {"minimum_wage", "overtime_pay"}
    assert all("relationship" in [g.value for g in f.uncertain_gates]
               for _, f in a.findings(Verdict.NEEDS_HUMAN))


def test_domestic_worker_gets_no_labor_standards_verdicts():
    a = run(make(LONG_DAY, *WEEK, wage=180, domestic_worker=True))
    assert all(r.findings == () for r in a.results)
    assert "家事勞工" in report.render(a)["not_checked"][0]


def test_responsibility_system_suspends_hours_rules_but_not_minimum_wage():
    a = run(make(LONG_DAY, *WEEK, wage=180, responsibility_system="unknown"))
    assert [f.rule_id for _, f in a.findings(Verdict.VIOLATION)] == ["minimum_wage"]
    assert "overtime_pay" in {f.rule_id for _, f in a.findings(Verdict.NEEDS_HUMAN)}


def test_needs_human_amounts_stay_out_of_the_shortfall():
    a = run(make(LONG_DAY, *WEEK, special_hours_arrangement="unknown"))
    assert a.claim.shortfall == 0
    assert a.claim.possible_extra > 0


def test_payslip_that_covers_the_premiums_settles_the_claim():
    owed = 11 * 200 + 2 * 200 / 3 + 1 * 200 * 2 / 3
    a = run(make(LONG_DAY, *WEEK, payslip=round(owed) + 1))
    assert a.claim.shortfall == 0
    assert a.findings(Verdict.VIOLATION) == ()
    assert [f.rule_id for _, f in a.findings(Verdict.NOTE) if f.rule_id == "overtime_pay"] == ["overtime_pay"]


def test_payslip_short_of_the_premiums_leaves_the_difference():
    a = run(make(LONG_DAY, *WEEK, payslip=2200))
    assert a.claim.paid_assumed is False
    assert report.render(a)["claim"]["shortfall"] == "267"


def test_sample_worker_has_exactly_three_violations_and_owes_1856(sample):
    view = report.render(run(sample))
    assert [v["rule_id"] for v in view["violations"]] == ["minimum_wage", "rest_break", "holiday_pay"]
    assert [v["rule_id"] for v in view["needs_human"]] == ["shift_rest"]
    assert view["claim"]["required"] == "9,996"
    assert view["claim"]["paid"] == "8,140"
    assert view["claim"]["shortfall"] == "1856"
    assert "1,856" in view["mediation_draft"]


def test_shift_rest_verdict_quotes_the_rotating_shift_answer_it_rests_on(sample):
    view = report.render(run(sample | {"employment": sample["employment"] | {"shift_system": "rotating"}}))
    [item] = [v for v in view["violations"] if v["rule_id"] == "shift_rest"]
    assert CATALOGS[REFERENCE]["ui.shift_system.rotating"] in item["explanation"]


@pytest.mark.parametrize("bad, problem", [
    ({"period": {"start": "2026-10-18", "end": "2026-10-05"}}, "period"),
    ({"shifts": [{"date": "2026-11-01", "start": "09:00", "end": "12:00"}]}, "outside the period"),
    ({"shifts": [{"date": "2026-10-05", "start": "09:00", "end": "12:00", "break_minutes": 180}]}, "break"),
    ({"shifts": [{"date": "2026-10-05", "start": "09:00", "end": "12:00"},
                 {"date": "2026-10-05", "start": "11:00", "end": "13:00"}]}, "overlaps"),
    ({"surprise": 1}, "surprise"),
    ({"payslips": [{"period": {"start": "2026-10-01", "end": "2026-10-31"}, "gross_pay": 16280}]}, "payslip"),
])
def test_intake_rejects_records_the_engine_cannot_trust(sample, bad, problem):
    with pytest.raises(IntakeError) as error:
        parse_record(sample | bad)
    assert any(problem in p for p in error.value.problems)


def test_every_template_renders_in_every_language(sample):
    cases = [sample, make(LONG_DAY, *WEEK, wage=180, relationship="unknown", special_hours_arrangement="unknown"),
             make([], "2026-10-05", "2026-10-31", pay_basis="monthly", agreed_wage=20000),
             make([("2027-01-04", "09:00", "13:00")], "2027-01-04", "2027-02-07", shift_system="fixed")]
    for lang in CATALOGS:
        for data in cases:
            json.dumps(report.render(run(data), lang), ensure_ascii=False)


def test_translations_are_marked_as_machine_drafts_and_add_no_keys():
    assert not is_draft(REFERENCE)
    for lang, catalog in CATALOGS.items():
        if lang != REFERENCE:
            assert is_draft(lang)
            assert set(catalog) <= set(CATALOGS[REFERENCE])


def test_every_rule_has_a_title_and_every_citation_is_quoted_in_sources():
    sources = (Path(law.__file__).parent / "sources.md").read_text(encoding="utf-8")
    for rule in RULES:
        assert f"rule.{rule.id}.title" in CATALOGS[REFERENCE]
        for citation in rule.citations:
            assert citation.url in sources, citation.label
            assert re.search(rf"^## {re.escape(citation.law)}第{re.escape(citation.flno)}條", sources, re.M), citation.label


def test_a_settled_payslip_does_not_excuse_working_seven_days_in_a_row():
    days = [f"2026-10-{d:02d}" for d in range(12, 19)]
    a = run(make([(d, "09:00", "13:00") for d in days], days[0], days[-1], payslip=100000))
    assert a.claim.shortfall == 0
    assert [f.rule_id for _, f in a.findings(Verdict.VIOLATION)] == ["regular_leave"]
    assert "例假（七休一）" in report.render(a)["mediation_draft"]
