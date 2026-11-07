"""The HTTP surface, and the vision boundary with Gemini replaced by a canned reply."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from labor_radar import extraction
from labor_radar.web import app

client = TestClient(app)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def test_check_returns_the_localized_report(sample):
    response = client.post("/api/check?lang=zh-TW", json=sample)
    assert response.status_code == 200
    body = response.json()
    assert [v["rule_id"] for v in body["violations"]] == ["minimum_wage", "rest_break", "holiday_pay"]
    assert body["disclaimer"].startswith("本工具提供的是法律資訊，不是法律意見")


def test_check_rejects_bad_input_with_the_reasons(sample):
    response = client.post("/api/check", json=sample | {"period": {"start": "2026-10-18", "end": "2026-10-05"}})
    assert response.status_code == 422
    assert any("period" in problem for problem in response.json()["detail"])


def test_draft_languages_are_flagged(sample):
    body = client.post("/api/check?lang=vi", json=sample).json()
    assert body["draft_translation"] is True and body["draft_banner"]


def test_extract_without_a_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.post("/api/extract", files={"file": ("a.png", PNG, "image/png")})
    assert response.status_code == 503


def _fake_gemini(monkeypatch, reply: dict):
    sent = {}

    def post(url, **kwargs):
        sent.update(url=url, body=kwargs["json"])
        payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]}
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(extraction.httpx, "post", post)
    return sent


def test_extract_sends_the_schema_and_returns_typed_shifts(monkeypatch):
    sent = _fake_gemini(monkeypatch, {
        "document_type": "schedule", "shifts": [{"date": "2026-10-07", "start": "17:00", "end": "23:00"}],
        "hourly_wage": 185, "unreadable": ["10/9 那格被手指擋住"],
    })
    response = client.post("/api/extract", files={"file": ("a.png", PNG, "image/png")})
    assert response.status_code == 200
    assert response.json()["shifts"] == [{"date": "2026-10-07", "start": "17:00:00", "end": "23:00:00", "break_minutes": None}]
    config = sent["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["responseSchema"] == extraction.RESPONSE_SCHEMA


@pytest.mark.parametrize("reply", [
    {"document_type": "schedule", "shifts": [{"date": "十月七日", "start": "17:00", "end": "23:00"}], "unreadable": []},
    {"document_type": "schedule", "shifts": [{"date": "2026-10-07", "start": "25:00", "end": "23:00"}], "unreadable": []},
])
def test_extract_rejects_model_output_that_breaks_the_schema(monkeypatch, reply):
    _fake_gemini(monkeypatch, reply)
    response = client.post("/api/extract", files={"file": ("a.png", PNG, "image/png")})
    assert response.status_code == 502


def test_extract_refuses_files_it_should_not_send(monkeypatch):
    _fake_gemini(monkeypatch, {"document_type": "other", "shifts": [], "unreadable": []})
    response = client.post("/api/extract", files={"file": ("a.txt", b"hello", "text/plain")})
    assert response.status_code == 415


def test_sample_record_is_served_for_the_demo():
    meta = client.get("/api/meta").json()
    assert meta["sample"]["worker"]["name"] == "林小美"
    assert {l["code"] for l in meta["languages"]} >= {"zh-TW", "vi", "id", "th"}
    json.dumps(meta)
