import json
from pathlib import Path

import pytest

from labor_radar.engine import Assessment, assess
from labor_radar.intake import parse_record
from labor_radar.rules.core import Verdict

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def make(shifts, start, end, *, wage=200, payslip=None, **employment):
    """A record for an hourly employee with every nuance answered 'no', unless overridden."""
    job = {
        "pay_basis": "hourly", "agreed_wage": wage, "relationship": "employee", "domestic_worker": False,
        "shift_system": "rotating", "special_hours_arrangement": "no", "responsibility_system": "no",
        "overtime_consent": "no", "week_starts_on": "monday",
    } | employment
    data = {
        "worker": {"name": "測試勞工"}, "employer": {"name": "測試雇主"},
        "employment": job,
        "period": {"start": start, "end": end},
        "shifts": [
            {"date": d, "start": s, "end": e, **({"break_minutes": b[0]} if b else {})}
            for d, s, e, *b in shifts
        ],
    }
    if payslip is not None:
        data["payslips"] = [{"period": {"start": start, "end": end}, "gross_pay": payslip}]
    return data


def run(data) -> Assessment:
    return assess(parse_record(data))


def found(assessment: Assessment, rule_id: str, verdict: Verdict | None = None):
    return [
        f for r in assessment.results if r.rule.id == rule_id for f in r.findings
        if verdict is None or f.verdict is verdict
    ]


@pytest.fixture
def sample():
    return json.loads((SAMPLES / "lin_xiaomei.json").read_text(encoding="utf-8"))


def agent_case(data, **employment):
    """The agent's Case for a full record, with some employment answers replaced."""
    from labor_radar.agent.case import Case

    draft = json.loads(json.dumps(data))
    draft["employment"] |= employment
    draft["payslips"] = [{k: v for k, v in p.items() if k != "period"} for p in draft.get("payslips", [])]
    return Case(draft=draft)
