"""A whole conversation against a scripted model: what the loop lets through, what it refuses, how it recovers."""

import json
import re

import pytest

from labor_radar.agent import questions
from labor_radar.agent.case import Case, Phase
from labor_radar.agent.llm import LLMError, ScriptedLLM, Step, ToolCall
from labor_radar.agent.loop import MAX_STEPS, run_turn
from labor_radar.agent.questions import Tap
from labor_radar.agent.tools import Attachment

OPENING = ("我在手搖飲打工，時薪185，10/7 17:00到23:00，10/8 08:00到13:00。老闆每週排班，今年是2026年。")
SHIFTS = [{"date": "2026-10-07", "start": "17:00", "end": "23:00"},
          {"date": "2026-10-08", "start": "08:00", "end": "13:00"}]
ANSWERS = "我是受僱員工，沒有責任制，也沒有變形工時。"


def set_facts(*facts):
    return Step.calling(("set_facts", {"facts": list(facts)}))


def fact(path, value, quote):
    return {"path": path, "value": value, "quote": quote}


def opening_facts():
    return set_facts(
        fact("employment.pay_basis", "hourly", "時薪185"),
        fact("employment.agreed_wage", 185, "時薪185"),
        fact("shifts", SHIFTS, "10/7 17:00到23:00，10/8 08:00到13:00"),
        fact("employment.shift_system", "weekly shifts", "老闆每週排班"),
    )


def confirmed_case(llm_steps=None):
    """The state after the worker has told everything and pressed 確認資料."""
    llm = ScriptedLLM([
        opening_facts(), set_facts(fact("employment.shift_system", "flexible", "老闆每週排班")),
        Step.saying("收到。"),
        set_facts(fact("employment.relationship", "employee", "我是受僱員工"),
                  fact("employment.responsibility_system", "no", "沒有責任制"),
                  fact("employment.special_hours_arrangement", "no", "沒有變形工時")),
        Step.saying("資料齊了，請檢查資料表再按確認資料。"),
    ])
    first = run_turn(Case(), OPENING, llm, "zh-TW")
    second = run_turn(first.case, ANSWERS, llm, "zh-TW")
    assert second.phase is Phase.CONFIRM
    return second.case.model_copy(update={"confirmed": True})


def test_the_worker_talks_and_the_agent_asks_only_what_matters():
    llm = ScriptedLLM([
        opening_facts(),
        set_facts(fact("employment.shift_system", "flexible", "老闆每週排班")),
        Step.calling(("next_questions", {})),
        Step.saying("你和老闆是受僱關係嗎？選項：受僱員工，或對方說你是承攬。"),
    ])
    result = run_turn(Case(), OPENING, llm, "zh-TW")

    [first, second, third] = result.trace[:3]
    assert first["tool"] == "set_facts" and first["ok"]
    assert [r["path"] for r in first["rejected"]] == ["employment.shift_system"]
    assert "weekly shifts" in first["rejected"][0]["error"] and "flexible" in first["rejected"][0]["error"]
    assert second["ok"] and third["tool"] == "next_questions"

    assert result.case.draft["employment"]["shift_system"] == "flexible"
    assert len(result.case.draft["shifts"]) == 2
    assert result.phase is Phase.CLARIFY
    assert result.questions["questions"][0]["id"] == "relationship"
    assert result.reply.startswith("你和老闆是受僱關係嗎")
    assert [row["source"] for row in result.facts if row["path"] == "employment.agreed_wage"] == ["user"]
    assert result.report_view is None


def test_the_amount_a_question_could_change_is_for_the_chips_not_for_the_reply():
    def quote_the_spread(request):
        [amount] = re.findall(r"最多差 ([\d,]+) 元", json.dumps(request.contents[-1], ensure_ascii=False))
        return Step.saying(f"答案不同，金額最多差 {amount} 元。")

    llm = ScriptedLLM([opening_facts(), set_facts(fact("employment.shift_system", "flexible", "老闆每週排班")),
                       Step.calling(("next_questions", {})), quote_the_spread,
                       Step.saying("你和老闆是受僱關係嗎？")])
    result = run_turn(Case(), OPENING, llm, "zh-TW")
    assert result.phase is Phase.CLARIFY
    assert result.reply == "你和老闆是受僱關係嗎？"


def test_the_model_sees_only_the_tools_its_phase_allows_and_is_told_the_state():
    llm = ScriptedLLM([opening_facts(), Step.saying("好")])
    run_turn(Case(), OPENING, llm, "zh-TW")
    first, second = llm.requests
    assert first.tools == ["set_facts", "next_questions"]
    assert "run_check" not in second.tools
    assert "目前階段：收集資料" in first.system and "目前階段：釐清細節" in second.system


def test_the_confirmed_conversation_ends_in_a_grounded_report_and_a_mediation_draft():
    confirmed = confirmed_case()
    llm = ScriptedLLM([
        Step.calling(("run_check", {})),
        lambda request: Step.saying(
            "算好了。你的時薪 185 元，低於當時的最低工資。報告裡有 2 項要處理的地方。這是法律資訊，不是法律意見。"),
    ])
    result = run_turn(confirmed, "我確認了，請告訴我結果", llm, "zh-TW")
    assert result.phase is Phase.REPORT
    assert "run_check" in llm.requests[0].tools
    assert result.trace[0]["tool"] == "run_check" and result.trace[0]["ok"]
    assert result.report_view["disclaimer"].startswith("本工具提供的是法律資訊")
    assert [v["rule_id"] for v in result.report_view["violations"]] == ["minimum_wage", "rest_break"]
    assert result.case.reported
    assert not [e for e in result.trace if e["kind"] == "guard"]

    follow = ScriptedLLM([Step.calling(("draft_mediation", {})), Step.saying("草稿已經放在報告下方，簽名前請再檢查。")])
    drafted = run_turn(result.case, "幫我寫調解申請書", follow, "zh-TW")
    assert drafted.trace[0]["tool"] == "draft_mediation" and drafted.trace[0]["ok"]
    assert "勞資爭議調解申請書" in drafted.report_view["mediation_draft"]


def test_the_mediation_draft_cannot_be_requested_before_a_check():
    llm = ScriptedLLM([Step.calling(("draft_mediation", {})), Step.saying("先讓我跑檢查。")])
    result = run_turn(confirmed_case(), "寫申請書", llm, "zh-TW")
    assert result.trace[0]["ok"] is False and "run_check first" in result.trace[0]["summary"]


def test_a_fact_changed_in_the_report_phase_sends_the_worker_back_to_confirm():
    reported = run_turn(confirmed_case(), "結果?", ScriptedLLM([Step.calling(("run_check", {})), Step.saying("好")]),
                        "zh-TW")
    old_shortfall = reported.report_view["claim"]["shortfall"]
    llm = ScriptedLLM([set_facts(fact("employment.agreed_wage", 200, "時薪改成200")),
                       Step.saying(f"照上次的報告，短少 {old_shortfall} 元。"), Step.saying("已經改了，請再確認一次資料表。")])
    changed = run_turn(reported.case, "時薪改成200", llm, "zh-TW")
    assert changed.phase is Phase.CONFIRM and changed.case.confirmed is False
    assert changed.report_view is None
    assert changed.reply == "已經改了，請再確認一次資料表。"


def _response(request, tool):
    return next(part["functionResponse"]["response"] for content in request.contents for part in content["parts"]
                if part.get("functionResponse", {}).get("name") == tool)


def test_a_check_run_earlier_in_the_same_turn_stops_counting_once_a_fact_changes():
    def quote_the_check(request):
        return Step.saying(f"照剛才的結果，短少 {_response(request, 'run_check')['claim']['shortfall']} 元。")

    llm = ScriptedLLM([Step.calling(("run_check", {})), set_facts(fact("employment.agreed_wage", 190, "時薪改成190")),
                       quote_the_check, Step.saying("時薪改了，請再檢查資料表，沒問題就按確認資料。")])
    result = run_turn(confirmed_case(), "時薪改成190", llm, "zh-TW")
    assert result.phase is Phase.CONFIRM
    assert result.reply == "時薪改了，請再檢查資料表，沒問題就按確認資料。"
    assert [e["action"] for e in result.trace if e["kind"] == "guard"] == ["rewritten"]


def test_a_mediation_draft_made_before_a_fact_changed_no_longer_grounds_its_amount():
    def quote_the_draft(request):
        [amount] = re.findall(r"請求金額：([\d,]+) 元", _response(request, "draft_mediation")["mediation_draft"])
        return Step.saying(f"申請書上的請求金額是 {amount} 元。")

    llm = ScriptedLLM([Step.calling(("run_check", {})), Step.calling(("draft_mediation", {})),
                       set_facts(fact("employment.agreed_wage", 190, "時薪改成190")),
                       quote_the_draft, Step.saying("時薪改了，請再檢查資料表。")])
    result = run_turn(confirmed_case(), "時薪改成190", llm, "zh-TW")
    assert result.reply == "時薪改了，請再檢查資料表。"


def test_a_made_up_number_is_sent_back_once_and_the_rewrite_is_used():
    llm = ScriptedLLM([
        set_facts(fact("employment.pay_basis", "hourly", "時薪185"), fact("employment.agreed_wage", 185, "時薪185")),
        Step.saying("你的時薪 190 元，對嗎？"),
        Step.saying("你的時薪是 185 元，對嗎？"),
    ])
    result = run_turn(Case(), "時薪185", llm, "zh-TW", debug=True)
    assert result.reply == "你的時薪是 185 元，對嗎？"
    [event] = [e for e in result.trace if e["kind"] == "guard"]
    assert event["action"] == "rewritten" and event["detail"]["problems"][0]["token"] == "190"
    retry = llm.requests[-1]
    assert retry.calls == "none"
    assert "190" in retry.contents[-1]["parts"][0]["text"] and retry.contents[-2]["parts"][0]["text"] == "你的時薪 190 元，對嗎？"


def test_two_bad_replies_in_a_row_fall_back_to_the_fixed_question():
    llm = ScriptedLLM([
        set_facts(fact("employment.pay_basis", "hourly", "時薪185"), fact("employment.agreed_wage", 185, "時薪185"),
                  fact("shifts", SHIFTS, "10/7 17:00到23:00，10/8 08:00到13:00")),
        Step.saying("你的時薪 190 元。"),
        Step.saying("你的時薪是 195 元。"),
    ])
    result = run_turn(Case(), OPENING, llm, "zh-TW", debug=True)
    assert result.phase is Phase.CLARIFY
    top = result.questions["questions"][0]
    assert result.reply.startswith(top["question"])
    assert all(option["label"] in result.reply for option in top["options"])
    assert "190" not in result.reply and "195" not in result.reply
    [event] = [e for e in result.trace if e["kind"] == "guard"]
    assert event["action"] == "fallback" and event["detail"]["after_rewrite"][0]["token"] == "195"


def test_judging_words_are_refused_before_the_report():
    llm = ScriptedLLM([
        set_facts(fact("employment.pay_basis", "hourly", "時薪185")),
        Step.saying("老闆這樣違法。"),
        Step.saying("我還需要知道你上班的日期和時段。"),
    ])
    result = run_turn(Case(), "時薪185", llm, "zh-TW", debug=True)
    assert result.reply == "我還需要知道你上班的日期和時段。"
    assert [(p["kind"], p["token"]) for p in result.trace[-1]["detail"]["problems"]] == [("term", "違法")]


def test_the_same_words_are_fine_once_the_report_is_out():
    llm = ScriptedLLM([Step.calling(("run_check", {})), Step.saying("最低工資這一項，報告認為可能違法。")])
    result = run_turn(confirmed_case(), "結果", llm, "zh-TW")
    assert result.reply == "最低工資這一項，報告認為可能違法。"
    assert not [e for e in result.trace if e["kind"] == "guard"]


def test_a_report_from_an_earlier_turn_still_counts_while_the_facts_stay_the_same():
    reported = run_turn(confirmed_case(), "結果", ScriptedLLM([Step.calling(("run_check", {})), Step.saying("好了。")]), "zh-TW")
    llm = ScriptedLLM([Step.calling(("find_help", {"city": "高雄市"})), Step.saying("最低工資這一項，報告認為可能違法。")])
    result = run_turn(reported.case, "最低工資那項是什麼意思？", llm, "zh-TW")
    assert result.reply == "最低工資這一項，報告認為可能違法。"
    assert result.report_view is not None


def test_confirmed_facts_without_a_check_are_not_a_report():
    llm = ScriptedLLM([Step.calling(("find_help", {"city": "高雄市"})),
                       Step.saying("老闆這樣違法，你可以請求加班費，也少給你最低工資。"),
                       Step.saying("我先幫你跑一次檢查。")])
    result = run_turn(confirmed_case(), "我確認了", llm, "zh-TW")
    assert result.reply == "我先幫你跑一次檢查。"
    [event] = [e for e in result.trace if e["kind"] == "guard"]
    assert event["action"] == "rewritten"
    assert result.report_view is None


def test_in_the_report_phase_a_wrong_amount_is_still_caught():
    llm = ScriptedLLM([Step.calling(("run_check", {})), Step.saying("你被少給了 5,000 元。"), Step.saying("報告有 1 項。")])
    result = run_turn(confirmed_case(), "結果", llm, "zh-TW", debug=True)
    assert result.reply == "報告有 1 項。"
    assert result.trace[-1]["detail"]["problems"][0]["token"] == "5,000"


def test_a_reply_in_the_report_phase_that_never_recovers_shows_the_engine_summary():
    llm = ScriptedLLM([Step.calling(("run_check", {})), Step.saying("總共少 9,999 元"), Step.saying("還是 9,999 元")])
    result = run_turn(confirmed_case(), "結果", llm, "zh-TW")
    assert "9,999" not in result.reply
    assert result.reply == "\n".join(["以下是系統算出的結果摘要。", *result.report_view["summary"],
                                      result.report_view["disclaimer"]])


def test_a_model_that_fails_leaves_a_usable_conversation():
    class Down:
        def step(self, *args, **kwargs):
            raise LLMError("Gemini returned HTTP 500")

    result = run_turn(confirmed_case(), "結果", Down(), "zh-TW")
    assert result.trace[0] == {"kind": "llm_error", "message": "Gemini returned HTTP 500"}
    assert result.phase is Phase.REPORT and result.case.reported
    assert result.report_view["violations"]


def test_a_model_that_never_stops_calling_tools_is_cut_off():
    llm = ScriptedLLM([Step.calling(("next_questions", {}))] * (MAX_STEPS + 2))
    result = run_turn(Case(), "hi", llm, "zh-TW")
    assert len(llm.requests) == MAX_STEPS
    assert result.trace[-1] == {"kind": "limit"}
    assert result.reply == "請告訴我時薪或月薪是多少，並說明上班的日期和時段，或附上班表照片。"


def test_an_empty_reply_gets_the_fixed_text():
    result = run_turn(Case(), "hi", ScriptedLLM([Step.saying("")]), "zh-TW")
    assert result.trace[-1]["problems"] == ["empty"]
    assert result.reply == "請告訴我時薪或月薪是多少，並說明上班的日期和時段，或附上班表照片。"


def test_the_fixed_text_follows_the_worker_language():
    result = run_turn(Case(), "hi", ScriptedLLM([Step.saying("")]), "vi")
    assert result.reply.startswith("Hãy cho tôi biết lương")


def test_the_model_turn_goes_back_verbatim_with_its_thought_signature():
    model_turn = {"role": "model", "parts": [{"functionCall": {"name": "next_questions", "args": {}},
                                              "thoughtSignature": "c2lnbmF0dXJl"}]}
    llm = ScriptedLLM([Step((ToolCall("next_questions", {}),), "", model_turn), Step.saying("好的，先告訴我時薪。")])
    run_turn(Case(), "hi", llm, "zh-TW")
    contents = llm.requests[1].contents
    assert contents[-2] == model_turn
    [response] = contents[-1]["parts"]
    assert contents[-1]["role"] == "user" and response["functionResponse"]["name"] == "next_questions"
    assert response["functionResponse"]["response"]["phase"] == "gather"


def test_several_calls_in_one_step_get_one_answer_each_in_order():
    llm = ScriptedLLM([Step.calling(("next_questions", {}), ("run_check", {})), Step.saying("先告訴我時薪。")])
    result = run_turn(Case(), "hi", llm, "zh-TW")
    parts = llm.requests[1].contents[-1]["parts"]
    assert [p["functionResponse"]["name"] for p in parts] == ["next_questions", "run_check"]
    assert "not available" in parts[1]["functionResponse"]["response"]["error"]
    assert [e["ok"] for e in result.trace] == [True, False]


def test_history_keeps_the_last_twenty_turns_and_the_new_reply():
    case = Case()
    for index in range(15):
        case = run_turn(case, f"第{index}句", ScriptedLLM([Step.saying("我還需要知道你上班的日期和時段。")]), "zh-TW").case
    assert len(case.history) == 20
    assert case.history[-1].role == "assistant" and case.history[-2].text == "第14句"


def test_neither_the_photo_nor_the_key_reaches_the_trace_or_the_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "key-should-never-leak")
    from labor_radar import extraction

    monkeypatch.setattr(extraction, "extract", lambda data, mime, year: extraction.Draft.model_validate(
        {"document_type": "schedule", "unreadable": [], "shifts": [{"date": "2026-10-07", "start": "17:00", "end": "23:00"}]}))
    llm = ScriptedLLM([Step.calling(("read_document", {})), Step.saying("我讀到 1 筆班次，請問時薪是多少？")])
    result = run_turn(Case(), "這是班表", llm, "zh-TW", Attachment(b"PHOTO-BYTES-123", "image/png", "班表.png"))
    dumped = result.model_dump_json() + json.dumps([r.contents for r in llm.requests], ensure_ascii=False)
    assert "PHOTO-BYTES-123" not in dumped and "key-should-never-leak" not in dumped
    assert result.trace[0]["tool"] == "read_document" and result.trace[0]["ok"]
    assert "班表.png" in llm.requests[0].contents[-1]["parts"][-1]["text"]


def test_a_tool_the_phase_forbids_leaves_the_case_alone_and_tells_the_model():
    llm = ScriptedLLM([Step.calling(("run_check", {})), Step.saying("我還需要知道你上班的日期和時段。")])
    result = run_turn(Case(), "hi", llm, "zh-TW")
    assert result.trace[0]["ok"] is False and "not available" in result.trace[0]["summary"]
    assert result.case.reported is False


def test_the_questions_the_ui_shows_match_the_ones_the_engine_ranks():
    confirmed = confirmed_case()
    shown = run_turn(confirmed.model_copy(update={"confirmed": False}), "hi", ScriptedLLM([Step.saying("請檢查資料表。")]), "zh-TW")
    assert shown.phase is Phase.CONFIRM
    assert shown.questions["not_asked_reason"] == "不影響結果、所以沒問的題目"
    assert {q["id"] for q in shown.questions["not_asked"]} == {q.id for q in questions.analyze(shown.case).not_asked}


def test_an_attached_file_is_read_before_the_model_gets_to_choose(monkeypatch):
    from labor_radar import extraction

    monkeypatch.setattr(extraction, "extract", lambda data, mime, year: extraction.Draft.model_validate(
        {"document_type": "schedule", "unreadable": [], "shifts": []}))
    attached = ScriptedLLM([Step.calling(("read_document", {})), Step.calling(("next_questions", {})), Step.saying("請告訴我時薪。")])
    run_turn(Case(), "這是班表", attached, "zh-TW", Attachment(b"x", "image/png"))
    assert [r.calls for r in attached.requests] == ["read_document", "any", "auto"]


def test_after_reading_the_file_the_model_must_write_or_ask_before_it_speaks(monkeypatch):
    """Recorded 2026-11-08: after the forced read, a free model said it had noted the worker's words and never called set_facts."""
    from labor_radar import extraction

    monkeypatch.setattr(extraction, "extract", lambda data, mime, year: extraction.Draft.model_validate(
        {"document_type": "schedule", "unreadable": [], "shifts": []}))
    llm = ScriptedLLM([Step.calling(("read_document", {})),
                       set_facts(fact("employment.shift_system", "flexible", "老闆每週自己排班")),
                       Step.saying("記下了。")])
    result = run_turn(Case(), "老闆每週自己排班，這是班表", llm, "zh-TW", Attachment(b"x", "image/png"))
    assert result.case.draft["employment"]["shift_system"] == "flexible"
    assert [r.calls for r in llm.requests] == ["read_document", "any", "auto"]


def test_a_call_repeated_within_one_step_runs_once(monkeypatch):
    from labor_radar import extraction

    reads = []
    monkeypatch.setattr(extraction, "extract", lambda data, mime, year: reads.append(1) or extraction.Draft.model_validate(
        {"document_type": "schedule", "unreadable": [], "shifts": []}))
    llm = ScriptedLLM([Step.calling(("read_document", {}), ("read_document", {})), Step.calling(("next_questions", {})),
                       Step.saying("請告訴我時薪。")])
    result = run_turn(Case(), "這是班表", llm, "zh-TW", Attachment(b"x", "image/png"))
    parts = llm.requests[1].contents[-1]["parts"]
    assert len(reads) == 1 and [p["functionResponse"]["name"] for p in parts] == ["read_document", "read_document"]
    assert parts[0] == parts[1] and [e["tool"] for e in result.trace] == ["read_document", "next_questions"]


def test_the_model_is_told_a_tapped_answer_is_already_stored():
    llm = ScriptedLLM([Step.calling(("next_questions", {})), Step.saying("好，請檢查資料表。")])
    result = run_turn(confirmed_case().model_copy(update={"confirmed": False}), Tap("week_starts_on", "monday"), llm, "zh-TW")
    assert result.case.draft["employment"]["week_starts_on"] == "monday"
    [said, note] = llm.requests[0].contents[-1]["parts"]
    assert said["text"].endswith("→ 星期一") and "系統已經存好" in note["text"]


def test_a_turn_without_a_file_still_opens_with_a_tool_call():
    plain = ScriptedLLM([Step.calling(("next_questions", {})), Step.saying("請告訴我時薪。")])
    run_turn(Case(), "你好", plain, "zh-TW")
    assert [r.calls for r in plain.requests] == ["any", "auto"]
