"""A filled-in draft of the 勞資爭議調解申請書. Always Traditional Chinese: it is filed with a Taiwan 勞工局.

Fields follow 高雄市政府勞工局「勞資爭議調解申請書(1150513新版)」 and 勞資爭議處理法 §10.
"""

from labor_radar import fmt
from labor_radar.engine import Assessment
from labor_radar.i18n import fill, t
from labor_radar.model import PayBasis
from labor_radar.rules.core import Verdict

LANG = "zh-TW"
FORM_URL = (
    "https://orgws.kcg.gov.tw/001/KcgOrgUploadFiles/420/RelFile/74763/62424/"
    "624283e0-eff1-4a55-82c0-9f33d1499e24.pdf"
)
FORM_PAGE = "https://labor.kcg.gov.tw/cp.aspx?n=29690B60441433D1&s=1301B51BB7C67579"
MOL_TEMPLATE = "https://www.mol.gov.tw/media/d3tm2mx4/勞資爭議調解申請書.odt"
BLANK = "＿＿＿＿"

STORY = {
    ("minimum_wage", "hourly"): "雙方約定時薪 {agreed} 元，低於 {since} 起每小時{name} {minimum} 元。",
    ("minimum_wage", "monthly"): "{month} 約定月薪 {agreed} 元，低於每月{name} {minimum} 元。",
    ("overtime_pay", "hourly"): "{date} 工作 {hours} 小時，其中 {overtime} 小時為延長工作時間，請求依第 24 條第 1 項給付延長工時工資。",
    ("overtime_pay", "monthly"): "{date} 工作 {hours} 小時，其中 {overtime} 小時為延長工作時間，請求依第 24 條第 1 項給付延長工時工資。",
    ("daily_hours_cap", "over_12"): "{date} 工作 {hours} 小時，超過一日 12 小時之上限。",
    ("monthly_overtime_cap", "over_54"): "{month} 延長工作時間（含休息日出勤）共 {overtime} 小時，超過每月 54 小時之上限。",
    ("monthly_overtime_cap", "over_46"): "{month} 延長工作時間（含休息日出勤）共 {overtime} 小時，超過每月 46 小時之上限。",
    ("shift_rest", "under_11"): "{end} 下班後，{start} 即排定上班，更換班次之間僅休息 {rest} 小時，未達連續 11 小時。",
    ("shift_rest", "under_8"): "{end} 下班後，{start} 即排定上班，更換班次之間僅休息 {rest} 小時，未達連續 8 小時。",
    ("rest_break", "no_break"): "{dates} 各日連續工作逾 4 小時（最長 {longest} 小時），期間未給予休息。",
    ("regular_leave", "over_6"): "{first} 至 {last} 連續出勤 {days} 日，超過 6 日。",
    ("rest_day_pay", "inferred"): "{date} 所在之週休息不足 2 日，{date} 出勤 {hours} 小時應屬休息日工作，請求依第 24 條第 2 項給付休息日工資。",
    ("rest_day_pay", "inferred_assumed"): "以{weekday}為 7 日週期起日計算，{date} 所在之週休息不足 2 日，{date} 出勤 {hours} 小時應屬休息日工作，請求依第 24 條第 2 項給付休息日工資。",
    ("holiday_pay", "worked"): "{date}（{holiday}）出勤 {hours} 小時，請求依第 39 條加倍發給工資。",
}


def _story(rule_id: str, finding) -> str:
    template = STORY.get((rule_id, finding.variant))
    if template is None:
        return f"{t(LANG, f'rule.{rule_id}.title')}：{'、'.join(fmt.day(d) for d in finding.dates)}"
    return fill(LANG, template, finding.params)


def _or_blank(text: str) -> str:
    return text or BLANK


def mediation_draft(assessment: Assessment) -> str:
    record = assessment.record
    e = record.employment
    wage_label = "時薪" if e.pay_basis is PayBasis.HOURLY else "月薪"
    violations = assessment.findings(Verdict.VIOLATION)
    story = [
        f"{i}. {_story(rule.id, f)}（{'、'.join(c.label for c in rule.citations)}）"
        for i, (rule, f) in enumerate(violations, 1)
    ] or [f"1. {BLANK}"]
    claim = assessment.claim
    amount_lines: list[str] = []
    if claim.required is not None and claim.shortfall:
        amount_lines = [f"      {t(LANG, claim.base.template, **claim.base.params)} = {fmt.money(claim.base.amount)} 元"]
        amount_lines += [
            f"      {t(LANG, c.template, **c.params)} = {fmt.money(c.amount)} 元"
            for f in claim.components for c in f.calcs
        ]
        amount_lines += [
            f"      依法至少應領 {fmt.money(claim.required)} 元 − 已領 {fmt.money(claim.paid)} 元"
            f" = {int(fmt.round_ntd(claim.shortfall)):,} 元",
        ]
    requested = f"{int(fmt.round_ntd(claim.shortfall)):,}" if claim.shortfall else BLANK
    other = [rule.id for rule, f in violations if not f.calcs]
    lines = [
        t(LANG, "mediation.title"),
        f"參照表單：高雄市政府勞工局「勞資爭議調解申請書(1150513新版)」{FORM_URL}",
        f"（表單下載頁 {FORM_PAGE}；勞動部範本 {MOL_TEMPLATE}）",
        "依據：勞資爭議處理法第 9 條、第 10 條、第 11 條",
        "",
        "一、當事人",
        f"  申請人：{record.worker.name}　性別：{BLANK}　年齡：{BLANK}　職業：{BLANK}",
        f"          住居所：{_or_blank(record.worker.address)}　電話：{_or_blank(record.worker.phone)}",
        f"          身分證統一號碼：{BLANK}",
        f"          身分別：□一般 □未成年 □新住民 □原住民 □外籍移工：{BLANK}（國籍） □身心障礙者",
        "  代理人：（沒有就不填）",
        f"  對造人：{record.employer.name}　地址：{_or_blank(record.employer.address)}",
        f"  代表人或負責人：{_or_blank(record.employer.representative)}",
        "",
        "二、選定調解方式（擇一，並由申請人簽名確認）",
        "  □ 1. 調解人（由勞工局委託的民間團體指派）",
        "  □ 2. 調解人（由勞工局指派）",
        "  □ 3. 調解委員會",
        "",
        "三、爭議概況",
        f"  爭議發生時間：{fmt.day(record.period.start)} 至 {fmt.day(record.period.end)}",
        "  爭議總人數：1 人",
        f"  勞務提供地：{_or_blank(record.city)}",
        f"  勞動契約存續期間：{BLANK} 至 {BLANK}　□在職中",
        f"  雙方約定工資：{wage_label} {fmt.money(e.agreed_wage)} 元",
        "",
        "四、勞資爭議發生經過",
        *[f"  {line}" for line in story],
        "  ※本爭議案件是否係因美國對等關稅所致：□是 □否",
        "",
        "五、請求調解事項（可複選）",
        f"  ■ 工資（例：加班費）　請求金額：{requested} 元",
        "    計算方式：",
        *(amount_lines or [f"      {BLANK}"]),
        "    證據：□勞動契約　□薪資明細　□勞保資料　□出勤紀錄（LINE 或電子郵件紀錄等相關資料）"
        f"　□其他：班表 {BLANK}",
    ]
    if other:
        titles = "、".join(t(LANG, f"rule.{rule_id}.title") for rule_id in dict.fromkeys(other))
        lines.append(f"  ■ 其他請求事項：請雇主改善以下情形，今後依法排班：{titles}")
    lines += [
        "",
        "六、同意將地址、身分證統一號碼及電話提供給對造人：□同意　□不同意",
        "",
        f"申請人簽章：{BLANK}　撰寫人簽章：{BLANK}　日期：{BLANK}",
        "",
        t(LANG, "mediation.note"),
    ]
    return "\n".join(lines)
