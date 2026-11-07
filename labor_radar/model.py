"""One worker's facts, already parsed and trusted. Built only by `intake`."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import NewType

Hours = NewType("Hours", Fraction)
NTD = NewType("NTD", Fraction)

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class PayBasis(StrEnum):
    HOURLY = "hourly"
    MONTHLY = "monthly"


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class ShiftSystem(StrEnum):
    """How the worker describes the schedule. FLEXIBLE is the store that books part-timers into
    whatever slots it needs each week: probably not a 輪班制 under §34, but that is disputed."""

    ROTATING = "rotating"
    FLEXIBLE = "flexible"
    FIXED = "fixed"
    UNKNOWN = "unknown"


class Relationship(StrEnum):
    EMPLOYEE = "employee"
    CONTRACTOR = "contractor"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    def __contains__(self, day: date) -> bool:
        return self.start <= day <= self.end

    def days(self) -> Iterator[date]:
        day = self.start
        while day <= self.end:
            yield day
            day += timedelta(days=1)

    def months(self) -> tuple[tuple[int, int], ...]:
        """Every calendar month the period touches, in order."""
        months = []
        year, month = self.start.year, self.start.month
        while (year, month) <= (self.end.year, self.end.month):
            months.append((year, month))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return tuple(months)

    def whole_months(self) -> tuple[tuple[int, int], ...] | None:
        """The calendar months the period spans, or None if it cuts a month."""
        if self.start.day != 1 or (self.end + timedelta(days=1)).day != 1:
            return None
        return self.months()


@dataclass(frozen=True)
class Party:
    name: str
    address: str = ""
    phone: str = ""
    representative: str = ""


@dataclass(frozen=True)
class Shift:
    start: datetime
    end: datetime
    break_minutes: int = 0

    @property
    def workday(self) -> date:
        return self.start.date()

    @property
    def crosses_midnight(self) -> bool:
        return self.end.date() != self.start.date() and self.end.time() != datetime.min.time()

    @property
    def hours(self) -> Hours:
        minutes = int((self.end - self.start).total_seconds()) // 60 - self.break_minutes
        return Hours(Fraction(minutes, 60))

    @property
    def clock(self) -> tuple[str, str]:
        return self.start.strftime("%H:%M"), self.end.strftime("%H:%M")


@dataclass(frozen=True)
class Employment:
    pay_basis: PayBasis
    agreed_wage: NTD
    relationship: Relationship
    domestic_worker: bool
    shift_system: ShiftSystem
    special_hours_arrangement: Answer
    responsibility_system: Answer
    overtime_consent: Answer
    week_starts_on: int | None = None
    """Weekday (0 = Monday) the employer's 7-day cycle starts on, or None if the worker does not know."""
    agreed_monthly_hours: Hours | None = None


@dataclass(frozen=True)
class PaySlip:
    period: Period
    gross_pay: NTD
    self_reported: bool


@dataclass(frozen=True)
class WorkRecord:
    worker: Party
    employer: Party
    employment: Employment
    period: Period
    shifts: tuple[Shift, ...]
    payslips: tuple[PaySlip, ...]
    city: str = ""
