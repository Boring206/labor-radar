"""The model behind the agent: one call that returns tool calls or final text.

`contents` is Gemini's own format. The model's turn comes back as `Step.content` and goes into the
next request unchanged, because thought signatures must round-trip.
"""

import os
import time
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from labor_radar import extraction

TIMEOUT = 90
RETRY_STATUS = {429, 500, 502, 503}
RETRIES = 2


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict


@dataclass(frozen=True)
class Step:
    calls: tuple[ToolCall, ...] = ()
    text: str = ""
    content: dict = field(default_factory=dict)

    @classmethod
    def calling(cls, *calls: tuple[str, dict]) -> "Step":
        parts = [{"functionCall": {"name": name, "args": args}} for name, args in calls]
        return cls(tuple(ToolCall(name, args) for name, args in calls), "", {"role": "model", "parts": parts})

    @classmethod
    def saying(cls, text: str) -> "Step":
        return cls((), text, {"role": "model", "parts": [{"text": text}]})


class LLM(Protocol):
    def step(self, system: str, contents: list[dict], tools: list[dict], *, allow_calls: bool = True) -> Step: ...


class GeminiLLM:
    """REST generateContent with function calling. The key comes only from GEMINI_API_KEY."""

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("GEMINI_MODEL", extraction.DEFAULT_MODEL)

    def step(self, system: str, contents: list[dict], tools: list[dict], *, allow_calls: bool = True) -> Step:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set", 503)
        body: dict = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0},
        }
        if tools:
            body["tools"] = [{"functionDeclarations": tools}]
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO" if allow_calls else "NONE"}}
        data = self._post(body, key)
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError(f"Gemini returned no candidate ({data.get('promptFeedback', {}).get('blockReason', 'unknown')})")
        content = {"role": "model", **(candidates[0].get("content") or {})}
        parts = content.get("parts", [])
        calls = tuple(ToolCall(p["functionCall"]["name"], p["functionCall"].get("args") or {})
                      for p in parts if "functionCall" in p)
        text = "".join(p["text"] for p in parts if "text" in p and not p.get("thought")).strip()
        return Step(calls, text, content)

    def _post(self, body: dict, key: str) -> dict:
        url = extraction.ENDPOINT.format(model=self.model)
        for attempt in range(RETRIES + 1):
            try:
                response = httpx.post(url, json=body, timeout=TIMEOUT, headers={"x-goog-api-key": key})
                if response.status_code in RETRY_STATUS and attempt < RETRIES:
                    time.sleep(2 * (attempt + 1))
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as error:
                raise LLMError(f"Gemini returned HTTP {error.response.status_code}") from None
            except (httpx.HTTPError, ValueError) as error:
                if attempt == RETRIES:
                    raise LLMError(f"Gemini call failed: {type(error).__name__}") from None
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class Request:
    system: str
    contents: list[dict]
    tools: list[str]
    allow_calls: bool


class ScriptedLLM:
    """Replays prepared steps and records what it was asked. An item may be a function of the
    request, for a reply that depends on what the tools returned."""

    def __init__(self, script: Iterable[Step | Callable[[Request], Step]]):
        self.script = list(script)
        self.requests: list[Request] = []

    def step(self, system: str, contents: list[dict], tools: list[dict], *, allow_calls: bool = True) -> Step:
        request = Request(system, deepcopy(contents), [t["name"] for t in tools], allow_calls)
        self.requests.append(request)
        if not self.script:
            raise AssertionError("the script ran out of steps")
        item = self.script.pop(0)
        return item(request) if callable(item) else item
