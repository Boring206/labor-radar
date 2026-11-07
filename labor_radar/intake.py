"""The boundary. Untrusted JSON (from the form, a file, or the vision model) becomes a WorkRecord here."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from labor_radar.model import (
    NTD,
    WEEKDAYS,
    Answer,
    Employment,
    Hours,
    Party,
    PayBasis,
    PaySlip,
    Period,
    Relationship,
    Shift,
    ShiftSystem,
    WorkRecord,
)

UNKNOWN_WEEKDAY = "unknown"
MAX_PERIOD_DAYS = 400
EARLIEST = date(2000, 1, 1)
LATEST = date(2100, 12, 31)
"""A date outside these is a typo. Inside them, the engine's overnight and week arithmetic cannot overflow."""
Day = Annotated[date, Field(ge=EARLIEST, le=LATEST)]


class IntakeError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PartyIn(_Strict):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field("", max_length=200)
    phone: str = Field("", max_length=40)
    representative: str = Field("", max_length=100)


class EmploymentIn(_Strict):
    pay_basis: PayBasis
    agreed_wage: Decimal = Field(gt=0, le=1_000_000)
    relationship: Relationship
    domestic_worker: bool
    shift_system: ShiftSystem
    special_hours_arrangement: Answer
    responsibility_system: Answer
    overtime_consent: Answer
    week_starts_on: Literal[(*WEEKDAYS, UNKNOWN_WEEKDAY)] = UNKNOWN_WEEKDAY
    agreed_monthly_hours: Decimal | None = Field(None, gt=0, le=744)


class PeriodIn(_Strict):
    start: Day
    end: Day

    @model_validator(mode="after")
    def _ordered(self):
        if self.end < self.start:
            raise ValueError("period.end is before period.start")
        if (self.end - self.start).days >= MAX_PERIOD_DAYS:
            raise ValueError(f"period is longer than {MAX_PERIOD_DAYS} days")
        return self


class ShiftIn(_Strict):
    date: Day
    start: time
    end: time
    break_minutes: int = Field(0, ge=0, le=600)


class PaySlipIn(_Strict):
    period: PeriodIn
    gross_pay: Decimal = Field(ge=0, le=10_000_000)
    self_reported: bool = False


class RecordIn(_Strict):
    worker: PartyIn
    employer: PartyIn
    city: str = Field("", max_length=20)
    employment: EmploymentIn
    period: PeriodIn
    shifts: list[ShiftIn] = Field(max_length=1000)
    payslips: list[PaySlipIn] = Field(default_factory=list, max_length=24)


def _shift(raw: ShiftIn) -> Shift:
    start = datetime.combine(raw.date, raw.start)
    end = datetime.combine(raw.date, raw.end)
    if end <= start:
        end += timedelta(days=1)
    return Shift(start=start, end=end, break_minutes=raw.break_minutes)


def _period(raw: PeriodIn) -> Period:
    return Period(raw.start, raw.end)


def _tiles(period: Period, parts: list[Period]) -> bool:
    """Whether the parts cover the period exactly once, with no gap or overlap."""
    day = period.start
    for part in sorted(parts, key=lambda p: p.start):
        if part.start != day:
            return False
        day = part.end + timedelta(days=1)
    return day == period.end + timedelta(days=1)


def _party(raw: PartyIn) -> Party:
    return Party(raw.name, raw.address, raw.phone, raw.representative)


def parse_record(data: object) -> WorkRecord:
    try:
        raw = RecordIn.model_validate(data)
    except ValidationError as error:
        raise IntakeError([
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()
        ]) from None

    period = _period(raw.period)
    shifts = sorted((_shift(s) for s in raw.shifts), key=lambda s: s.start)
    problems = []
    for index, shift in enumerate(shifts):
        if shift.workday not in period:
            problems.append(f"shift on {shift.workday} is outside the period {period.start}..{period.end}")
        if shift.break_minutes * 60 >= (shift.end - shift.start).total_seconds():
            problems.append(f"shift on {shift.workday}: break is as long as the shift")
        if index and shift.start < shifts[index - 1].end:
            problems.append(f"shift starting {shift.start:%Y-%m-%d %H:%M} overlaps the previous shift")
    slips = [_period(p.period) for p in raw.payslips]
    if slips and not _tiles(period, slips):
        covered = ", ".join(f"{s.start}..{s.end}" for s in slips)
        problems.append(f"payslips cover {covered}, not exactly the record period {period.start}..{period.end}; "
                        "give the gross pay for the record period only, so the check does not compare different spans")
    if problems:
        raise IntakeError(problems)

    e = raw.employment
    return WorkRecord(
        worker=_party(raw.worker),
        employer=_party(raw.employer),
        employment=Employment(
            pay_basis=e.pay_basis,
            agreed_wage=NTD(Fraction(e.agreed_wage)),
            relationship=e.relationship,
            domestic_worker=e.domestic_worker,
            shift_system=e.shift_system,
            special_hours_arrangement=e.special_hours_arrangement,
            responsibility_system=e.responsibility_system,
            overtime_consent=e.overtime_consent,
            week_starts_on=None if e.week_starts_on == UNKNOWN_WEEKDAY else WEEKDAYS.index(e.week_starts_on),
            agreed_monthly_hours=None if e.agreed_monthly_hours is None else Hours(Fraction(e.agreed_monthly_hours)),
        ),
        period=period,
        shifts=tuple(shifts),
        payslips=tuple(
            PaySlip(_period(p.period), NTD(Fraction(p.gross_pay)), p.self_reported) for p in raw.payslips
        ),
        city=raw.city,
    )
