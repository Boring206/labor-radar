"""The rule table. One entry per legal requirement the engine checks.

Each check is a pure function of the ledger. Explanations live in the i18n
catalogs under `rule.<id>.<variant>`; amounts are the premium or shortfall the
article requires, shown line by line as `Calc`s.
"""

from collections import defaultdict
from collections.abc import Iterator
from datetime import date, timedelta
from fractions import Fraction
from itertools import pairwise

from labor_radar import fmt
from labor_radar.i18n import Text
from labor_radar.model import NTD, WEEKDAYS, Answer, PayBasis, ShiftSystem
from labor_radar.rules import holidays, law, minimum_wage
from labor_radar.rules.core import Calc, Finding, Gate, Rule, Verdict
from labor_radar.rules.ledger import (
    FULL_TIME_MONTHLY_HOURS,
    NORMAL_DAY_HOURS,
    DayKind,
    Ledger,
)

LSA_IN_FORCE = date(1984, 8, 1)  # 73年7月30日公布，第86條自公布日施行；中央法規標準法第13條：第三日起
AMENDED_2018 = date(2018, 3, 1)
MINIMUM_WAGE_ACT = date(2024, 1, 1)
HOLIDAY_ACT = date(2025, 5, 30)

DAILY_CAP = Fraction(12)
MONTHLY_CAP = Fraction(46)
MONTHLY_CAP_WITH_CONSENT = Fraction(54)
QUARTERLY_CAP_WITH_CONSENT = Fraction(138)
MAX_CONSECUTIVE_DAYS = 6
MIN_REST = timedelta(hours=11)
MIN_REST_ANNOUNCED = timedelta(hours=8)
MAX_STRETCH_MINUTES = 4 * 60
MIN_BREAK_MINUTES = 30

OVERTIME_TIERS = ((Fraction(2), Fraction(1, 3)), (None, Fraction(2, 3)))
REST_DAY_TIERS = ((Fraction(2), Fraction(4, 3)), (None, Fraction(5, 3)))


def _split(hours: Fraction, tiers) -> Iterator[tuple[int, Fraction, Fraction]]:
    """Cut `hours` into consecutive tiers of (width, rate); width None is unbounded."""
    remaining = hours
    for index, (width, rate) in enumerate(tiers):
        take = remaining if width is None else min(remaining, width)
        if take > 0:
            yield index, take, rate
        remaining -= take


def check_minimum_wage(ledger: Ledger) -> Iterator[Finding]:
    e = ledger.employment
    worked = ledger.worked_days()
    months = ledger.record.period.whole_months()
    if e.pay_basis is PayBasis.MONTHLY and months is not None:
        uncovered = [date(y, m, 1) for y, m in months if minimum_wage.in_force_on(date(y, m, 1)) is None]
    else:
        uncovered = [d.day for d in worked if d.minimum is None]
    if uncovered:
        yield Finding("minimum_wage", Verdict.NEEDS_HUMAN, "out_of_range", tuple(uncovered),
                      {"first": fmt.day(minimum_wage.FIRST_COVERED_DAY),
                       "last": fmt.day(minimum_wage.LAST_COVERED_DAY)})

    if e.pay_basis is PayBasis.HOURLY:
        by_row: dict[minimum_wage.MinimumWage, list] = defaultdict(list)
        for d in worked:
            if d.minimum is not None and d.minimum.hourly > e.agreed_wage:
                by_row[d.minimum].append(d)
        for row, days in by_row.items():
            total = sum((d.hours for d in days), Fraction(0))
            gap = row.hourly - e.agreed_wage
            yield Finding(
                "minimum_wage", Verdict.VIOLATION, "hourly", tuple(d.day for d in days),
                {"agreed": fmt.money(e.agreed_wage), "minimum": fmt.money(row.hourly),
                 "since": fmt.day(row.effective_from), "name": row.name},
                (Calc("calc.minimum_wage.hourly",
                      {"hours": fmt.hours(total), "minimum": fmt.money(row.hourly),
                       "agreed": fmt.money(e.agreed_wage)},
                      NTD(total * gap)),),
            )
    else:
        if months is None:
            yield Finding("minimum_wage", Verdict.NEEDS_HUMAN, "partial_month")
        else:
            for year, month in months:
                yield from _monthly_minimum(ledger, year, month)

    pending_days = [d for d in worked if minimum_wage.pending_on(d.day) is not None]
    if pending_days and e.pay_basis is PayBasis.HOURLY:
        row = minimum_wage.pending_on(pending_days[0].day)
        extra = sum(
            (d.hours * max(Fraction(0), row.hourly - ledger.hourly_rate(d)) for d in pending_days),
            Fraction(0),
        )
        yield Finding(
            "minimum_wage", Verdict.NOTE, "pending", tuple(d.day for d in pending_days),
            {"since": fmt.day(row.effective_from), "hourly": fmt.money(row.hourly),
             "monthly": fmt.money(row.monthly), "extra": fmt.money(extra)},
        )


def _monthly_minimum(ledger: Ledger, year: int, month: int) -> Iterator[Finding]:
    """The monthly floor assumes full time (174 h). Below that, 勞動部's 部分時間工作 注意事項
    陸、二(一) prorates it by hours; that is guidance, and the full-time hours of this employer
    may be fewer than 174, so a prorated shortfall is needs_human, never a violation."""
    e = ledger.employment
    first = date(year, month, 1)
    row = minimum_wage.in_force_on(first)
    if row is None or row.monthly <= e.agreed_wage:
        return
    params = {"agreed": fmt.money(e.agreed_wage), "minimum": fmt.money(row.monthly),
              "month": f"{year}-{month:02d}", "name": row.name}
    agreed_hours = e.agreed_monthly_hours
    hours = agreed_hours if agreed_hours is not None else ledger.normal_hours(year, month)
    if hours >= FULL_TIME_MONTHLY_HOURS:
        yield Finding("minimum_wage", Verdict.VIOLATION, "monthly", (first,), params,
                      (Calc("calc.minimum_wage.monthly", params, NTD(row.monthly - e.agreed_wage)),))
        return
    floor = NTD(row.monthly * hours / FULL_TIME_MONTHLY_HOURS)
    if agreed_hours is not None and floor <= e.agreed_wage:
        return
    params |= {"hours": fmt.hours(hours), "full": fmt.hours(FULL_TIME_MONTHLY_HOURS), "floor": fmt.money(floor)}
    calcs = (Calc("calc.minimum_wage.part_time", params, NTD(floor - e.agreed_wage)),) if floor > e.agreed_wage else ()
    variant = "part_time" if agreed_hours is not None else "part_time_hours_unknown"
    yield Finding("minimum_wage", Verdict.NEEDS_HUMAN, variant, (first,), params, calcs)


def check_regular_hours(ledger: Ledger) -> Iterator[Finding]:
    long_days = [d for d in ledger.worked_days() if d.kind is DayKind.NORMAL and d.hours > NORMAL_DAY_HOURS]
    if long_days:
        yield Finding(
            "regular_hours", Verdict.NOTE, "over_8", tuple(d.day for d in long_days),
            {"days": len(long_days),
             "overtime": fmt.hours(sum((ledger.overtime_hours(d) for d in long_days), Fraction(0)))},
        )


def check_overtime_pay(ledger: Ledger) -> Iterator[Finding]:
    monthly = ledger.employment.pay_basis is PayBasis.MONTHLY
    for d in ledger.worked_days():
        if d.kind not in (DayKind.NORMAL, DayKind.HOLIDAY) or d.hours <= NORMAL_DAY_HOURS:
            continue
        rate = ledger.hourly_rate(d)
        calcs = []
        for tier, hours, premium in _split(d.hours - NORMAL_DAY_HOURS, OVERTIME_TIERS):
            multiplier = premium + 1 if monthly else premium
            calcs.append(Calc(
                f"calc.overtime_pay.tier{tier + 1}",
                {"date": fmt.day(d.day), "hours": fmt.hours(hours), "rate": fmt.money(rate),
                 "factor": fmt.factor(multiplier)},
                NTD(hours * rate * multiplier),
            ))
        yield Finding(
            "overtime_pay", Verdict.VIOLATION, "monthly" if monthly else "hourly", (d.day,),
            {"date": fmt.day(d.day), "hours": fmt.hours(d.hours),
             "overtime": fmt.hours(d.hours - NORMAL_DAY_HOURS)},
            tuple(calcs),
        )


def check_daily_cap(ledger: Ledger) -> Iterator[Finding]:
    for d in ledger.worked_days():
        if d.hours > DAILY_CAP:
            yield Finding("daily_hours_cap", Verdict.VIOLATION, "over_12", (d.day,),
                          {"date": fmt.day(d.day), "hours": fmt.hours(d.hours)})


def _overtime_by_month(ledger: Ledger) -> dict[tuple[int, int], Fraction]:
    """延長工作時間 per calendar month, 休息日 hours included (§36 III). A month the record only
    partly covers holds a lower bound."""
    totals: dict[tuple[int, int], Fraction] = defaultdict(Fraction)
    for d in ledger.worked_days():
        totals[(d.day.year, d.day.month)] += ledger.overtime_hours(d)
    return totals


def check_monthly_overtime(ledger: Ledger) -> Iterator[Finding]:
    totals = _overtime_by_month(ledger)
    consent = ledger.employment.overtime_consent
    for (year, month), total in sorted(totals.items()):
        params = {"month": f"{year}-{month:02d}", "overtime": fmt.hours(total)}
        dates = (date(year, month, 1),)
        if total > MONTHLY_CAP_WITH_CONSENT:
            yield Finding("monthly_overtime_cap", Verdict.VIOLATION, "over_54", dates, params)
        elif total > MONTHLY_CAP:
            verdict, variant = {
                Answer.NO: (Verdict.VIOLATION, "over_46"),
                Answer.YES: (Verdict.NEEDS_HUMAN, "over_46_with_consent"),
                Answer.UNKNOWN: (Verdict.NEEDS_HUMAN, "over_46_consent_unknown"),
            }[consent]
            yield Finding("monthly_overtime_cap", verdict, variant, dates, params)


def check_quarterly_overtime(ledger: Ledger) -> Iterator[Finding]:
    """§32 II 但書: with 工會/勞資會議 consent, 54 h a month and 138 h every three months.
    施行細則 §22 I counts the three months from dates the parties agree on, which the engine
    cannot know, so every window of three calendar months is checked and an excess is only
    ever needs_human. Without consent the 46 h monthly cap already binds (46 × 3 = 138)."""
    consent = ledger.employment.overtime_consent
    if consent is Answer.NO:
        return
    totals = _overtime_by_month(ledger)
    months = ledger.record.period.months()
    for window in (months[i:i + 3] for i in range(len(months) - 2)):
        total = sum((totals.get(m, Fraction(0)) for m in window), Fraction(0))
        if total > QUARTERLY_CAP_WITH_CONSENT:
            (y0, m0), (y1, m1) = window[0], window[-1]
            yield Finding(
                "quarterly_overtime_cap", Verdict.NEEDS_HUMAN,
                "over_138" if consent is Answer.YES else "over_138_consent_unknown",
                (date(y0, m0, 1),),
                {"first": f"{y0}-{m0:02d}", "last": f"{y1}-{m1:02d}", "overtime": fmt.hours(total)},
            )


def check_shift_rest(ledger: Ledger) -> Iterator[Finding]:
    """The variant text depends on the answer: only "rotating" lets it say §34 applies."""
    rotating = ledger.employment.shift_system is ShiftSystem.ROTATING
    shifts = sorted(ledger.record.shifts, key=lambda s: s.start)
    for before, after in pairwise(shifts):
        if after.workday == before.workday or after.clock == before.clock:
            continue
        rest = after.start - before.end
        if rest < MIN_REST:
            variant = "under_8" if rest < MIN_REST_ANNOUNCED else "under_11"
            yield Finding(
                "shift_rest", Verdict.VIOLATION, variant if rotating else f"{variant}_if_rotating",
                (before.workday, after.workday),
                {"end": fmt.moment(before.end), "start": fmt.moment(after.start),
                 "rest": fmt.hours(Fraction(int(rest.total_seconds()) // 60, 60))},
            )


def check_rest_break(ledger: Ledger) -> Iterator[Finding]:
    """§35: 繼續工作四小時，至少應有三十分鐘之休息. A shift records only its total break, not
    when it fell. No break at all over more than 4 h is a violation. A break too short to
    give 30 minutes after every 4 hours is needs_human (it may have been split or timed in
    ways the record does not show). Anything longer is not judged."""
    no_break, short = [], []
    for shift in ledger.record.shifts:
        span = int((shift.end - shift.start).total_seconds()) // 60
        worked = span - shift.break_minutes
        if worked <= MAX_STRETCH_MINUTES:
            continue
        breaks_needed = -(-worked // MAX_STRETCH_MINUTES) - 1
        if shift.break_minutes == 0:
            no_break.append(shift)
        elif shift.break_minutes < breaks_needed * MIN_BREAK_MINUTES:
            short.append(shift)
    if no_break:
        yield Finding(
            "rest_break", Verdict.VIOLATION, "no_break", tuple(s.workday for s in no_break),
            {"count": len(no_break), "dates": fmt.days(s.workday for s in no_break),
             "longest": fmt.hours(max(s.hours for s in no_break))},
        )
    if short:
        yield Finding(
            "rest_break", Verdict.NEEDS_HUMAN, "short_break", tuple(s.workday for s in short),
            {"count": len(short), "dates": fmt.days(s.workday for s in short)},
        )


def cycle_params(ledger: Ledger) -> dict[str, object]:
    return {"weekday": Text(f"ui.weekday.{WEEKDAYS[ledger.week_start]}")}


def _cycle_variant(ledger: Ledger, variant: str) -> str:
    """Which day is a 休息日 or 例假 depends on the 7-day cycle; say so when it was not given."""
    return variant if ledger.week_start_known else f"{variant}_assumed"


def check_regular_leave(ledger: Ledger) -> Iterator[Finding]:
    runs: list[list[date]] = [[]]
    for d in ledger.days:
        if d.worked:
            runs[-1].append(d.day)
        elif runs[-1]:
            runs.append([])
    for run in runs:
        if len(run) > MAX_CONSECUTIVE_DAYS:
            yield Finding("regular_leave", Verdict.VIOLATION, "over_6", tuple(run),
                          {"first": fmt.day(run[0]), "last": fmt.day(run[-1]), "days": len(run)})
    for d in ledger.worked_days():
        if d.kind is DayKind.REGULAR_LEAVE:
            yield Finding("regular_leave", Verdict.NEEDS_HUMAN, _cycle_variant(ledger, "worked_pay"), (d.day,),
                          {"date": fmt.day(d.day), "hours": fmt.hours(d.hours)} | cycle_params(ledger),
                          (_holiday_extra(ledger, d),))


def check_rest_day_pay(ledger: Ledger) -> Iterator[Finding]:
    """§24 II premiums. 月薪 already pays one day (8 h) of each 休息日, so a monthly-paid
    worker is also owed each hour past 8 itself; hourly base pay already counts every hour."""
    monthly = ledger.employment.pay_basis is PayBasis.MONTHLY
    for d in ledger.worked_days():
        if d.kind is not DayKind.REST_DAY:
            continue
        rate = ledger.hourly_rate(d)
        calcs = [
            Calc(f"calc.rest_day_pay.tier{tier + 1}",
                 {"date": fmt.day(d.day), "hours": fmt.hours(hours), "rate": fmt.money(rate),
                  "factor": fmt.factor(premium)},
                 NTD(hours * rate * premium))
            for tier, hours, premium in _split(d.hours, REST_DAY_TIERS)
        ]
        beyond = d.hours - NORMAL_DAY_HOURS
        if monthly and beyond > 0:
            calcs.append(Calc("calc.rest_day_pay.beyond_8_base",
                              {"date": fmt.day(d.day), "hours": fmt.hours(beyond), "rate": fmt.money(rate)},
                              NTD(beyond * rate)))
        yield Finding("rest_day_pay", Verdict.VIOLATION, _cycle_variant(ledger, "inferred"), (d.day,),
                      {"date": fmt.day(d.day), "hours": fmt.hours(d.hours)} | cycle_params(ledger), tuple(calcs))


def _holiday_extra(ledger: Ledger, day) -> Calc:
    """§39 加倍發給: one more day's wage for monthly pay; per hour, up to 8, for hourly pay."""
    rate = ledger.hourly_rate(day)
    if ledger.employment.pay_basis is PayBasis.MONTHLY:
        return Calc("calc.holiday_pay.day", {"date": fmt.day(day.day), "daily": fmt.money(ledger.daily_wage(day))},
                    ledger.daily_wage(day))
    within = min(day.hours, NORMAL_DAY_HOURS)
    return Calc("calc.holiday_pay.double", {"date": fmt.day(day.day), "hours": fmt.hours(within), "rate": fmt.money(rate)},
                NTD(within * rate))


def check_holiday_pay(ledger: Ledger) -> Iterator[Finding]:
    period = ledger.record.period
    if period.start < holidays.COVERED.start or period.end > holidays.COVERED.end:
        yield Finding("holiday_pay", Verdict.NEEDS_HUMAN, "out_of_range", (),
                      {"start": fmt.day(holidays.COVERED.start), "end": fmt.day(holidays.COVERED.end)})
    straddled = {
        day for shift in ledger.record.shifts if shift.crosses_midnight
        for day in (shift.workday, shift.end.date()) if holidays.on(day)
    }
    for d in ledger.worked_days():
        if d.day in straddled:
            continue
        params = {"date": fmt.day(d.day), "hours": fmt.hours(d.hours)}
        if d.kind is DayKind.HOLIDAY:
            yield Finding("holiday_pay", Verdict.VIOLATION, "worked", (d.day,),
                          params | {"holiday": d.holiday.name}, (_holiday_extra(ledger, d),))
        elif d.kind is DayKind.NORMAL and d.holiday is not None and d.holiday.voters_only:
            within, rate = min(d.hours, NORMAL_DAY_HOURS), ledger.hourly_rate(d)
            yield Finding("holiday_pay", Verdict.NEEDS_HUMAN, "voting_day", (d.day,),
                          params | {"holiday": d.holiday.name},
                          (Calc("calc.holiday_pay.voting_day",
                                {"date": fmt.day(d.day), "hours": fmt.hours(within), "rate": fmt.money(rate)},
                                NTD(within * rate)),))
    for day in sorted(straddled):
        yield Finding("holiday_pay", Verdict.NEEDS_HUMAN, "crosses_midnight", (day,),
                      {"date": fmt.day(day), "holiday": holidays.on(day).name})
    yield from _holidays_off(ledger, straddled)


def _holidays_off(ledger: Ledger, straddled: set[date]) -> Iterator[Finding]:
    """§39 照給 for a 休假 not worked. A monthly wage already includes it; an hourly wage does
    not (勞動部「時薪制勞工的權益」問2). It is owed only if the day was one the worker was
    originally due to work, which a record of shifts worked cannot show: needs_human, no amount."""
    if ledger.employment.pay_basis is not PayBasis.HOURLY:
        return
    off = [d for d in ledger.days if d.kind is DayKind.OFF and d.holiday is not None and d.day not in straddled]
    regular = [d for d in off if not d.holiday.voters_only]
    if regular:
        yield Finding("holiday_pay", Verdict.NEEDS_HUMAN, "off_day", tuple(d.day for d in regular),
                      {"dates": fmt.days(d.day for d in regular),
                       "holidays": "、".join(d.holiday.name for d in regular)})
    for d in off:
        if d.holiday.voters_only:
            yield Finding("holiday_pay", Verdict.NEEDS_HUMAN, "off_voting_day", (d.day,),
                          {"date": fmt.day(d.day), "holiday": d.holiday.name})


_HOURS_GATES = frozenset({Gate.RESPONSIBILITY_SYSTEM, Gate.SPECIAL_HOURS})

RULES: tuple[Rule, ...] = (
    Rule("minimum_wage", (law.MWA_5, law.LSA_21, law.MWA_18), MINIMUM_WAGE_ACT, frozenset(),
         ("payslip", "contract", "bank_transfer"), True, check_minimum_wage),
    Rule("regular_hours", (law.LSA_30_1, law.RULES_17, law.RULES_20_1), AMENDED_2018, _HOURS_GATES,
         ("clock_records", "schedule"), False, check_regular_hours),
    Rule("overtime_pay", (law.LSA_24_1, law.RULES_20_1), AMENDED_2018, _HOURS_GATES,
         ("clock_records", "payslip"), True, check_overtime_pay),
    Rule("daily_hours_cap", (law.LSA_32_2,), AMENDED_2018, frozenset({Gate.RESPONSIBILITY_SYSTEM}),
         ("clock_records",), False, check_daily_cap),
    Rule("monthly_overtime_cap", (law.LSA_32_2, law.LSA_36_3, law.RULES_20_1), AMENDED_2018, _HOURS_GATES,
         ("clock_records",), False, check_monthly_overtime),
    Rule("quarterly_overtime_cap", (law.LSA_32_2, law.RULES_22, law.LSA_36_3), AMENDED_2018, _HOURS_GATES,
         ("clock_records",), False, check_quarterly_overtime),
    Rule("shift_rest", (law.LSA_34_2,), AMENDED_2018, frozenset({Gate.SHIFT_SYSTEM}),
         ("schedule", "clock_records"), False, check_shift_rest),
    # §84-1 lifts §30, §32, §36, §37 and §49, not §35, so 責任制 does not gate the break.
    Rule("rest_break", (law.LSA_35,), LSA_IN_FORCE, frozenset({Gate.BREAK_EXCEPTION}),
         ("clock_records", "schedule"), False, check_rest_break),
    Rule("regular_leave", (law.LSA_36_1, law.RULES_22_3, law.LSA_40), AMENDED_2018, _HOURS_GATES,
         ("schedule", "clock_records"), True, check_regular_leave),
    Rule("rest_day_pay", (law.LSA_24_2, law.LSA_36_1), AMENDED_2018, _HOURS_GATES,
         ("schedule", "clock_records", "payslip"), True, check_rest_day_pay),
    Rule("holiday_pay", (law.LSA_37_1, law.LSA_39, law.HOLIDAYS_4, law.HOLIDAYS_6), HOLIDAY_ACT,
         frozenset({Gate.RESPONSIBILITY_SYSTEM}), ("schedule", "clock_records", "payslip"), True,
         check_holiday_pay),
)
