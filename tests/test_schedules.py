from datetime import date
from unittest.mock import MagicMock

import pytest

from actual.queries import create_account, create_transaction
from actual.rules import Rule
from actual.schedules import Schedule, date_to_datetime


def test_basic_schedules():
    s = Schedule.model_validate(
        {
            "start": "2024-05-12",
            "frequency": "monthly",
            "skipWeekend": False,
            "endMode": "after_n_occurrences",
            "endOccurrences": 3,
            "interval": 1,
        }
    )
    assert s.before(date(2024, 5, 13)) == date(2024, 5, 12)
    assert s.xafter(date(2024, 5, 12), 4) == [
        date(2024, 5, 12),
        date(2024, 6, 12),
        date(2024, 7, 12),
    ]

    assert str(s) == "Every month on the 12th, 3 times"


def test_complex_schedules():
    s = Schedule.model_validate(
        {
            "start": "2024-05-08",
            "frequency": "monthly",
            "patterns": [
                {"value": -1, "type": "SU"},
                {"value": 2, "type": "SA"},
                {"value": 10, "type": "day"},
                {"value": 31, "type": "day"},
                {"value": 5, "type": "day"},
            ],
            "skipWeekend": True,
            "weekendSolveMode": "after",
            "endMode": "never",
            "endOccurrences": 1,
            "endDate": "2024-05-08",
            "interval": 1,
        }
    )
    assert s.xafter(date(2024, 5, 10), count=5) == [
        date(2024, 5, 10),
        date(2024, 5, 13),
        date(2024, 5, 27),
        date(2024, 5, 31),
        date(2024, 6, 5),
    ]
    # change the solve mode to before
    s.weekend_solve_mode = "before"
    assert s.xafter(date(2024, 5, 10), count=5) == [
        date(2024, 5, 10),
        # according to frontend, this entry happens twice
        date(2024, 5, 10),
        date(2024, 5, 24),
        date(2024, 5, 31),
        date(2024, 6, 5),
    ]

    assert str(s) == "Every month on the last Sunday, 2nd Saturday, 10th, 31st, 5th (before weekend)"


def test_skip_weekend_after_schedule():
    s = Schedule.model_validate(
        {
            "start": "2024-08-14",
            "interval": 1,
            "frequency": "monthly",
            "patterns": [],
            "skipWeekend": True,
            "weekendSolveMode": "after",
            "endMode": "on_date",
            "endOccurrences": 1,
            "endDate": "2024-09-14",
        }
    )
    after = s.xafter(date(2024, 9, 10), count=2)
    # we should ensure that dates that fall outside the endDate are not covered, even though actual will accept it
    assert after == []


def test_skip_weekend_before_schedule():
    s = Schedule.model_validate(
        {
            "start": "2024-04-10",
            "interval": 1,
            "frequency": "monthly",
            "patterns": [],
            "skipWeekend": True,
            "weekendSolveMode": "before",
            "endMode": "never",
            "endOccurrences": 1,
            "endDate": "2024-04-10",
        }
    )
    before = s.before(date(2024, 8, 14))
    assert before == date(2024, 8, 9)
    # check that it wouldn't pick itself
    assert s.before(date(2024, 7, 10)) == date(2024, 6, 10)
    # we should ensure that dates that fall outside the endDate are not covered, even though actual will accept it
    s.start = date(2024, 9, 21)
    assert s.before(date(2024, 9, 22)) is None


def test_is_approx():
    # create schedule for every 1st and last day of the month (30th or 31st)
    s = Schedule.model_validate(
        {
            "start": "2024-05-10",
            "frequency": "monthly",
            "patterns": [
                {"value": 1, "type": "day"},
                {"value": -1, "type": "day"},
            ],
            "skipWeekend": True,
            "weekendSolveMode": "after",
            "endMode": "on_date",
            "endOccurrences": 1,
            "endDate": "2024-07-01",
            "interval": 1,
        }
    )
    # make sure the xafter is correct
    assert s.xafter(date(2024, 6, 1), 5) == [
        date(2024, 6, 3),
        date(2024, 7, 1),
        date(2024, 7, 1),
    ]
    # compare is_approx
    assert s.is_approx(date(2024, 5, 1)) is False  # before starting period
    assert s.is_approx(date(2024, 5, 30)) is True
    assert s.is_approx(date(2024, 5, 31)) is True
    assert s.is_approx(date(2024, 6, 1)) is True
    assert s.is_approx(date(2024, 6, 3)) is True  # because 1st is also included

    # 30th June is a sunday, so the right date would be 1st of June
    assert s.is_approx(date(2024, 6, 28)) is False
    assert s.is_approx(date(2024, 6, 30)) is True
    assert s.is_approx(date(2024, 7, 1)) is True

    # after end date we reject everything
    assert s.is_approx(date(2024, 7, 2)) is False
    assert s.is_approx(date(2024, 7, 31)) is False

    assert str(s) == "Every month on the 1st, last day, until 2024-07-01 (after weekend)"


def test_date_to_datetime():
    dt = date(2024, 5, 1)
    assert date_to_datetime(dt).date() == dt
    assert date_to_datetime(None) is None


def test_exceptions():
    with pytest.raises(ValueError):
        # on_date is set but no date is provided
        Schedule.model_validate(
            {
                "start": "2024-05-12",
                "frequency": "monthly",
                "skipWeekend": False,
                "endMode": "on_date",
                "endOccurrences": 3,
                "interval": 1,
            }
        )


def test_strings():
    assert str(Schedule(start="2024-05-12", frequency="yearly")) == "Every year on May 12"
    assert str(Schedule(start="2024-05-12", frequency="weekly")) == "Every week on Sunday"
    assert str(Schedule(start="2024-05-12", frequency="daily")) == "Every day"


def test_scheduled_rule():
    mock = MagicMock()
    acct = create_account(mock, "Bank")
    rule = Rule(
        id="d84d1400-4245-4bb9-95d0-be4524edafe9",
        conditions=[
            {
                "op": "isapprox",
                "field": "date",
                "value": {
                    "start": "2024-05-01",
                    "frequency": "monthly",
                    "patterns": [],
                    "skipWeekend": False,
                    "weekendSolveMode": "after",
                    "endMode": "never",
                    "endOccurrences": 1,
                    "endDate": "2024-05-14",
                    "interval": 1,
                },
            },
            {"op": "isapprox", "field": "amount", "value": -2000},
            {"op": "is", "field": "acct", "value": acct.id},
        ],
        stage=None,
        actions=[{"op": "link-schedule", "value": "df1e464f-13ae-4a97-a07e-990faeb48b2f"}],
        conditions_op="and",
    )
    assert "'date' isapprox 'Every month on the 1st'" in str(rule)

    transaction_matching = create_transaction(mock, date(2024, 5, 2), acct, None, amount=-19)
    transaction_not_matching = create_transaction(mock, date(2024, 5, 2), acct, None, amount=-15)
    rule.run(transaction_matching)
    rule.run(transaction_not_matching)

    assert transaction_matching.schedule_id == "df1e464f-13ae-4a97-a07e-990faeb48b2f"
    assert transaction_not_matching.schedule_id is None
