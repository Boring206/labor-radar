"""The guard reads the model's words, not its internals: a number it cannot trace, or a verdict it may not give."""

import json

import pytest
from conftest import agent_case

from labor_radar.agent import guard
from labor_radar.agent.case import Case, Phase, with_turn
from labor_radar.agent.tools import Ctx, call


@pytest.fixture
def case(sample):
    return with_turn(agent_case(sample), "user", "我時薪185，每週排班，上週付我 8140")


@pytest.fixture
def reported(sample):
    confirmed = agent_case(sample).model_copy(update={"confirmed": True})
    checked, _ = call(confirmed, "run_check", {}, Ctx(lang="zh-TW"))
    return checked


def bad(text, case, phase=Phase.CONFIRM, extra=()):
    return [p.token for p in guard.check(text, case, phase, extra)]


@pytest.mark.parametrize("text", [
    "你的時薪是 185 元。",
    "時薪１８５元",
    "NT$185",
    "你說領了 8,140 元。",
    "10/07 17:00 到 23:00 上班",
    "2026-10-07 那天 17:00 上班",
    "10月7日晚上上班",
    "10/8 早上 08:00 又上班",
    "目前有 8 筆班次",
    "你提供的期間是 2026-10-05 到 2026-10-18",
])
def test_numbers_that_are_in_the_facts_pass(text, case):
    assert bad(text, case) == []


@pytest.mark.parametrize("text, token", [
    ("你的時薪是 190 元。", "190"),
    ("8,141 元", "8,141"),
    ("10/09 你有上班", "10/9"),
    ("你 23:30 下班", "23:30"),
    ("一共 12 筆班次", "12"),
    ("依第 99 條規定", "第 99 條"),
    ("月薪 4.5 萬", "4.5"),
])
def test_numbers_that_appear_from_nowhere_are_caught(text, token, case):
    assert bad(text, case) == [token]


def test_the_same_number_from_the_assistants_own_earlier_reply_does_not_count(case):
    echoed = with_turn(case, "assistant", "你一週工作 61 小時")
    assert bad("你一週工作 61 小時", echoed) == ["61"]


def test_a_number_the_worker_typed_counts_wherever_it_sits(case):
    assert bad("你說上週付你 8140", case) == []


@pytest.mark.parametrize("text", ["1. 你和老闆是受僱關係嗎？\n2. 老闆有說過責任制嗎？", "（1）先確認時薪\n（2）再確認班次", "1、你好\n"])
def test_list_numbering_up_to_five_at_a_line_start_is_free(text, case):
    assert bad(text, case) == []


@pytest.mark.parametrize("text, token", [("6. 第六題", "6"), ("你有 2 個問題", "2")])
def test_only_list_markers_are_free(text, token, case):
    assert bad(text, case) == [token]


def test_numbers_from_the_report_are_grounded_and_others_are_not(reported):
    paragraph = f"估計短少 {reported.last_report['claim']['shortfall']} 元，依勞動基準法第 35 條。"
    assert bad(paragraph, reported, Phase.REPORT) == []
    assert bad("估計短少 1,857 元", reported, Phase.REPORT) == ["1,857"]


def test_article_numbers_need_the_report_to_cite_them(reported):
    assert bad("勞動基準法第 35 條", reported, Phase.REPORT) == []
    assert bad("勞動基準法第 84-1 條", reported, Phase.REPORT) == ["第 84-1 條"]


def test_text_a_tool_produced_counts_when_the_loop_passes_it_on(case):
    assert bad("第 34 條第 2 項", case) != []
    law = json.dumps({"articles": [{"label": "勞動基準法第34條第2項"}]}, ensure_ascii=False)
    assert bad("第 34 條第 2 項", case, extra=[law]) == []


@pytest.mark.parametrize("term", guard.LEGALITY_TERMS)
def test_every_judging_word_is_blocked_before_the_report_and_allowed_in_it(term, reported):
    sentence = f"老闆這樣做{term}。"
    assert bad(sentence, reported, Phase.CONFIRM) == [term]
    assert bad(sentence, reported, Phase.REPORT) == []


@pytest.mark.parametrize("phase", [Phase.GATHER, Phase.CLARIFY, Phase.CONFIRM])
def test_no_judgment_in_any_phase_before_the_report(phase, case):
    assert bad("這看起來違法。", case, phase) == ["違法"]


def test_plain_questions_pass(case):
    assert bad("你的班別是固定的，還是每週店家排給你？", case) == []


def test_another_language_gets_the_number_check_only(case):
    assert bad("Lương theo giờ của bạn là 185 Đài tệ.", case) == []
    assert bad("Lương theo giờ của bạn là 190 Đài tệ.", case) == ["190"]


def test_a_case_with_nothing_in_it_grounds_nothing():
    assert bad("時薪 185 元", Case()) == ["185"]
