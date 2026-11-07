"""The guard reads the model's words, not its internals: a number it cannot trace, or a verdict it may not give."""

import json

import pytest
from conftest import agent_case

from labor_radar.agent import guard
from labor_radar.agent.case import Case, with_turn
from labor_radar.agent.tools import Ctx, call, report_for


@pytest.fixture
def case(sample):
    return with_turn(agent_case(sample), "user", "我時薪185，每週排班，上週付我 8140")


@pytest.fixture
def reported(sample):
    confirmed = agent_case(sample).model_copy(update={"confirmed": True})
    checked, _ = call(confirmed, "run_check", {}, Ctx(lang="zh-TW"))
    return checked


def bad(text, case, extra=()):
    return [p.token for p in guard.check(text, case, report_for(case, "zh-TW"), extra)]


def kinds(text, case, extra=()):
    return sorted({p.kind for p in guard.check(text, case, report_for(case, "zh-TW"), extra)})


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
    ("依勞基法 99 條", "第 99 條"),
    ("Theo Điều 99 luật lao động", "第 99 條"),
    ("Menurut Pasal 99", "第 99 條"),
    ("ตามมาตรา 99", "第 99 條"),
    ("月薪 4.5 萬", "45,000"),
    ("你大概少拿了2萬5千元", "25,000"),
    ("月薪 4萬5千", "45,000"),
    ("差不多 1萬 元", "10,000"),
    ("lương 190 nghìn", "190,000"),
    ("gaji 25 ribu", "25,000"),
    ("大約 25k", "25,000"),
    ("\n4.5 小時", "4.5"),
    ("\n1.9萬", "19,000"),
    ("\n2.5 倍", "2.5"),
])
def test_numbers_that_appear_from_nowhere_are_caught(text, token, case):
    assert bad(text, case) == [token]


def test_a_number_the_worker_said_with_a_unit_counts_as_its_value():
    worker = with_turn(Case(), "user", "我月薪2萬8")
    assert bad("你的月薪是 28,000 元", worker) == []
    assert bad("你的月薪是 2 萬 8 千元", worker) == []


def test_an_article_number_is_not_a_plain_number(reported):
    assert bad("勞動基準法第 39 條", reported) == []
    assert bad("國定假日有 39 天", reported) == ["39"]


def test_an_article_cited_without_its_ordinal_or_in_another_language_matches(reported):
    assert bad("勞基法 35 條規定要休息", reported) == []
    assert bad("Theo Điều 35, bạn được nghỉ giải lao", reported) == []


def test_a_name_read_from_a_photo_vouches_for_no_number():
    case = Case(draft={"employer": {"name": "店名 加班費3000"}, "worker": {"name": "7-11 店員"}})
    assert bad("加班費 3000 元", case) == ["3,000"]
    assert bad("你在 7-11 上班", case) == ["11", "7"]


def test_a_huge_number_is_caught_instead_of_crashing(case):
    huge = "9" * 5000
    assert guard.check(f"你應該拿到 {huge} 元", case, None)
    long_talk = with_turn(Case(), "user", huge)
    assert bad("好的", long_talk) == []


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
    paragraph = f"估計短少 {report_for(reported, 'zh-TW')['claim']['shortfall']} 元，依勞動基準法第 35 條。"
    assert bad(paragraph, reported) == []
    assert bad("估計短少 1,857 元", reported) == ["1,857"]


def test_article_numbers_need_the_report_to_cite_them(reported):
    assert bad("勞動基準法第 35 條", reported) == []
    assert bad("勞動基準法第 84-1 條", reported) == ["第 84-1 條"]


def test_text_a_tool_produced_counts_when_the_loop_passes_it_on(case):
    assert bad("第 34 條第 2 項", case) != []
    law = json.dumps({"articles": [{"label": "勞動基準法第34條第2項"}]}, ensure_ascii=False)
    assert bad("第 34 條第 2 項", case, extra=[law]) == []


JUDGING = [
    "老闆這樣違法。", "這違反勞基法。", "這樣不合法。", "這算違規。", "老闆犯法了。", "老闆少給你錢。", "這是積欠工資。",
    "老闆欠你錢。", "這是欠薪。", "老闆應補給你。", "你可以請求加班費。", "這筆錢可請求。",
    "這樣是非法的。", "這不符合規定。", "這不合規。", "這違背勞基法。", "老闆觸法了。", "老闆欠了你錢。",
    "你被欠了工資。", "你少拿了加班費。", "你可以討回來。", "你可以要回錢。", "你有權請求加班費。",
    "雇主依法應付加班費。", "雇主沒有依法給加班費。",
    "老闆违法了。", "老闆這樣違　法。", "老闆這樣違\u200b法。", "老闆這樣違 法。",
]


@pytest.mark.parametrize("sentence", JUDGING)
def test_judging_words_are_blocked_before_the_report_and_allowed_in_it(sentence, reported):
    assert kinds(sentence, reported.model_copy(update={"reported": False})) == ["term"]
    assert bad(sentence, reported) == []


@pytest.mark.parametrize("sentence", ["你要回答這兩題就好。", "請你回覆我上班的時段。", "我還欠缺你的班表。"])
def test_ordinary_words_that_share_characters_with_a_judging_word_pass(sentence, case):
    assert bad(sentence, case) == []


def test_before_the_report_an_amount_must_come_from_the_facts_or_the_worker(case):
    question = "公司有沒有經工會同意，讓每月加班超過 46 小時？"
    assert bad("每月加班超過 46 小時", case, extra=[question]) == []
    assert kinds("你可以拿回 46 元", case, extra=[question]) == ["amount"]
    assert bad("時薪 185 元，上週付你 8,140 元", case) == []


def test_confirmed_facts_are_not_a_report_until_the_check_runs(sample):
    confirmed = agent_case(sample).model_copy(update={"confirmed": True})
    assert bad("這看起來違法。", confirmed) == ["違法"]


def test_plain_questions_pass(case):
    assert bad("你的班別是固定的，還是每週店家排給你？", case) == []


def test_another_language_gets_the_number_check_only(case):
    assert bad("Lương theo giờ của bạn là 185 Đài tệ.", case) == []
    assert bad("Lương theo giờ của bạn là 190 Đài tệ.", case) == ["190"]


def test_a_case_with_nothing_in_it_grounds_nothing():
    assert bad("時薪 185 元", Case()) == ["185"]
