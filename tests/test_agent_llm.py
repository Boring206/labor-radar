"""The Gemini boundary, with the network replaced: what we send, what we keep from the reply."""

import json

import httpx
import pytest

from labor_radar.agent import llm as llm_mod
from labor_radar.agent.llm import GeminiLLM, LLMError

DECL = [{"name": "next_questions", "description": "d", "parameters": {"type": "OBJECT", "properties": {}}}]
USER = [{"role": "user", "parts": [{"text": "hi"}]}]


def reply(parts, status=200, **extra):
    return httpx.Response(status, json={"candidates": [{"content": {"parts": parts}}], **extra},
                          request=httpx.Request("POST", "http://x"))


def fake_post(monkeypatch, *responses):
    sent = []
    queue = list(responses)

    def post(url, **kwargs):
        sent.append({"url": url, **kwargs})
        return queue.pop(0)

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm_mod.httpx, "post", post)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda seconds: None)
    return sent


def test_the_request_carries_the_system_prompt_tools_and_the_key_in_a_header(monkeypatch):
    sent = fake_post(monkeypatch, reply([{"text": "你好"}]))
    step = GeminiLLM().step("SYSTEM", USER, DECL)
    [request] = sent
    body = request["json"]
    assert body["systemInstruction"] == {"parts": [{"text": "SYSTEM"}]}
    assert body["contents"] == USER and body["generationConfig"]["temperature"] == 0
    assert body["tools"] == [{"functionDeclarations": DECL}]
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}
    assert request["headers"] == {"x-goog-api-key": "test-key"} and "test-key" not in json.dumps(body)
    assert request["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert step.text == "你好" and step.calls == ()


def test_the_model_comes_from_the_environment(monkeypatch):
    sent = fake_post(monkeypatch, reply([{"text": "ok"}]))
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    GeminiLLM().step("s", USER, [])
    assert "gemini-test" in sent[0]["url"] and "tools" not in sent[0]["json"]


def test_calls_are_forbidden_with_mode_none_for_a_rewrite(monkeypatch):
    sent = fake_post(monkeypatch, reply([{"text": "ok"}]))
    GeminiLLM().step("s", USER, DECL, calls="none")
    assert sent[0]["json"]["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}


def test_some_call_can_be_demanded(monkeypatch):
    sent = fake_post(monkeypatch, reply([{"functionCall": {"name": "next_questions"}}]))
    GeminiLLM().step("s", USER, DECL, calls="any")
    assert sent[0]["json"]["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}


def test_one_tool_can_be_forced(monkeypatch):
    sent = fake_post(monkeypatch, reply([{"functionCall": {"name": "next_questions"}}]))
    GeminiLLM().step("s", USER, DECL, calls="next_questions")
    assert sent[0]["json"]["toolConfig"] == {
        "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["next_questions"]}}


def test_a_forced_tool_step_does_not_think(monkeypatch):
    """Gemini 2.5 Flash stalled for minutes on a step that forces one tool while it thinks (2026-11-08);
    a forced step only picks a call, so it runs without a thinking budget. Free-choice steps keep thinking."""
    sent = fake_post(monkeypatch, reply([{"functionCall": {"name": "next_questions"}}]), reply([{"text": "ok"}]))
    GeminiLLM().step("s", USER, DECL, calls="next_questions")
    GeminiLLM().step("s", USER, DECL, calls="auto")
    assert sent[0]["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert "thinkingConfig" not in sent[1]["json"]["generationConfig"]


def test_function_calls_are_read_and_the_content_object_is_kept_whole(monkeypatch):
    part = {"functionCall": {"name": "next_questions", "args": {"x": 1}}, "thoughtSignature": "c2ln"}
    fake_post(monkeypatch, reply([part]))
    step = GeminiLLM().step("s", USER, DECL)
    assert [(c.name, c.args) for c in step.calls] == [("next_questions", {"x": 1})]
    assert step.content == {"role": "model", "parts": [part]}


def test_a_call_without_arguments_gets_an_empty_object(monkeypatch):
    fake_post(monkeypatch, reply([{"functionCall": {"name": "next_questions"}}]))
    assert GeminiLLM().step("s", USER, DECL).calls[0].args == {}


def test_thought_parts_are_not_shown_as_the_answer(monkeypatch):
    fake_post(monkeypatch, reply([{"text": "thinking...", "thought": True}, {"text": "答案"}]))
    assert GeminiLLM().step("s", USER, DECL).text == "答案"


def test_a_missing_key_is_a_503_before_any_request(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError) as error:
        GeminiLLM().step("s", USER, DECL)
    assert error.value.status == 503


def test_a_busy_model_is_retried_and_then_answers(monkeypatch):
    busy = httpx.Response(503, json={}, request=httpx.Request("POST", "http://x"))
    sent = fake_post(monkeypatch, busy, reply([{"text": "ok"}]))
    assert GeminiLLM().step("s", USER, DECL).text == "ok" and len(sent) == 2


def test_errors_never_carry_the_key_or_the_body(monkeypatch):
    denied = httpx.Response(403, json={"error": "key test-key is bad"}, request=httpx.Request("POST", "http://x"))
    fake_post(monkeypatch, denied)
    with pytest.raises(LLMError) as error:
        GeminiLLM().step("s", USER, DECL)
    assert "test-key" not in str(error.value) and "403" in str(error.value)


def test_a_blocked_prompt_is_an_error_not_an_empty_answer(monkeypatch):
    blocked = httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}},
                             request=httpx.Request("POST", "http://x"))
    fake_post(monkeypatch, blocked)
    with pytest.raises(LLMError, match="SAFETY"):
        GeminiLLM().step("s", USER, DECL)
