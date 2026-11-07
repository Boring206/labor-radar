"""The tools are the model's only hands. These tests call them as the model would, including badly."""

from datetime import time

import pytest
from conftest import agent_case, make
from pydantic import ValidationError

from labor_radar import extraction
from labor_radar.agent import questions, tools
from labor_radar.agent.case import Case, Phase, Source, gaps, get_path, materialize, with_turn
from labor_radar.agent.tools import Attachment, Ctx
from labor_radar.intake import parse_record

WEEK_ = ("2026-10-12", "2026-10-18")
SAID = "我在手搖飲打工，時薪185，10/7 17:00到23:00。我不知道老闆算不算輪班制"


def said(case, text=SAID):
    return with_turn(case, "user", text)


def ctx(**kwargs):
    return Ctx(lang="zh-TW", **kwargs)


def names(case, **kwargs):
    return [tool.name for tool in tools.available(case, ctx(**kwargs))]


def fact(path, value, quote):
    return {"path": path, "value": value, "quote": quote}


def test_run_check_is_invisible_and_refused_until_the_worker_confirms(sample):
    case = agent_case(sample)
    assert questions.phase(case) is Phase.CONFIRM
    assert "run_check" not in names(case)
    refused_case, result = tools.call(case, "run_check", {}, ctx())
    assert "not available" in result["error"] and refused_case.reported is False
    confirmed = case.model_copy(update={"confirmed": True})
    assert "run_check" in names(confirmed)
    _, report = tools.call(confirmed, "run_check", {}, ctx())
    assert report["violation_count"] == 3


@pytest.mark.parametrize("tool", ["run_check", "draft_mediation", "explain_rule", "find_help"])
def test_report_tools_are_closed_in_every_phase_before_the_report(tool):
    gather = Case()
    clarify = agent_case(make([("2026-10-12", "09:00", "13:00")], "2026-10-12", "2026-10-18",
                              relationship="unknown", wage=100))
    for case in (gather, clarify):
        assert tool not in names(case)
        assert "not available" in tools.call(case, tool, {}, ctx())[1]["error"]


def test_a_changed_fact_after_confirmation_puts_the_worker_back_at_the_table(sample):
    confirmed = said(agent_case(sample), "改成時薪200").model_copy(update={"confirmed": True})
    confirmed, _ = tools.call(confirmed, "run_check", {}, ctx())
    changed, result = tools.call(confirmed, "set_facts",
                                 {"facts": [fact("employment.agreed_wage", 200, "時薪200")]}, ctx())
    assert changed.confirmed is False and changed.reported is False
    assert result["phase"] == "confirm"
    assert "run_check" not in names(changed)


def test_restating_a_fact_does_not_undo_a_confirmation(sample):
    stated = [fact("employment.agreed_wage", 185, "時薪185")]
    case, _ = tools.call(said(agent_case(sample), "時薪185"), "set_facts", {"facts": stated}, ctx())
    case = case.model_copy(update={"confirmed": True})
    same, result = tools.call(case, "set_facts", {"facts": stated}, ctx())
    assert same.confirmed and result["phase"] == "report"


def test_saying_who_a_fact_came_from_changes_the_table_so_it_asks_again(sample):
    case = said(agent_case(sample), "時薪185").model_copy(update={"confirmed": True})
    changed, _ = tools.call(case, "set_facts", {"facts": [fact("employment.agreed_wage", 185, "時薪185")]}, ctx())
    assert changed.confirmed is False


def test_a_value_the_model_invents_is_rejected_with_the_allowed_ones_listed():
    case = said(Case())
    kept, result = tools.call(case, "set_facts",
                              {"facts": [fact("employment.shift_system", "weekly shifts", "輪班制")]}, ctx())
    assert result["error"] == "no fact was stored"
    [rejected] = result["rejected"]
    for allowed in ("rotating", "flexible", "fixed", "unknown"):
        assert allowed in rejected["error"]
    assert "weekly shifts" in rejected["error"] and kept == case


def test_one_bad_fact_does_not_block_the_good_ones():
    case = said(Case())
    stored, result = tools.call(case, "set_facts", {"facts": [
        fact("employment.pay_basis", "hourly", "時薪185"),
        fact("employment.agreed_wage", "a lot", "時薪185"),
        fact("employment.agreed_wage", 185, "時薪185"),
    ]}, ctx())
    assert result["applied"] == ["employment.pay_basis", "employment.agreed_wage"]
    assert [r["path"] for r in result["rejected"]] == ["employment.agreed_wage"]
    assert stored.draft["employment"] == {"pay_basis": "hourly", "agreed_wage": 185}


def test_a_stored_fact_remembers_who_said_it_and_the_words():
    case = said(Case())
    stored, _ = tools.call(case, "set_facts", {"facts": [fact("employment.agreed_wage", 185, "時薪185")]}, ctx())
    provenance = stored.provenance["employment.agreed_wage"]
    assert provenance.source is Source.USER and provenance.quote == "時薪185"


@pytest.mark.parametrize("quote", ["", None, "時薪二百", "我時薪 185 元整", "x" * 400])
def test_a_quote_the_worker_never_wrote_is_refused(quote):
    case = said(Case())
    kept, result = tools.call(case, "set_facts", {"facts": [fact("employment.agreed_wage", 185, quote)]}, ctx())
    assert "quote" in result["rejected"][0]["error"] and kept == case


def test_quote_matching_ignores_spacing_and_full_width_digits():
    case = said(Case(), "時薪１８５ 元")
    stored, result = tools.call(case, "set_facts", {"facts": [fact("employment.agreed_wage", 185, "時薪185元")]}, ctx())
    assert result["applied"] == ["employment.agreed_wage"]


def test_dont_know_is_an_answer_so_the_question_is_not_asked_again(sample):
    case = said(agent_case(sample, shift_system="unknown"))
    assert questions.phase(case) is Phase.CLARIFY
    skipped, _ = tools.call(case, "set_facts",
                            {"facts": [fact("employment.shift_system", "unknown", "我不知道老闆算不算輪班制")]}, ctx())
    assert skipped.draft["employment"]["shift_system"] == "unknown"
    assert "shift_system" in skipped.answered
    assert questions.phase(skipped) is Phase.CONFIRM


def test_a_shift_over_midnight_is_kept_as_intake_reads_it():
    said_text = "時薪185，10/7 22:00 到 02:00"
    case = said(Case(), said_text)
    midnight = [{"date": "2026-10-07", "start": "22:00", "end": "02:00"}]
    stored, result = tools.call(case, "set_facts", {"facts": [
        fact("employment.pay_basis", "hourly", "時薪185"), fact("employment.agreed_wage", 185, "時薪185"),
        fact("shifts", midnight, "10/7 22:00 到 02:00"),
    ]}, ctx())
    assert result["phase"] != "gather"
    [shift] = parse_record(materialize(stored)).shifts
    assert shift.crosses_midnight and shift.hours == 4


@pytest.mark.parametrize("shift", [
    {"date": "10/7", "start": "17:00", "end": "23:00"},
    {"date": "2026-10-07", "start": "25:00", "end": "23:00"},
    {"date": "2026-10-07", "start": "17:00"},
    {"date": "2026-10-07", "start": "17:00", "end": "23:00", "color": "red"},
    "17:00-23:00",
])
def test_a_malformed_shift_is_refused_without_touching_the_draft(shift):
    case = said(Case(), "10/7 17:00-23:00")
    kept, result = tools.call(case, "set_facts", {"facts": [fact("shifts", [shift], "10/7 17:00-23:00")]}, ctx())
    assert result["error"] == "no fact was stored" and kept == case


def test_a_shift_whose_break_swallows_it_is_stored_and_blocks_the_check_until_fixed():
    case = said(Case(), "10/7 17:00-18:00 休息 90 分鐘，時薪185")
    base = [fact("employment.pay_basis", "hourly", "時薪185"), fact("employment.agreed_wage", 185, "時薪185")]
    bad = [{"date": "2026-10-07", "start": "17:00", "end": "18:00", "break_minutes": 90}]
    stored, result = tools.call(case, "set_facts", {"facts": [*base, fact("shifts", bad, "17:00-18:00")]}, ctx())
    assert result["phase"] == "gather" and any("break" in g for g in result["gaps"])
    fixed = [{**bad[0], "break_minutes": 0}]
    stored, result = tools.call(stored, "set_facts", {"facts": [fact("shifts", fixed, "17:00-18:00")]}, ctx())
    assert len(stored.draft["shifts"]) == 1 and gaps(stored) == []


def test_adding_the_same_shift_twice_leaves_one():
    case = said(Case(), "10/7 17:00-23:00")
    shift = [{"date": "2026-10-07", "start": "17:00", "end": "23:00"}]
    once, _ = tools.call(case, "set_facts", {"facts": [fact("shifts", shift, "10/7 17:00-23:00")]}, ctx())
    twice, _ = tools.call(once, "set_facts", {"facts": [fact("shifts", shift, "10/7 17:00-23:00")]}, ctx())
    assert twice.draft["shifts"] == once.draft["shifts"] and len(twice.draft["shifts"]) == 1


def test_a_shift_can_be_taken_back():
    case = said(agent_case(make([("2026-10-12", "09:00", "13:00"), ("2026-10-13", "09:00", "13:00")], "2026-10-12", "2026-10-18")),
                "10/13 我沒上班")
    removed, _ = tools.call(case, "set_facts", {"facts": [fact("shifts.remove", ["2026-10-13"], "10/13 我沒上班")]}, ctx())
    assert [s["date"] for s in removed.draft["shifts"]] == ["2026-10-12"]
    _, again = tools.call(removed, "set_facts", {"facts": [fact("shifts.remove", ["2026-10-13"], "10/13 我沒上班")]}, ctx())
    assert "no shift is recorded" in again["rejected"][0]["error"]


@pytest.mark.parametrize("args", [
    {}, {"facts": None}, {"facts": "x"}, {"facts": []}, {"facts": [1]}, {"facts": [{}]}, {"facts": [None]},
    {"facts": [{"path": ["a"], "value": 1, "quote": "x"}]},
    {"facts": [{"path": "draft", "value": 1, "quote": "時薪185"}]},
    {"facts": [{"path": "shifts", "value": "all of october", "quote": "時薪185"}]},
    {"facts": [{"path": "shifts.remove", "value": [3], "quote": "時薪185"}]},
    {"facts": [{"path": "employment.agreed_wage", "value": [185], "quote": "時薪185"}]},
    {"facts": [{"path": "employment.agreed_wage", "value": True, "quote": "時薪185"}]},
    {"facts": [{"path": "employment.agreed_wage", "value": -5, "quote": "時薪185"}]},
    {"facts": [{"path": "employment.agreed_wage", "value": 10**9, "quote": "時薪185"}]},
    {"facts": [{"path": "employment.domestic_worker", "value": "maybe", "quote": "時薪185"}]},
    {"facts": [{"path": "period.start", "value": "tomorrow", "quote": "時薪185"}]},
    {"facts": [{"path": "employment.week_starts_on", "value": "Monday", "quote": "時薪185"}]},
])
def test_set_facts_answers_nonsense_with_an_error_and_never_raises(args):
    case = said(Case(), "時薪185")
    kept, result = tools.call(case, "set_facts", args, ctx())
    assert "error" in result and kept == case


@pytest.mark.parametrize("args", [None, [], "run", 5])
def test_arguments_that_are_not_an_object_are_refused(args):
    case = said(Case())
    assert "error" in tools.call(case, "next_questions", args, ctx())[1]


def test_unknown_tools_are_refused():
    assert "unknown tool" in tools.call(Case(), "delete_everything", {}, ctx())[1]["error"]


def test_the_tool_list_follows_the_phase():
    assert names(Case()) == ["set_facts", "next_questions"]
    report = agent_case(make([("2026-10-12", "09:00", "13:00")], "2026-10-12", "2026-10-18")).model_copy(
        update={"confirmed": True})
    assert names(report) == ["set_facts", "run_check", "explain_rule", "draft_mediation", "find_help"]


def photo(**fields):
    base = {"document_type": "schedule", "shifts": [], "unreadable": []}
    return extraction.Draft.model_validate(base | fields)


def fake_extract(monkeypatch, draft):
    monkeypatch.setattr(extraction, "extract", lambda data, mime, year: draft)


def test_reading_a_photo_needs_an_attachment_and_says_so():
    case = said(Case())
    assert "read_document" not in names(case)
    assert "read_document" in names(case, attachment=Attachment(b"x", "image/png"))
    assert "no file" in tools.call(case, "read_document", {}, ctx())[1]["error"]


def test_a_photo_fills_shifts_and_wage_as_photo_facts(monkeypatch):
    fake_extract(monkeypatch, photo(
        shifts=[{"date": "2026-10-07", "start": "17:00", "end": "23:00"},
                {"date": "2026-10-08", "start": "08:00", "end": "13:00", "break_minutes": 30}],
        hourly_wage=185, unreadable=["10/9 那格被手指擋住"]))
    case = said(Case())
    read, result = tools.call(case, "read_document", {}, ctx(attachment=Attachment(b"x", "image/png", "班表.jpg")))
    assert result["unreadable"] == ["10/9 那格被手指擋住"] and result["phase"] == "clarify"
    assert read.draft["employment"] == {"pay_basis": "hourly", "agreed_wage": 185}
    assert [s["start"] for s in read.draft["shifts"]] == ["17:00", "08:00"]
    sources = {k: v.source for k, v in read.provenance.items()}
    assert set(sources.values()) == {Source.PHOTO}
    assert read.draft["shifts"][1]["break_minutes"] == 30


def test_reading_the_same_photo_twice_adds_nothing(monkeypatch):
    fake_extract(monkeypatch, photo(shifts=[{"date": "2026-10-07", "start": "17:00", "end": "23:00"}], hourly_wage=185))
    case = said(Case())
    attachment = Attachment(b"x", "image/png")
    once, _ = tools.call(case, "read_document", {}, ctx(attachment=attachment))
    twice, _ = tools.call(once, "read_document", {}, ctx(attachment=attachment))
    assert twice.draft == once.draft


def test_a_photo_does_not_overwrite_what_the_worker_said(monkeypatch):
    fake_extract(monkeypatch, photo(hourly_wage=150, shifts=[{"date": "2026-10-07", "start": "17:00", "end": "23:00"}]))
    case = said(Case(), "我的時薪是185")
    case, _ = tools.call(case, "set_facts", {"facts": [fact("employment.agreed_wage", 185, "時薪是185")]}, ctx())
    read, result = tools.call(case, "read_document", {}, ctx(attachment=Attachment(b"x", "image/png")))
    assert read.draft["employment"] == {"agreed_wage": 185}
    assert result["conflicts"] == [{"path": "employment.pay_basis", "kept": None, "photo": "hourly"},
                                   {"path": "employment.agreed_wage", "kept": 185, "photo": 150}]
    assert [s["date"] for s in read.draft["shifts"]] == ["2026-10-07"]


PHOTO_FILE = Attachment(b"x", "image/png")


@pytest.mark.parametrize("fields, path", [
    ({"monthly_wage": 1_500_000}, "employment.agreed_wage"),
    ({"hourly_wage": float("inf")}, "employment.agreed_wage"),
    ({"gross_pay": 12_000_000}, "payslips.gross_pay"),
])
def test_a_photo_value_out_of_range_is_refused_and_the_rest_is_kept(monkeypatch, fields, path):
    fake_extract(monkeypatch, photo(shifts=[{"date": "2026-10-07", "start": "17:00", "end": "23:00"}], **fields))
    read, result = tools.call(said(Case()), "read_document", {}, ctx(attachment=PHOTO_FILE))
    assert [r["path"] for r in result["rejected"]] == [path]
    assert get_path(read.draft, path) is None and get_path(read.draft, "employment.pay_basis") is None
    assert [s["date"] for s in read.draft["shifts"]] == ["2026-10-07"]


def test_a_photo_wage_never_pairs_with_the_pay_type_the_worker_gave(monkeypatch):
    fake_extract(monkeypatch, photo(document_type="payslip", monthly_wage=28000))
    case = said(Case(), "我是領時薪的")
    case, _ = tools.call(case, "set_facts", {"facts": [fact("employment.pay_basis", "hourly", "我是領時薪的")]}, ctx())
    read, result = tools.call(case, "read_document", {}, ctx(attachment=PHOTO_FILE))
    assert read.draft["employment"] == {"pay_basis": "hourly"}
    assert {c["path"] for c in result["conflicts"]} == {"employment.pay_basis", "employment.agreed_wage"}


def test_a_photo_pay_type_never_pairs_with_the_wage_the_worker_gave(monkeypatch):
    fake_extract(monkeypatch, photo(document_type="payslip", monthly_wage=28000))
    case = said(Case(), "我拿185")
    case, _ = tools.call(case, "set_facts", {"facts": [fact("employment.agreed_wage", 185, "我拿185")]}, ctx())
    read, result = tools.call(case, "read_document", {}, ctx(attachment=PHOTO_FILE))
    assert read.draft["employment"] == {"agreed_wage": 185}
    assert read.provenance["employment.agreed_wage"].source is Source.USER
    assert {c["path"] for c in result["conflicts"]} == {"employment.pay_basis", "employment.agreed_wage"}


def test_a_photo_pay_period_never_pairs_with_a_date_the_worker_gave(monkeypatch):
    fake_extract(monkeypatch, photo(document_type="payslip", gross_pay=8000,
                                    pay_period_start="2026-10-05", pay_period_end="2026-10-18"))
    case = said(Case(), "從 2026-10-01 開始算")
    case, _ = tools.call(case, "set_facts", {"facts": [fact("period.start", "2026-10-01", "從 2026-10-01 開始算")]}, ctx())
    read, result = tools.call(case, "read_document", {}, ctx(attachment=PHOTO_FILE))
    assert read.draft["period"] == {"start": "2026-10-01"}
    assert "payslips" not in read.draft
    assert {c["path"] for c in result["conflicts"]} >= {"period.start", "period.end"}


def test_a_tool_that_trips_on_what_came_back_reports_it_instead_of_failing_the_turn(monkeypatch):
    def garbled(data, mime, year):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(extraction, "extract", garbled)
    case = said(Case())
    kept, result = tools.call(case, "read_document", {}, ctx(attachment=PHOTO_FILE))
    assert "read_document could not use this input" in result["error"] and kept == case


def test_a_photo_that_cannot_be_read_is_an_error_the_model_can_report(monkeypatch):
    def refuse(data, mime, year):
        raise extraction.ExtractionError("unsupported file type text/plain", 415)

    monkeypatch.setattr(extraction, "extract", refuse)
    case = said(Case())
    kept, result = tools.call(case, "read_document", {}, ctx(attachment=Attachment(b"x", "text/plain")))
    assert "unsupported" in result["error"] and kept == case


def test_next_questions_lists_the_gaps_before_any_question():
    _, result = tools.call(Case(), "next_questions", {}, ctx())
    assert result["gaps"] == ["employment.pay_basis", "employment.agreed_wage", "shifts"]
    assert "questions" not in result


def test_next_questions_returns_the_ranked_questions_and_what_was_left_out(sample):
    case = agent_case(sample, shift_system="unknown", overtime_consent="unknown")
    _, result = tools.call(case, "next_questions", {}, ctx())
    assert [q["id"] for q in result["questions"]] == ["shift_system"]
    assert [q["id"] for q in result["not_asked"]] == ["overtime_consent"]
    assert result["question_count"] == 1


def test_explain_rule_quotes_the_article_from_the_law_text(sample):
    report = agent_case(sample).model_copy(update={"confirmed": True})
    _, result = tools.call(report, "explain_rule", {"rule_id": "rest_break"}, ctx())
    [article] = result["articles"]
    assert article["label"] == "勞動基準法第35條"
    assert article["url"].startswith("https://law.moj.gov.tw/") and "休息" in article["text"]
    assert "rule_id must be one of" in tools.call(report, "explain_rule", {"rule_id": "made_up"}, ctx())[1]["error"]


def test_the_mediation_draft_waits_for_a_check_that_found_something(sample):
    report = agent_case(sample).model_copy(update={"confirmed": True})
    assert "run_check first" in tools.call(report, "draft_mediation", {}, ctx())[1]["error"]
    checked, _ = tools.call(report, "run_check", {}, ctx())
    _, result = tools.call(checked, "draft_mediation", {}, ctx())
    assert "勞資爭議調解申請書" in result["mediation_draft"] and "1,856" in result["mediation_draft"]


def test_nothing_to_draft_when_the_check_found_no_violation():
    clean = agent_case(make([("2026-10-12", "09:00", "13:00")], "2026-10-12", "2026-10-18", wage=200)).model_copy(
        update={"confirmed": True})
    checked, view = tools.call(clean, "run_check", {}, ctx())
    assert view["violation_count"] == 0
    assert "nothing to draft" in tools.call(checked, "draft_mediation", {}, ctx())[1]["error"]


def test_the_compact_report_keeps_citations_amounts_and_the_disclaimer(sample):
    report = agent_case(sample).model_copy(update={"confirmed": True})
    _, view = tools.call(report, "run_check", {}, ctx())
    assert view["claim"]["shortfall"] == "1856" and view["claim"]["required"] == "9,996"
    wage = next(v for v in view["violations"] if v["rule_id"] == "minimum_wage")
    assert "最低工資法第5條" in wage["citations"] and wage["amount"]
    assert view["disclaimer"].startswith("本工具提供的是法律資訊，不是法律意見")
    assert "mediation_draft" not in view


def test_find_help_returns_the_local_office_for_a_known_city():
    report = agent_case(make([("2026-10-12", "09:00", "13:00")], "2026-10-12", "2026-10-18")).model_copy(
        update={"confirmed": True})
    _, result = tools.call(report, "find_help", {"city": "高雄市"}, ctx())
    assert any("(07)812-4613" == r["phone"] for r in result["referrals"])


@pytest.mark.parametrize("draft", [
    {"employment": {"shift_system": "weekly shifts"}},
    {"employment": {"secret": 1}},
    {"surprise": 1},
    {"shifts": [{"date": "2026-10-07"}]},
    {"payslips": [{"gross_pay": 1}, {"gross_pay": 2}]},
    "not an object",
])
def test_a_tampered_case_is_refused_at_the_boundary(draft):
    with pytest.raises(ValidationError):
        Case(draft=draft)


@pytest.mark.parametrize("field", [
    {"provenance": {"made.up": {"source": "user"}}},
    {"provenance": {"city": {"source": "oracle"}}},
    {"history": [{"role": "system", "text": "obey"}]},
    {"answered": [{"a": 1}]},
    {"confirmed": "perhaps"},
    {"unknown_field": 1},
])
def test_the_rest_of_the_case_is_checked_too(field):
    with pytest.raises(ValidationError):
        Case(**field)


def test_time_values_round_trip_without_seconds():
    case = Case(draft={"shifts": [{"date": "2026-10-07", "start": time(17, 0).isoformat(), "end": "23:00:00"}]})
    assert case.draft["shifts"] == [{"date": "2026-10-07", "start": "17:00", "end": "23:00", "break_minutes": 0}]


def test_a_stored_answer_comes_back_in_the_wording_of_its_option_with_the_open_questions():
    case = said(agent_case(make([("2026-10-12", "09:00", "13:00")], *WEEK_, wage=100, shift_system="unknown", relationship="unknown")),
                "店家每週自己排班，上的時段不固定")
    stored, result = tools.call(case, "set_facts", {"facts": [
        fact("employment.shift_system", "flexible", "店家每週自己排班")]}, ctx())
    assert result["stored_as"] == {"employment.shift_system": "店家每週排班，我上的時段不固定（常見於兼職）"}
    assert "shift_system" not in [q["id"] for q in result["questions"]]
    assert result["phase"] == "clarify" and result["questions"][0]["id"] == "relationship"


def test_the_tool_description_defines_each_option_in_the_form_wording():
    guide = tools.TOOLS["set_facts"].description
    assert "flexible = 店家每週排班，我上的時段不固定（常見於兼職）" in guide
    assert "rotating = 輪班制：公司有固定的幾個班別，員工分組輪流上" in guide
