from __future__ import annotations

import datetime
import decimal


def date_to_int(date: datetime.date, month_only: bool = False) -> int:
    """
    Converts a date object to an integer representation.

    For example, the `date(2025, 3, 10)` gets converted to `20250310`.

    If `month_only` is set to `True`, the day will be removed from the date.

    For example, the same date above gets converted to `202503`.
    """
    date_format = "%Y%m" if month_only else "%Y%m%d"
    return int(datetime.date.strftime(date, date_format))


def int_to_date(date: int | str, month_only: bool = False) -> datetime.date:
    """
    Converts an `int` or `str` object to the `datetime.date` representation.

    For example, the int `20250310` gets converted to `date(2025, 3, 10)`.
    """
    date_format = "%Y%m" if month_only else "%Y%m%d"
    return datetime.datetime.strptime(str(date), date_format).date()


def date_to_datetime(date: datetime.date | None) -> datetime.datetime | None:
    """
    Converts one object from date to the datetime object.

    The reverse is possible directly by calling `datetime.date()`.
    """
    if date is None:
        return None
    return datetime.datetime.combine(date, datetime.time.min)


def day_to_ordinal(day: int) -> str:
    """Converts an integer day to an ordinal number, i.e., 1 -> 1st, 32 -> 32nd"""
    if 11 <= (day % 100) <= 13:
        suffix = "th"
    else:
        suffix = ["th", "st", "nd", "rd", "th"][min(day % 10, 4)]
    return f"{day}{suffix}"


def next_month(month: datetime.date) -> datetime.date:
    """
    Returns the next month after the provided `month`. [Original source](https://stackoverflow.com/a/59199379/12681470).
    """
    return (month.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)


def month_range(month: datetime.date) -> tuple[datetime.date, datetime.date]:
    """
    Range of the provided `month` as a tuple `[start, end)`.

    The end date is not inclusive, as it represents the start of the next month.
    """
    range_start = month.replace(day=1)
    range_end = next_month(month)
    return range_start, range_end


def current_timestamp() -> int:
    """Returns the current timestamp in milliseconds, using UTC time."""
    return int(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).timestamp() * 1000)


def cents_to_decimal(amount: int) -> decimal.Decimal:
    """
    Converts the number of cents to a `decimal.Decimal` object.

    When providing `500`, the result will be `decimal.Decimal(5.0)`.
    """
    return decimal.Decimal(amount) / decimal.Decimal(100)


def decimal_to_cents(amount: decimal.Decimal | int | float) -> int:
    """
    Converts the decimal amount (`decimal.Decimal` or `int` or `float`) to an integer value.

    When providing `decimal.Decimal(5.0)`, the result will be `500`.
    """
    return int(round(amount * 100))
