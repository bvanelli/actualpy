from __future__ import annotations

import datetime
import decimal
from typing import Tuple


def date_to_int(date: datetime.date, month_only: bool = False) -> int:
    """
    Converts a date object to an integer representation. For example, the `date(2025, 3, 10)` gets converted to
    `20250310`.

    If `month_only` is set to `True`, the day will be removed from the date. For example, the same date above gets
    converted to `202503`.
    """
    date_format = "%Y%m" if month_only else "%Y%m%d"
    return int(datetime.date.strftime(date, date_format))


def int_to_date(date: int | str, month_only: bool = False) -> datetime.date:
    """
    Converts an `int` or `str` object to the `datetime.date` representation. For example, the int `20250310`
    gets converted to `date(2025, 3, 10)`.
    """
    date_format = "%Y%m" if month_only else "%Y%m%d"
    return datetime.datetime.strptime(str(date), date_format).date()


def month_range(month: datetime.date) -> Tuple[datetime.date, datetime.date]:
    """
    Range of the provided `month` as a tuple [start, end).

    The end date is not inclusive, as it represents the start of the next month.
    """
    range_start = month.replace(day=1)
    # conversion taken from https://stackoverflow.com/a/59199379/12681470
    range_end = (range_start + datetime.timedelta(days=32)).replace(day=1)
    return range_start, range_end


def current_timestamp() -> int:
    """Returns the current timestamp in milliseconds, using UTC time."""
    return int(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).timestamp() * 1000)


def cents_to_decimal(amount: int) -> decimal.Decimal:
    """Converts the number of cents to a `decimal.Decimal` object. When providing `500`, the result will be
    `decimal.Decimal(5.0)`.
    """
    return decimal.Decimal(amount) / decimal.Decimal(100)


def decimal_to_cents(amount: decimal.Decimal | int | float) -> int:
    """Converts the decimal amount (`decimal.Decimal` or `int` or `float`) to an integer value. When providing
    `decimal.Decimal(5.0)`, the result will be `500`."""
    return int(round(amount * 100))
