from collections.abc import Iterable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction


def round_ntd(amount: Fraction, places: int = 0) -> Decimal:
    exact = Decimal(amount.numerator) / Decimal(amount.denominator)
    return exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def money(amount: Fraction) -> str:
    value = round_ntd(amount, 2)
    text = f"{value:,.2f}"
    return text[:-3] if text.endswith(".00") else text


def hours(value: Fraction) -> str:
    text = f"{round_ntd(value, 2):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def factor(value: Fraction) -> str:
    whole, part = divmod(value, 1)
    if part == 0:
        return str(whole)
    frac = f"{part.numerator}/{part.denominator}"
    return frac if whole == 0 else f"{whole} {frac}"


def day(value: date) -> str:
    return value.isoformat()


def days(values: Iterable[date]) -> str:
    return "、".join(day(v) for v in values)


def moment(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")
