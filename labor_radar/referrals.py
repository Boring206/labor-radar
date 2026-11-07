"""Where to get help. Every entry was read from the linked page on 2026-11-01."""

from dataclasses import dataclass

from labor_radar.i18n import t


@dataclass(frozen=True)
class Referral:
    key: str
    phone: str
    url: str


HOTLINE_1955 = Referral(
    "1955", "1955",
    "https://fw.wda.gov.tw/wda-employer/home/foreign-worker-affairs/searchByName?name=%E8%AB%AE%E8%A9%A2%E7%94%B3%E8%A8%B4",
)
LEGAL_AID = Referral("laf", "412-8518", "https://www.laf.org.tw/service-project-detail/9")
LOCAL_OFFICES = "https://www.mol.gov.tw/media/jmbfdmzt/勞資爭議調解仲裁申請-承辦單位聯繫資訊一覽表.pdf"

LOCAL: dict[str, Referral] = {
    "高雄市": Referral("local.kaohsiung", "(07)812-4613", "https://labor.kcg.gov.tw/cp.aspx?n=29690B60441433D1"),
}


def _entry(lang: str, referral: Referral) -> dict:
    return {
        "name": t(lang, f"referral.{referral.key}"),
        "detail": t(lang, f"referral.{referral.key}.detail", phone=referral.phone),
        "phone": referral.phone,
        "url": referral.url,
    }


def for_city(lang: str, city: str) -> list[dict]:
    local = LOCAL.get(city)
    entries = [_entry(lang, HOTLINE_1955)]
    if local:
        entries.append(_entry(lang, local))
    else:
        entries.append({"name": t(lang, "referral.local"), "detail": t(lang, "referral.local.detail"),
                        "phone": "", "url": LOCAL_OFFICES})
    entries.append(_entry(lang, LEGAL_AID))
    return entries
