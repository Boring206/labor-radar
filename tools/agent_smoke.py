"""Drive the agent with the real Gemini model through a scripted worker conversation and save the transcript.

GEMINI_API_KEY=... uv run python tools/agent_smoke.py [--out samples/agent_demo.transcript.md]
Spends about a dozen Gemini calls. The key is read from the environment and never printed or written.
"""

import argparse
import json
import mimetypes
import sys
from pathlib import Path

from labor_radar import extraction
from labor_radar.agent import questions
from labor_radar.agent.case import Case, Phase
from labor_radar.agent.llm import GeminiLLM
from labor_radar.agent.loop import TurnResult, run_turn
from labor_radar.agent.tools import Attachment

ROOT = Path(__file__).resolve().parents[1]
PHOTO = ROOT / "samples" / "schedule_lin_xiaomei.jpg"
OPENING = ("我在手搖飲店打工，時薪 185。老闆每週自己排班，我上的時段不固定，這是這兩週的班表照片，"
           "請幫我看看。")
PERSONA = {
    "relationship": "我是受僱員工，要排班、要打卡，領薪水。",
    "shift_system": "店家每週自己排班，我上的時段不固定。",
    "special_hours_arrangement": "公司沒有說過變形工時，也沒有調整例假。",
    "responsibility_system": "公司沒有說我是責任制。",
    "overtime_consent": "不知道有沒有經過工會同意。",
    "week_starts_on": "我不知道公司的 7 日週期從星期幾起算。",
}
PAYSLIP = "這兩週薪資單上的應發金額是 8140 元。"
GATHER_NUDGE = "班表上的日期和時段都在照片裡，時薪就是我說的 185 元。"
MAX_CLARIFY_TURNS = 6


class Counting:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def step(self, *args, **kwargs):
        self.calls += 1
        return self.inner.step(*args, **kwargs)


def render_turn(index: int, message: str, attached: str | None, result: TurnResult) -> list[str]:
    lines = [f"## 第 {index} 輪", "", f"**工人**：{message}" + (f"（附檔：{attached}）" if attached else ""), ""]
    if result.trace:
        lines.append("代理人做了什麼：")
        lines.append("")
    for event in result.trace:
        if event["kind"] == "tool":
            status = "成功" if event["ok"] else f"被拒絕：{event['summary']}"
            lines.append(f"- 工具 `{event['tool']}` {event['args']} → {status}")
            for rejected in event.get("rejected", []):
                lines.append(f"  - 被拒絕的值：`{rejected['path']} = {rejected['value']}`，原因：{rejected['error']}")
        elif event["kind"] == "guard":
            detail = event["detail"]
            lines.append(f"- 護欄（{event['action']}）攔下：「{detail['text']}」")
            lines += [f"  - {p['message']}" for p in detail["problems"]]
            if detail.get("rewrite"):
                lines.append(f"  - 重寫後：「{detail['rewrite']}」")
        else:
            lines.append(f"- {event['kind']} {event.get('message', '')}")
    lines += ["", f"**助理**：{result.reply}", "", f"階段：`{result.phase.value}`", ""]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "samples" / "agent_demo.transcript.md")
    args = parser.parse_args()
    if not extraction.available():
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2

    llm = Counting(GeminiLLM())
    vision_calls = 0
    real_extract = extraction.extract

    def counted_extract(*a, **k):
        nonlocal vision_calls
        vision_calls += 1
        return real_extract(*a, **k)

    extraction.extract = counted_extract
    photo = Attachment(PHOTO.read_bytes(), mimetypes.guess_type(PHOTO.name)[0] or "image/jpeg", PHOTO.name)
    out = ["# 勞權雷達 AI 助理：實機對話紀錄", "",
           f"模型：Gemini（{llm.inner.model}）。班表照片：`samples/{PHOTO.name}`（虛構）。工人的話是腳本，助理的回覆和工具呼叫是實際執行結果。", ""]

    case, turn = Case(), 0
    script = [(OPENING, photo)]
    asked: set[str] = set()
    result = None
    while True:
        if script:
            message, attachment = script.pop(0)
        elif result.phase in (Phase.CLARIFY, Phase.GATHER):
            if turn >= MAX_CLARIFY_TURNS:
                break
            pending = [q["id"] for q in result.questions["questions"] if q["id"] not in asked]
            asked.update(pending)
            message = " ".join(PERSONA[i] for i in pending) or GATHER_NUDGE
            if turn == 1:
                message += " " + PAYSLIP
            attachment = None
        else:
            break
        turn += 1
        result = run_turn(case, message, llm, "zh-TW", attachment, debug=True)
        case = result.case
        out += render_turn(turn, message, attachment.name if attachment else None, result)

    out += ["## 資料表（確認前）", ""]
    out += [f"- {row['label']}：{row['value']}（{row['source']}{'，假設' if row['assumed'] else ''}）" for row in result.facts]
    out += ["", f"沒問的題目：{'、'.join(q['question'] for q in result.questions['not_asked']) or '（無）'}", ""]

    if result.phase is Phase.CONFIRM:
        case = case.model_copy(update={"confirmed": True})
        for message in ("我確認資料沒問題，請告訴我結果。", "第 35 條是什麼意思？", "請幫我寫勞資爭議調解申請書。"):
            turn += 1
            result = run_turn(case, message, llm, "zh-TW", debug=True)
            case = result.case
            out += render_turn(turn, message, None, result)
        out += ["## 規則引擎的結果（最後一次檢查）", "", *[f"- {line}" for line in result.report_view["summary"]], ""]
        out += [f"- {v['title']}：{v['explanation']}" for v in result.report_view["violations"]]
        out += ["", "## 調解申請書草稿（節錄）", "", "```text",
                *result.report_view["mediation_draft"].splitlines()[:40], "```", ""]
    else:
        out += [f"（停在 `{result.phase.value}` 階段，沒有進入確認。）", ""]

    out += ["## 用量", "", f"- 對話模型呼叫：{llm.calls} 次", f"- 照片轉錄（視覺）呼叫：{vision_calls} 次",
            f"- 總計：{llm.calls + vision_calls} 次 Gemini 呼叫", ""]
    args.out.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {args.out}; chat calls {llm.calls}, vision calls {vision_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
