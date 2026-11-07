"""放假日 under 紀念日及節日實施條例 §4 and §6, on their actual dates.

Workers get the actual date; the civil-service 補假/調整放假 calendar does not apply.
A 投票日 is a §37 I 應放假日 only for a worker who may vote and was due to work that day
(勞動部107年11月8日勞動條3字第1070131460號公告), so it is `voters_only`.
"""

from dataclasses import dataclass
from datetime import date

from labor_radar.model import Period


@dataclass(frozen=True)
class Holiday:
    day: date
    name: str
    article: str
    voters_only: bool = False


COVERED = Period(date(2026, 1, 1), date(2027, 1, 31))

TABLE: tuple[Holiday, ...] = (
    Holiday(date(2026, 1, 1), "中華民國開國紀念日", "第4條"),
    Holiday(date(2026, 2, 15), "除夕前一日（小年夜）", "第6條第1項第1款"),
    Holiday(date(2026, 2, 16), "除夕", "第6條第1項第1款"),
    Holiday(date(2026, 2, 17), "春節（初一）", "第6條第1項第1款"),
    Holiday(date(2026, 2, 18), "春節（初二）", "第6條第1項第1款"),
    Holiday(date(2026, 2, 19), "春節（初三）", "第6條第1項第1款"),
    Holiday(date(2026, 2, 28), "和平紀念日", "第4條"),
    Holiday(date(2026, 4, 4), "兒童節", "第6條第1項第2款"),
    Holiday(date(2026, 4, 5), "清明節", "第6條第1項第2款"),
    Holiday(date(2026, 5, 1), "勞動節", "第6條第1項第2款"),
    Holiday(date(2026, 6, 19), "端午節", "第6條第1項第2款"),
    Holiday(date(2026, 9, 25), "中秋節", "第6條第1項第2款"),
    Holiday(date(2026, 9, 28), "孔子誕辰紀念日／教師節", "第4條、第6條第1項第2款"),
    Holiday(date(2026, 10, 10), "國慶日", "第4條"),
    Holiday(date(2026, 10, 25), "臺灣光復暨金門古寧頭大捷紀念日", "第4條"),
    Holiday(date(2026, 11, 28), "115年地方公職人員選舉投票日", "勞動部勞動條3字第1070131460號公告", voters_only=True),
    Holiday(date(2026, 12, 25), "行憲紀念日", "第4條"),
    Holiday(date(2027, 1, 1), "中華民國開國紀念日", "第4條"),
)

_BY_DAY = {h.day: h for h in TABLE}


def on(day: date) -> Holiday | None:
    return _BY_DAY.get(day)
