"""One behavior test (or a small group) per rule. Expected amounts are worked out by
hand from the article text, never read back from the engine."""

from datetime import date
from fractions import Fraction

from conftest import found, make, run

from labor_radar.rules.core import Verdict

MON, TUE, WED, THU, FRI, SAT, SUN = (f"2026-10-{d:02d}" for d in range(5, 12))
NEXT_WEEK = tuple(f"2026-10-{d:02d}" for d in range(12, 19))
OCTOBER_WEEKDAYS = tuple(
    f"2026-10-{d:02d}" for d in (1, 2, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30)
)


# 最低工資法第5條、勞動基準法第21條第1項
def test_hourly_wage_below_minimum_owes_the_gap_for_every_hour():
    a = run(make([(MON, "09:00", "13:00"), (TUE, "09:00", "13:00")], MON, SUN, wage=180))
    [f] = found(a, "minimum_wage", Verdict.VIOLATION)
    assert f.amount == 8 * (196 - 180)


def test_hourly_wage_at_minimum_is_lawful():
    a = run(make([(MON, "09:00", "13:00")], MON, SUN, wage=196))
    assert found(a, "minimum_wage") == []


def test_announced_2027_minimum_applies_from_new_year():
    shifts = [("2026-12-28", "09:00", "13:00"), ("2027-01-04", "09:00", "13:00")]
    a = run(make(shifts, "2026-12-28", "2027-01-10", wage=200))
    [f] = found(a, "minimum_wage", Verdict.VIOLATION)
    assert [d.isoformat() for d in f.dates] == ["2027-01-04"]
    assert f.amount == 4 * (205 - 200)


def test_dates_past_the_wage_table_need_a_human():
    a = run(make([("2028-01-03", "09:00", "13:00")], "2028-01-03", "2028-01-09", wage=200))
    assert [(f.verdict, f.variant) for f in found(a, "minimum_wage")] == [(Verdict.NEEDS_HUMAN, "out_of_range")]


def test_monthly_months_past_the_wage_table_need_a_human():
    a = run(make([], "2028-01-01", "2028-01-31", pay_basis="monthly", agreed_wage=20000))
    assert [(f.verdict, f.variant) for f in found(a, "minimum_wage")] == [(Verdict.NEEDS_HUMAN, "out_of_range")]


def test_2024_basic_wage_and_2025_minimum_wage_are_dated():
    shifts = [("2024-12-30", "09:00", "13:00"), ("2025-01-02", "09:00", "13:00")]
    a = run(make(shifts, "2024-12-30", "2025-01-05", wage=185))
    [f] = found(a, "minimum_wage", Verdict.VIOLATION)
    assert f.params["name"] == "最低工資" and f.amount == 4 * (190 - 185)


def test_pending_proposal_is_shown_but_never_counted(monkeypatch):
    from labor_radar.rules import minimum_wage as mw
    proposal = mw._row(date(2026, 11, 1), "最低工資", 31000, 210, mw.Status.PENDING_APPROVAL, "test")
    monkeypatch.setattr(mw, "TABLE", (*mw.TABLE, proposal))
    a = run(make([("2026-11-02", "09:00", "13:00")], "2026-11-02", "2026-11-08", wage=200))
    assert found(a, "minimum_wage", Verdict.VIOLATION) == []
    [note] = found(a, "minimum_wage", Verdict.NOTE)
    assert (note.variant, note.params["extra"]) == ("pending", "40")
    assert a.claim.shortfall == 0


def test_monthly_wage_below_minimum_owes_the_monthly_gap():
    full_time = [(d, "09:00", "18:00", 60) for d in OCTOBER_WEEKDAYS]
    data = make(full_time, "2026-10-01", "2026-10-31", pay_basis="monthly", agreed_wage=28000)
    [f] = found(run(data), "minimum_wage", Verdict.VIOLATION)
    assert f.amount == 29500 - 28000


# 僱用部分時間工作勞工應行注意事項 陸、二(一)；勞動部常見問答：每月正常工時 174 小時
PART_TIME_OCTOBER = [(d, "09:00", "13:00") for d in OCTOBER_WEEKDAYS]


def test_monthly_part_timer_is_not_held_to_the_full_time_floor():
    a = run(make(PART_TIME_OCTOBER, "2026-10-01", "2026-10-31", pay_basis="monthly", agreed_wage=20000))
    assert found(a, "minimum_wage", Verdict.VIOLATION) == []
    [f] = found(a, "minimum_wage", Verdict.NEEDS_HUMAN)
    assert (f.variant, f.params["hours"], f.params["floor"]) == ("part_time_hours_unknown", "88", "14,919.54")
    assert a.claim.shortfall == 0


def test_monthly_part_timer_below_the_prorated_floor_needs_a_human_with_the_gap():
    a = run(make(PART_TIME_OCTOBER, "2026-10-01", "2026-10-31", pay_basis="monthly", agreed_wage=12000,
                 agreed_monthly_hours=88))
    assert found(a, "minimum_wage", Verdict.VIOLATION) == []
    [f] = found(a, "minimum_wage", Verdict.NEEDS_HUMAN)
    assert f.variant == "part_time" and f.amount == 29500 * Fraction(88, 174) - 12000
    assert a.claim.possible_extra == f.amount


def test_monthly_part_timer_at_the_prorated_floor_is_lawful():
    a = run(make(PART_TIME_OCTOBER, "2026-10-01", "2026-10-31", pay_basis="monthly", agreed_wage=20000,
                 agreed_monthly_hours=88))
    assert found(a, "minimum_wage") == []


def test_monthly_part_timer_premiums_use_the_wage_over_agreed_hours():
    shifts = [("2026-10-05", "08:00", "19:00", 60), ("2026-10-10", "09:00", "13:00")] + PART_TIME_OCTOBER[3:]
    a = run(make(shifts, "2026-10-01", "2026-10-31", pay_basis="monthly", agreed_wage=20000,
                 agreed_monthly_hours=88))
    [overtime] = found(a, "overtime_pay", Verdict.VIOLATION)
    [holiday] = found(a, "holiday_pay", Verdict.VIOLATION)
    assert overtime.amount == 2 * Fraction(20000, 88) * Fraction(4, 3)
    assert holiday.amount == Fraction(20000, 30)


def test_monthly_wage_needs_whole_months():
    data = make([], "2026-10-05", "2026-10-31", pay_basis="monthly", agreed_wage=28000)
    [f] = found(run(data), "minimum_wage")
    assert f.verdict is Verdict.NEEDS_HUMAN and f.variant == "partial_month"


# 勞動基準法第30條第1項、施行細則第20-1條
def test_hours_past_eight_on_a_normal_day_are_overtime():
    a = run(make([(MON, "08:00", "19:00", 60)], MON, SUN))
    [note] = found(a, "regular_hours")
    assert note.verdict is Verdict.NOTE
    assert note.params["overtime"] == "2"


# 勞動基準法第24條第1項
def test_overtime_crossing_the_two_hour_tier():
    a = run(make([(MON, "08:00", "20:00", 60)], MON, SUN))
    [f] = found(a, "overtime_pay", Verdict.VIOLATION)
    assert f.amount == 2 * 200 * Fraction(1, 3) + 1 * 200 * Fraction(2, 3)


def test_overtime_of_exactly_two_hours_stays_in_the_first_tier():
    a = run(make([(MON, "08:00", "19:00", 60)], MON, SUN))
    [f] = found(a, "overtime_pay", Verdict.VIOLATION)
    assert [c.template for c in f.calcs] == ["calc.overtime_pay.tier1"]
    assert f.amount == 2 * 200 * Fraction(1, 3)


def test_overtime_premium_uses_the_minimum_wage_when_agreed_rate_is_lower():
    a = run(make([(MON, "08:00", "18:00", 60)], MON, SUN, wage=180))
    [f] = found(a, "overtime_pay", Verdict.VIOLATION)
    assert f.amount == 1 * 196 * Fraction(1, 3)


def test_monthly_paid_overtime_includes_the_hour_itself():
    data = make([("2026-10-05", "08:00", "19:00", 60)], "2026-10-01", "2026-10-31",
                pay_basis="monthly", agreed_wage=36000)
    [f] = found(run(data), "overtime_pay", Verdict.VIOLATION)
    assert f.amount == 2 * Fraction(36000, 240) * Fraction(4, 3)


# 勞動基準法第32條第2項（每日12小時）
def test_more_than_twelve_hours_in_a_day_is_a_violation():
    a = run(make([(MON, "07:00", "21:00", 60)], MON, SUN))
    [f] = found(a, "daily_hours_cap")
    assert f.verdict is Verdict.VIOLATION and f.params["hours"] == "13"


def test_exactly_twelve_hours_is_within_the_daily_cap():
    a = run(make([(MON, "07:00", "20:00", 60)], MON, SUN))
    assert found(a, "daily_hours_cap") == []


# 勞動基準法第32條第2項（每月46/54小時）、第36條第3項
def _october(extra_on_last_day: str, consent: str):
    weekdays = [f"2026-10-{d:02d}" for d in (1, 2, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30)]
    shifts = [(d, "08:00", "19:00", 60) for d in weekdays[:-2]]
    shifts += [(weekdays[-2], "08:00", "20:00", 60), (weekdays[-1], "08:00", extra_on_last_day, 60)]
    return run(make(shifts, "2026-10-01", "2026-10-31", overtime_consent=consent))


def test_exactly_46_overtime_hours_in_a_month_is_lawful():
    assert found(_october("20:00", "no"), "monthly_overtime_cap") == []


def test_over_46_without_consent_is_a_violation():
    [f] = found(_october("20:30", "no"), "monthly_overtime_cap")
    assert f.verdict is Verdict.VIOLATION and f.params["overtime"] == "46.5"


def test_over_46_with_unknown_consent_needs_a_human():
    [f] = found(_october("20:30", "unknown"), "monthly_overtime_cap")
    assert f.verdict is Verdict.NEEDS_HUMAN


def test_rest_day_hours_count_toward_the_monthly_cap():
    weekdays = [f"2026-10-{d:02d}" for d in (1, 2, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30)]
    shifts = [(d, "08:00", "19:00", 60) for d in weekdays] + [("2026-10-17", "08:00", "11:00")]
    a = run(make(shifts, "2026-10-01", "2026-10-31"))
    [f] = found(a, "monthly_overtime_cap")
    assert f.params["overtime"] == "47"


# 勞動基準法第34條第2項
def test_exactly_eleven_hours_between_shifts_is_lawful():
    a = run(make([(MON, "13:00", "22:00"), (TUE, "09:00", "17:00")], MON, SUN))
    assert found(a, "shift_rest") == []


def test_ten_and_a_half_hours_between_shifts_is_a_violation():
    a = run(make([(MON, "13:00", "22:00"), (TUE, "08:30", "17:00")], MON, SUN))
    [f] = found(a, "shift_rest")
    assert (f.verdict, f.variant, f.params["rest"]) == (Verdict.VIOLATION, "under_11", "10.5")


def test_shift_crossing_midnight_counts_rest_from_the_next_morning():
    a = run(make([(MON, "18:00", "02:00"), (TUE, "10:00", "18:00")], MON, SUN))
    [f] = found(a, "shift_rest")
    assert f.params["end"] == "2026-10-06 02:00"
    assert f.params["rest"] == "8"


def test_less_than_eight_hours_rest_has_no_exception():
    a = run(make([(MON, "16:00", "00:00"), (TUE, "07:00", "12:00")], MON, SUN))
    [f] = found(a, "shift_rest")
    assert f.variant == "under_8"


def test_split_shift_on_one_day_is_not_a_shift_change():
    a = run(make([(MON, "10:00", "14:00"), (MON, "17:00", "21:00")], MON, SUN))
    assert found(a, "shift_rest") == []


def test_unknown_shift_system_turns_short_rest_into_needs_human():
    a = run(make([(MON, "13:00", "22:00"), (TUE, "08:00", "17:00")], MON, SUN, shift_system="unknown"))
    [f] = found(a, "shift_rest")
    assert f.verdict is Verdict.NEEDS_HUMAN and [g.value for g in f.uncertain_gates] == ["shift_system"]


def test_fixed_schedule_skips_the_shift_change_rule():
    a = run(make([(MON, "13:00", "22:00"), (TUE, "08:00", "17:00")], MON, SUN, shift_system="fixed"))
    assert found(a, "shift_rest") == []


# 勞動基準法第36條第1項、施行細則第22-3條
def test_seven_consecutive_work_days_violate_the_regular_leave():
    a = run(make([(d, "09:00", "13:00") for d in (MON, TUE, WED, THU, FRI, SAT, SUN)], MON, SUN))
    [f] = found(a, "regular_leave", Verdict.VIOLATION)
    assert f.verdict is Verdict.VIOLATION and f.params["days"] == 7


def test_six_consecutive_work_days_are_allowed():
    a = run(make([(d, "09:00", "13:00") for d in (MON, TUE, WED, THU, FRI, SAT)], MON, SUN))
    assert found(a, "regular_leave") == []


def test_consecutive_days_span_two_calendar_weeks():
    days = [f"2026-10-{d:02d}" for d in range(8, 15)]
    a = run(make([(d, "09:00", "13:00") for d in days], "2026-10-05", "2026-10-18"))
    [f] = found(a, "regular_leave")
    assert f.params["first"] == "2026-10-08" and f.params["last"] == "2026-10-14"


# 勞動基準法第24條第2項、第36條第1項
def test_sixth_work_day_in_a_week_is_paid_as_rest_day_using_the_shortest_day():
    *weekdays, sat, sun = NEXT_WEEK
    shifts = [(d, "09:00", "18:00", 60) for d in weekdays] + [(sat, "10:00", "15:00")]
    a = run(make(shifts, NEXT_WEEK[0], sun))
    [f] = found(a, "rest_day_pay", Verdict.VIOLATION)
    assert f.dates[0].isoformat() == sat
    assert f.amount == 2 * 200 * Fraction(4, 3) + 3 * 200 * Fraction(5, 3)


def test_holiday_worked_is_not_picked_as_the_rest_day():
    shifts = [(d, "09:00", "18:00", 60) for d in (MON, TUE, WED, THU, FRI)] + [(SAT, "10:00", "15:00")]
    a = run(make(shifts, MON, SUN))
    [rest] = found(a, "rest_day_pay", Verdict.VIOLATION)
    [holiday] = found(a, "holiday_pay", Verdict.VIOLATION)
    assert (rest.dates[0].isoformat(), holiday.dates[0].isoformat()) == (MON, SAT)


def test_two_days_off_in_the_week_means_no_rest_day_work():
    shifts = [(d, "09:00", "18:00", 60) for d in (MON, TUE, WED, THU, FRI)]
    assert found(run(make(shifts, MON, SUN)), "rest_day_pay") == []


def test_partial_week_at_the_edge_of_the_record_is_not_guessed():
    shifts = [(d, "09:00", "18:00", 60) for d in (WED, THU, FRI, SAT, SUN)]
    assert found(run(make(shifts, WED, SUN)), "rest_day_pay") == []


# 勞動基準法第37條第1項、第39條、紀念日及節日實施條例第4條
def test_national_holiday_worked_is_paid_double():
    a = run(make([("2026-09-28", "10:00", "16:00")], "2026-09-28", "2026-10-04"))
    [f] = found(a, "holiday_pay", Verdict.VIOLATION)
    assert f.params["holiday"] == "孔子誕辰紀念日／教師節"
    assert f.amount == 6 * 200


# 勞動部107年11月8日勞動條3字第1070131460號公告；中選會115年8月20日中選務字第1153150253號公告
def test_voting_day_worked_needs_a_human_with_double_pay_as_possible():
    a = run(make([("2026-11-28", "10:00", "16:00")], "2026-11-23", "2026-11-29"))
    assert [(f.verdict, f.variant, f.amount) for f in found(a, "holiday_pay")] == [
        (Verdict.NEEDS_HUMAN, "voting_day", 6 * 200)]
    assert a.claim.shortfall == 0


def test_ordinary_day_is_not_a_holiday():
    a = run(make([(NEXT_WEEK[0], "10:00", "16:00")], NEXT_WEEK[0], NEXT_WEEK[-1]))
    assert found(a, "holiday_pay") == []


def test_shift_crossing_midnight_into_a_holiday_needs_a_human():
    a = run(make([(FRI, "22:00", "06:00")], MON, SUN))
    [f] = found(a, "holiday_pay")
    assert f.verdict is Verdict.NEEDS_HUMAN and f.variant == "crosses_midnight"


# 紀念日及節日實施條例第10條「自公布日施行」，114年5月28日公布；中央法規標準法第13條：起算至第三日
def test_holiday_act_is_in_effect_from_the_third_day_after_promulgation():
    def variants(day):
        return [f.variant for f in found(run(make([(day, "10:00", "16:00")], day, day)), "holiday_pay")]
    assert "outside_effective" in variants("2025-05-29")
    assert "outside_effective" not in variants("2025-05-30")


def test_dates_beyond_the_holiday_table_need_a_human():
    a = run(make([("2027-02-01", "10:00", "16:00")], "2027-02-01", "2027-02-07"))
    [f] = found(a, "holiday_pay")
    assert f.variant == "out_of_range"


def test_monthly_paid_holiday_work_earns_one_more_days_wage_even_under_eight_hours():
    data = make([("2026-10-10", "10:00", "13:00")], "2026-10-01", "2026-10-31",
                pay_basis="monthly", agreed_wage=36000)
    [f] = found(run(data), "holiday_pay", Verdict.VIOLATION)
    assert f.amount == 36000 / 30


def test_holiday_hours_past_eight_add_overtime_on_top_of_double_pay():
    a = run(make([("2026-09-28", "08:00", "19:00", 60)], "2026-09-28", "2026-10-04"))
    [holiday] = found(a, "holiday_pay", Verdict.VIOLATION)
    [overtime] = found(a, "overtime_pay", Verdict.VIOLATION)
    assert holiday.amount == 8 * 200
    assert overtime.amount == 2 * 200 * Fraction(1, 3)


# 勞動部「加班補休規定」：第9至12小時另再加給2又2/3＝該小時工資1＋第24條第2項的1又2/3
def test_hourly_rest_day_past_eight_hours_owes_the_ministry_table_and_nothing_more():
    *weekdays, sat, sun = NEXT_WEEK
    shifts = [(d, "09:00", "20:00", 60) for d in weekdays] + [(sat, "08:00", "18:00")]
    a = run(make(shifts, NEXT_WEEK[0], sun))
    assert found(a, "rest_day_pay", Verdict.NEEDS_HUMAN) == []
    [rest] = found(a, "rest_day_pay", Verdict.VIOLATION)
    assert 10 * 200 + rest.amount == 2 * 200 * Fraction(7, 3) + 8 * 200 * Fraction(8, 3)
    assert a.claim.possible_extra == 0


def test_monthly_rest_day_hours_past_eight_add_the_hours_own_wage():
    shifts = [(d, "07:00", "20:00", 60) for d in NEXT_WEEK[:6]]
    a = run(make(shifts, "2026-10-01", "2026-10-31", pay_basis="monthly", agreed_wage=36000))
    [rest] = found(a, "rest_day_pay", Verdict.VIOLATION)
    assert rest.amount == 150 * Fraction(4, 3) * 2 + 150 * Fraction(5, 3) * 6 + 150 * Fraction(8, 3) * 4
    assert a.claim.possible_extra == 0


def test_worked_regular_leave_pay_needs_a_human():
    shifts = [(d, "09:00", "13:00") for d in NEXT_WEEK]
    a = run(make(shifts, NEXT_WEEK[0], NEXT_WEEK[-1]))
    [pay] = found(a, "regular_leave", Verdict.NEEDS_HUMAN)
    assert pay.variant == "worked_pay" and pay.amount == 4 * 200
    assert a.claim.possible_extra == 4 * 200


# 勞動基準法第35條：繼續工作四小時，至少應有三十分鐘之休息
def test_more_than_four_hours_without_a_break_is_a_violation():
    a = run(make([(MON, "17:00", "22:00"), (WED, "17:00", "23:00")], MON, SUN, shift_system="flexible"))
    [f] = found(a, "rest_break")
    assert (f.verdict, f.variant, f.params["count"], f.params["longest"]) == (Verdict.VIOLATION, "no_break", 2, "6")
    assert a.claim.shortfall == 0


def test_exactly_four_hours_needs_no_break():
    assert found(run(make([(MON, "09:00", "13:00")], MON, SUN, shift_system="fixed")), "rest_break") == []


def test_a_half_hour_break_in_an_eight_hour_day_is_not_second_guessed():
    a = run(make([(MON, "09:00", "17:30", 30)], MON, SUN, shift_system="fixed"))
    assert found(a, "rest_break") == []


def test_one_half_hour_break_in_nine_hours_of_work_needs_a_human():
    a = run(make([(MON, "08:00", "17:30", 30)], MON, SUN, shift_system="fixed"))
    [f] = found(a, "rest_break")
    assert (f.verdict, f.variant) == (Verdict.NEEDS_HUMAN, "short_break")


def test_rotating_shifts_may_arrange_the_break_inside_working_time():
    a = run(make([(MON, "17:00", "22:00")], MON, SUN, shift_system="rotating"))
    [f] = found(a, "rest_break")
    assert f.verdict is Verdict.NEEDS_HUMAN and [g.value for g in f.uncertain_gates] == ["break_exception"]


def test_responsibility_system_does_not_lift_the_break_rule():
    # 第84-1條只排除第30、32、36、37、49條
    a = run(make([(MON, "17:00", "22:00")], MON, SUN, shift_system="fixed", responsibility_system="unknown"))
    [f] = found(a, "rest_break")
    assert f.verdict is Verdict.VIOLATION


# 勞動基準法第34條第2項：彈性排班是否為輪班制有爭議
def test_flexible_roster_short_rest_between_shifts_needs_a_human():
    from labor_radar import report
    a = run(make([(MON, "17:00", "23:00"), (TUE, "08:00", "13:00")], MON, SUN, shift_system="flexible"))
    [f] = found(a, "shift_rest")
    assert (f.verdict, f.variant) == (Verdict.NEEDS_HUMAN, "under_11_if_rotating")
    assert [g.value for g in f.uncertain_gates] == ["shift_system"]
    [item] = [i for i in report.render(a)["needs_human"] if i["rule_id"] == "shift_rest"]
    assert "彈性排班" in item["why_human"][0]


# 勞動基準法第32條第2項但書、施行細則第22條第1項（每三個月138小時）
def _quarter(overtime_by_month, consent):
    """Mon/Wed/Fri days of Jul–Sep 2026, 12 h days (4 h overtime) until each month's total is met."""
    shifts = []
    for month, overtime in zip((7, 8, 9), overtime_by_month, strict=True):
        days = [date(2026, month, d) for d in range(1, 29) if date(2026, month, d).weekday() in (0, 2, 4)]
        for day in days:
            extra = min(Fraction(4), overtime)
            if extra <= 0:
                break
            end = f"{9 + 8 + int(extra):02d}:{int(extra % 1 * 60):02d}"
            shifts.append((day.isoformat(), "08:00", end, 60))
            overtime -= extra
    return run(make(shifts, "2026-07-01", "2026-09-30", overtime_consent=consent))


def test_over_138_in_three_months_with_consent_needs_a_human():
    a = _quarter((48, 48, 44), "yes")
    [f] = found(a, "quarterly_overtime_cap")
    assert (f.verdict, f.variant, f.params["overtime"]) == (Verdict.NEEDS_HUMAN, "over_138", "140")
    assert [m.variant for m in found(a, "monthly_overtime_cap")] == ["over_46_with_consent"] * 2


def test_exactly_138_in_three_months_is_within_the_cap():
    assert found(_quarter((48, 48, 42), "yes"), "quarterly_overtime_cap") == []


def test_over_138_with_unknown_consent_needs_a_human():
    [f] = found(_quarter((48, 48, 44), "unknown"), "quarterly_overtime_cap")
    assert f.variant == "over_138_consent_unknown"


def test_without_consent_the_46_hour_monthly_cap_already_binds():
    a = _quarter((48, 48, 44), "no")
    assert found(a, "quarterly_overtime_cap") == []
    assert [m.verdict for m in found(a, "monthly_overtime_cap")] == [Verdict.VIOLATION] * 2


# 勞動基準法第39條；勞動部「時薪制勞工的權益」問2；勞委會96年11月1日新聞稿
def test_hourly_worker_off_on_a_national_holiday_is_told_it_may_be_owed():
    a = run(make([(MON, "10:00", "14:00")], MON, SUN))
    [f] = found(a, "holiday_pay")
    assert (f.verdict, f.variant, f.params["holidays"]) == (Verdict.NEEDS_HUMAN, "off_day", "國慶日")
    assert f.amount == 0 and a.claim.shortfall == 0


def test_monthly_wage_already_pays_the_holiday_off():
    data = make([("2026-10-05", "09:00", "13:00")], "2026-10-01", "2026-10-31",
                pay_basis="monthly", agreed_wage=36000)
    assert [f for f in found(run(data), "holiday_pay") if f.variant.startswith("off")] == []


def test_voting_day_off_asks_about_the_vote_and_the_schedule():
    a = run(make([("2026-11-23", "10:00", "14:00")], "2026-11-23", "2026-11-29"))
    assert [(f.verdict, f.variant) for f in found(a, "holiday_pay")] == [(Verdict.NEEDS_HUMAN, "off_voting_day")]


# 施行細則第22-3條：7日週期依曆計算，起訖由勞雇約定
def test_unknown_cycle_start_that_finds_no_rest_day_work_somewhere_says_so():
    shifts = [(f"2026-10-{d:02d}", "09:00", "13:00") for d in range(5, 11)]
    known = run(make(shifts, "2026-10-05", "2026-10-18", week_starts_on="monday"))
    unknown = run(make(shifts, "2026-10-05", "2026-10-18", week_starts_on="unknown"))
    assert len(found(known, "rest_day_pay", Verdict.VIOLATION)) == 1
    assert found(unknown, "rest_day_pay", Verdict.VIOLATION) == []
    assert [f.variant for f in found(unknown, "rest_day_pay", Verdict.NOTE)] == ["week_start_varies"]


def test_unknown_cycle_start_owes_the_least_of_the_seven_starts():
    from labor_radar import report
    shifts = [(f"2026-10-{d:02d}", "09:00", "13:00") for d in range(5, 19)]
    known = run(make(shifts, "2026-10-05", "2026-10-18", week_starts_on="monday"))
    unknown = run(make(shifts, "2026-10-05", "2026-10-18", week_starts_on="unknown"))
    assert len(found(known, "rest_day_pay", Verdict.VIOLATION)) == 2
    [f] = found(unknown, "rest_day_pay", Verdict.VIOLATION)
    assert f.variant == "inferred_assumed" and f.amount == 2 * 200 * Fraction(4, 3) + 2 * 200 * Fraction(5, 3)
    # 星期一、二起算各有 2 個休息日出勤；星期三是第一個只有 1 個的起算日
    [item] = [i for i in report.render(unknown)["violations"] if i["rule_id"] == "rest_day_pay"]
    assert "星期三" in item["explanation"]


def test_a_form_without_the_cycle_start_is_treated_as_unknown():
    data = make([(MON, "09:00", "13:00")], MON, SUN)
    del data["employment"]["week_starts_on"]
    assert run(data).ledger.week_start_known is False


# 勞動基準法第40條與勞委會102年1月9日勞動2字第1010090930號函
def test_worked_regular_leave_explains_article_40_apart_from_the_ruling():
    from labor_radar import report
    a = run(make([(d, "09:00", "13:00") for d in NEXT_WEEK], NEXT_WEEK[0], NEXT_WEEK[-1]))
    [item] = [i for i in report.render(a)["needs_human"] if i["rule_id"] == "regular_leave"]
    assert "第 40 條" in item["explanation"] and "函釋" in item["explanation"]
    assert "勞動基準法第40條" in [c["label"] for c in item["citations"]]


# 勞動部加班費試算系統（https://calcr2.mol.gov.tw/Index）頁面例題：月薪30,000元，平日加班3小時、
# 休息日加班6小時、國定假日出勤8小時，加給工資 2,708.33 元
def test_premiums_match_the_ministry_calculator_worked_example():
    week = [("2026-10-05", "08:00", "20:00", 60), ("2026-10-06", "09:00", "18:00", 60),
            ("2026-10-07", "09:00", "18:00", 60), ("2026-10-08", "09:00", "18:00", 60),
            ("2026-10-09", "09:00", "15:00"), ("2026-10-10", "09:00", "18:00", 60)]
    a = run(make(week, "2026-10-01", "2026-10-31", pay_basis="monthly", agreed_wage=30000))
    premiums = [f for rule in ("overtime_pay", "rest_day_pay", "holiday_pay") for f in found(a, rule, Verdict.VIOLATION)]
    assert [f.rule_id for f in premiums] == ["overtime_pay", "rest_day_pay", "holiday_pay"]
    total = sum((f.amount for f in premiums), Fraction(0))
    assert total == Fraction(30000, 240) * (2 * Fraction(4, 3) + Fraction(5, 3)) \
        + Fraction(30000, 240) * (2 * Fraction(4, 3) + 4 * Fraction(5, 3)) + Fraction(30000, 30)
    assert round(total, 2) == Fraction(270833, 100)
