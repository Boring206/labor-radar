"""Day-by-day facts every rule reads: hours, what kind of day it was, the wage floor.

A shift belongs to the day it starts. Days are grouped into 7-day cycles starting on
`week_start` (施行細則 §22-3: 以每七日為一週期，依曆計算); the start is agreed by the
parties, so when the worker does not know it the engine builds one ledger per weekday.
§36 I requires two rest days per cycle; when fewer were taken, the shortest worked days
are the ones treated as 休息日/例假 worked, which gives the smallest legal premium.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from fractions import Fraction

from labor_radar.model import NTD, Hours, PayBasis, Shift, WorkRecord
from labor_radar.rules import holidays, minimum_wage
from labor_radar.rules.holidays import Holiday
from labor_radar.rules.minimum_wage import MinimumWage

NORMAL_DAY_HOURS = Hours(Fraction(8))
MONTHLY_WAGE_DAYS = 30
FULL_TIME_MONTHLY_HOURS = Hours(Fraction(174))
REST_DAYS_PER_CYCLE = 2


class DayKind(StrEnum):
    OFF = "off"
    NORMAL = "normal"
    REST_DAY = "rest_day"
    REGULAR_LEAVE = "regular_leave"
    HOLIDAY = "holiday"


@dataclass(frozen=True)
class Day:
    day: date
    shifts: tuple[Shift, ...]
    kind: DayKind
    holiday: Holiday | None
    minimum: MinimumWage | None

    @property
    def hours(self) -> Hours:
        return Hours(sum((s.hours for s in self.shifts), Fraction(0)))

    @property
    def worked(self) -> bool:
        return self.kind is not DayKind.OFF


@dataclass(frozen=True)
class Ledger:
    record: WorkRecord
    days: tuple[Day, ...]
    week_start: int

    @property
    def week_start_known(self) -> bool:
        return self.record.employment.week_starts_on is not None

    @property
    def employment(self):
        return self.record.employment

    def worked_days(self) -> tuple[Day, ...]:
        return tuple(d for d in self.days if d.worked)

    def part_time_hours(self) -> Hours | None:
        """約定每月正常工作時數 of a monthly-paid worker who agreed to less than full time."""
        e = self.employment
        hours = e.agreed_monthly_hours
        if e.pay_basis is PayBasis.MONTHLY and hours is not None and hours < FULL_TIME_MONTHLY_HOURS:
            return hours
        return None

    def normal_hours(self, year: int, month: int) -> Hours:
        """正常工作時間 recorded in one calendar month: up to 8 h a day, 休息日 and 例假 work left out."""
        return Hours(sum(
            (min(d.hours, NORMAL_DAY_HOURS) for d in self.worked_days()
             if (d.day.year, d.day.month) == (year, month) and d.kind in (DayKind.NORMAL, DayKind.HOLIDAY)),
            Fraction(0),
        ))

    def agreed_hourly(self) -> NTD:
        e = self.employment
        if e.pay_basis is PayBasis.HOURLY:
            return e.agreed_wage
        part_time = self.part_time_hours()
        if part_time is not None:
            return NTD(e.agreed_wage / part_time)
        return NTD(e.agreed_wage / MONTHLY_WAGE_DAYS / NORMAL_DAY_HOURS)

    def hourly_rate(self, day: Day) -> NTD:
        """平日每小時工資額, never below the minimum wage in force that day.

        A monthly-paid part-timer gets 月薪 ÷ 約定每月正常工作時數 with no floor: their floor
        is prorated by 勞動部 guidance, which the minimum wage rule reports as needs_human.
        """
        e = self.employment
        if day.minimum is None or self.part_time_hours() is not None:
            return self.agreed_hourly()
        if e.pay_basis is PayBasis.HOURLY:
            return NTD(max(e.agreed_wage, day.minimum.hourly))
        return NTD(max(e.agreed_wage, day.minimum.monthly) / MONTHLY_WAGE_DAYS / NORMAL_DAY_HOURS)

    def daily_wage(self, day: Day) -> NTD:
        """One day's wage: 平日每小時工資額 × 8, which is 月薪 ÷ 30 for full-time monthly pay."""
        if self.part_time_hours() is not None:
            return NTD(self.employment.agreed_wage / MONTHLY_WAGE_DAYS)
        return NTD(self.hourly_rate(day) * NORMAL_DAY_HOURS)

    def overtime_hours(self, day: Day) -> Hours:
        """延長工作時間: past 8 h on a day (施行細則 §20-1; on 休假日/例假 per 勞動部 函釋), every 休息日 hour."""
        match day.kind:
            case DayKind.NORMAL | DayKind.HOLIDAY | DayKind.REGULAR_LEAVE:
                return Hours(max(Fraction(0), day.hours - NORMAL_DAY_HOURS))
            case DayKind.REST_DAY:
                return day.hours
            case DayKind.OFF:
                return Hours(Fraction(0))


def _cycle_start(day: date, week_starts_on: int) -> date:
    return day - timedelta(days=(day.weekday() - week_starts_on) % 7)


def build(record: WorkRecord, week_start: int | None = None) -> Ledger:
    """`week_start` overrides an unknown cycle start; a known one always wins."""
    known = record.employment.week_starts_on
    week_start = known if known is not None else (week_start or 0)
    shifts_by_day: dict[date, list[Shift]] = defaultdict(list)
    for shift in sorted(record.shifts, key=lambda s: s.start):
        shifts_by_day[shift.workday].append(shift)

    kinds: dict[date, DayKind] = {}
    for day in record.period.days():
        if not shifts_by_day.get(day):
            kinds[day] = DayKind.OFF
        elif (holiday := holidays.on(day)) and not holiday.voters_only:
            kinds[day] = DayKind.HOLIDAY
        else:
            kinds[day] = DayKind.NORMAL

    def hours_on(day: date) -> Fraction:
        return sum((s.hours for s in shifts_by_day[day]), Fraction(0))

    cycle_starts = sorted({_cycle_start(d, week_start) for d in kinds})
    for start in cycle_starts:
        cycle = [start + timedelta(days=i) for i in range(7)]
        known = [d for d in cycle if d in kinds]
        rest_taken = sum(1 for d in known if kinds[d] is DayKind.OFF) + (7 - len(known))
        missing = max(0, REST_DAYS_PER_CYCLE - rest_taken)
        candidates = sorted(
            (d for d in known if kinds[d] is not DayKind.OFF),
            key=lambda d: (kinds[d] is DayKind.HOLIDAY, hours_on(d), d),
        )
        for day, kind in zip(candidates[:missing], (DayKind.REST_DAY, DayKind.REGULAR_LEAVE), strict=False):
            kinds[day] = kind

    days = tuple(
        Day(
            day=day,
            shifts=tuple(shifts_by_day.get(day, ())),
            kind=kind,
            holiday=holidays.on(day),
            minimum=minimum_wage.in_force_on(day),
        )
        for day, kind in kinds.items()
    )
    return Ledger(record=record, days=days, week_start=week_start)
