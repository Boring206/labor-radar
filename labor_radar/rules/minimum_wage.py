"""Wage floor by effective date. Add a row when 勞動部 announces one.

A proposal enters as PENDING_APPROVAL and is never used for a verdict; flip it to
IN_FORCE once 勞動部 公告 it under 最低工資法 §13 I (which follows 行政院核定).
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from fractions import Fraction

from labor_radar.model import NTD


class Status(StrEnum):
    IN_FORCE = "in_force"
    PENDING_APPROVAL = "pending_approval"


@dataclass(frozen=True)
class MinimumWage:
    effective_from: date
    name: str
    monthly: NTD
    hourly: NTD
    status: Status
    source: str


HISTORY = "https://www.mol.gov.tw/1607/28162/28166/28180/99861/28184/29016/"
GAZETTE_116 = "https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg032192/ch08/type1/gov82/num28/Eg.htm"


def _row(day: date, name: str, monthly: int, hourly: int, status: Status, source: str) -> MinimumWage:
    return MinimumWage(day, name, NTD(Fraction(monthly)), NTD(Fraction(hourly)), status, source)


TABLE: tuple[MinimumWage, ...] = (
    _row(date(2024, 1, 1), "基本工資", 27470, 183, Status.IN_FORCE, HISTORY),
    _row(date(2025, 1, 1), "最低工資", 28590, 190, Status.IN_FORCE, HISTORY),
    _row(date(2026, 1, 1), "最低工資", 29500, 196, Status.IN_FORCE, HISTORY),
    _row(date(2027, 1, 1), "最低工資", 30900, 205, Status.IN_FORCE, GAZETTE_116),
)

FIRST_COVERED_DAY = date(2024, 1, 1)
LAST_COVERED_DAY = date(2027, 12, 31)


def in_force_on(day: date) -> MinimumWage | None:
    """The row in force on `day`, or None outside the covered span: past LAST_COVERED_DAY a new
    amount may already apply, so the last known row is never carried forward."""
    if not FIRST_COVERED_DAY <= day <= LAST_COVERED_DAY:
        return None
    rows = [r for r in TABLE if r.status is Status.IN_FORCE and r.effective_from <= day]
    return max(rows, key=lambda r: r.effective_from, default=None)


def pending_on(day: date) -> MinimumWage | None:
    """A proposed amount that would govern `day` if approved."""
    current = in_force_on(day)
    rows = [
        r for r in TABLE
        if r.status is Status.PENDING_APPROVAL and r.effective_from <= day
        and (current is None or r.effective_from > current.effective_from)
    ]
    return max(rows, key=lambda r: r.effective_from, default=None)
