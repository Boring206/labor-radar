"""Regenerate labor_radar/rules/sources.md from law.moj.gov.tw.

Run: uv run python tools/fetch_sources.py
Every article cited in labor_radar/rules/law.py is fetched and quoted verbatim,
followed by the non-statute sources in tools/sources_extra.md.
"""

import html
import re
import ssl
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from labor_radar.rules import law  # noqa: E402

LAW_ALL = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}"

# law.moj.gov.tw's certificate lacks a Subject Key Identifier, which Python 3.13's
# strict X.509 mode rejects. Keep chain and hostname checks; drop only strict mode.
TLS = ssl.create_default_context()
TLS.verify_flags &= ~ssl.VERIFY_X509_STRICT


def _clean(fragment: str) -> str:
    fragment = re.sub(r'<div class="line-\d+[^"]*">', "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def fetch(pcode: str) -> tuple[str, dict[str, str]]:
    page = httpx.get(LAW_ALL.format(pcode=pcode), timeout=60, follow_redirects=True, verify=TLS).text
    amended = re.search(r"(?:修正|公布|制定)日期：</th>\s*<td>([^<]+)<", page)
    articles = {}
    for number, body in re.findall(r'<div class="col-no">(.*?)</div>\s*<div class="col-data">(.*?)</div>\s*</div>', page, re.S):
        key = re.sub(r"[第條\s]", "", _clean(number))
        articles[key] = _clean(body)
    return (amended.group(1).strip() if amended else "?"), articles


def main() -> None:
    by_law: dict[str, list[law.Citation]] = {}
    for citation in law.ALL:
        by_law.setdefault(citation.law, []).append(citation)
    out = [
        "# 法條原文",
        "",
        f"由 `tools/fetch_sources.py` 於 {date.today().isoformat()} 從全國法規資料庫產生，逐字引用，不要手改。",
        "",
    ]
    for law_name, citations in by_law.items():
        amended, articles = fetch(law._PCODES[law_name])
        out.append(f"> {law_name}：全國法規資料庫標示日期 {amended}")
        out.append("")
        for flno in dict.fromkeys(c.flno for c in citations):
            first = next(c for c in citations if c.flno == flno)
            out += [f"## {law_name}第{flno}條", "", first.url, ""]
            out += [f"> {line}" if line else ">" for line in articles[flno].splitlines()]
            out.append("")
    extra = (ROOT / "tools" / "sources_extra.md").read_text(encoding="utf-8")
    (ROOT / "labor_radar" / "rules" / "sources.md").write_text("\n".join(out) + "\n" + extra, encoding="utf-8")


if __name__ == "__main__":
    main()
