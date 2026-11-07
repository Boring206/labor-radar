"""Drive the AI 助理 tab in a headless browser and save screenshots.

The app must be running with GEMINI_API_KEY set:  uv run uvicorn labor_radar.web:app --port 8730
uv run --with playwright python tools/agent_ui_shot.py --chrome ~/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome
"""

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OPENING = "我在手搖飲店打工，時薪 185。老闆每週自己排班，我上的時段不固定，這是這兩週的班表照片，請幫我看看。"
ANSWERS = ("我是受僱員工，要排班打卡領薪水。公司沒有說我是責任制，也沒有變形工時。"
           "店家每週自己排班，我上的時段不固定。這兩週薪資單上的應發金額是 8140 元。")
TURN_TIMEOUT_MS = 120_000


def wait_for_reply(page: Page) -> None:
    page.wait_for_selector(".bubble.pending", state="attached")
    page.wait_for_selector(".bubble.pending", state="detached", timeout=TURN_TIMEOUT_MS)


def send(page: Page, text: str, file: Path | None = None) -> None:
    if file:
        page.set_input_files("#chat-file", str(file))
    page.fill("#chat-input", text)
    page.click("#chat-send")
    wait_for_reply(page)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8730/?mode=agent")
    parser.add_argument("--chrome", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "samples")
    args = parser.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(args.chrome), headless=True)
        page = browser.new_page(viewport={"width": 1320, "height": 1500})
        page.goto(args.url)
        page.wait_for_selector("#agent-mode:not([hidden])")
        send(page, OPENING, ROOT / "samples" / "schedule_lin_xiaomei.jpg")
        page.screenshot(path=str(args.out / "agent_ui_clarify.png"))
        chip = page.locator(".chip", has_text="受僱員工")
        if chip.count():
            chip.first.click()
            wait_for_reply(page)
        for _ in range(4):
            if page.is_enabled("#confirm"):
                break
            send(page, ANSWERS)
        assert page.is_enabled("#confirm"), "the assistant never reached the confirm step"
        page.evaluate("document.getElementById('trace-card').open = true")
        page.screenshot(path=str(args.out / "agent_ui.png"))
        page.click("#confirm")
        page.wait_for_selector(".bubble.pending", state="attached")
        page.wait_for_selector(".bubble.pending", state="detached", timeout=TURN_TIMEOUT_MS)
        page.wait_for_selector("#agent-result .finding")
        page.screenshot(path=str(args.out / "agent_ui_report.png"), full_page=True)
        browser.close()


if __name__ == "__main__":
    main()
