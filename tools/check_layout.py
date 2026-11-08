"""Measure whether the header, notice bar, tab row and the active tab's content share one left and right edge.

The app must be running with any GEMINI_API_KEY set (the AI 助理 tab is disabled without one; this script sends no request):
    GEMINI_API_KEY=x uv run uvicorn labor_radar.web:app --port 8741
    uv run --with playwright python tools/check_layout.py --url http://127.0.0.1:8741/ --chrome <chromium path>
Exit code 1 when any left or right edge differs by more than 1 px, or the page scrolls sideways.
"""
import argparse
import json
import sys

from playwright.sync_api import sync_playwright

MEASURE = """
() => {
  const box = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return { left: r.left + parseFloat(s.paddingLeft), right: r.right - parseFloat(s.paddingRight) };
  };
  const rect = (el) => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right }; };
  const main = document.querySelector('main:not([hidden])');
  const cards = [...main.querySelectorAll('.card')].filter((c) => c.offsetParent !== null && !c.closest('details'));
  return {
    'header content': box(document.querySelector('header')),
    'title (h1)': rect(document.querySelector('header h1')),
    'language select': rect(document.querySelector('#lang')),
    'notice bar content': box(document.querySelector('.banner.legal')),
    'tab row content': box(document.querySelector('nav.modes')),
    'first tab button': rect(document.querySelector('nav.modes button')),
    'main content': box(main),
    'cards (min left, max right)': {
      left: Math.min(...cards.map((c) => c.getBoundingClientRect().left)),
      right: Math.max(...cards.map((c) => c.getBoundingClientRect().right)),
    },
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  };
}
"""
LEFT_KEYS = ["header content", "title (h1)", "notice bar content", "tab row content", "first tab button", "main content", "cards (min left, max right)"]
RIGHT_KEYS = ["header content", "language select", "notice bar content", "tab row content", "main content", "cards (min left, max right)"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8741/")
    ap.add_argument("--chrome", default=None)
    ap.add_argument("--widths", default="1920,375")
    args = ap.parse_args()
    bad = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True)
        for width in (int(w) for w in args.widths.split(",")):
            page = browser.new_page(viewport={"width": width, "height": 1080})
            page.goto(args.url)
            page.wait_for_selector("#tab-agent:not([disabled])")
            for tab in ("form", "agent"):
                page.click(f"#tab-{tab}")
                m = page.evaluate(MEASURE)
                print(f"\n{width}px wide, tab {tab}")
                for k, v in m.items():
                    if isinstance(v, dict):
                        print(f"  {k:30} left {v['left']:8.1f}  right {v['right']:8.1f}")
                lefts = [m[k]["left"] for k in LEFT_KEYS]
                rights = [m[k]["right"] for k in RIGHT_KEYS]
                dl, dr = max(lefts) - min(lefts), max(rights) - min(rights)
                sideways = m["scrollWidth"] > m["clientWidth"]
                ok = dl <= 1 and dr <= 1 and not sideways
                bad += not ok
                print(f"  spread left {dl:.1f}  right {dr:.1f}  scrollWidth {m['scrollWidth']} / {m['clientWidth']}  -> {'OK' if ok else 'MISALIGNED'}")
            page.close()
        browser.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
