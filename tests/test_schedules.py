import datetime

from actual.schedules import Schedule


def test_basic_schedules():
    s = Schedule.parse_obj(
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
    assert s.xafter(datetime.date(2024, 5, 10), count=5) == [
        datetime.date(2024, 5, 10),
        datetime.date(2024, 5, 13),
        datetime.date(2024, 5, 27),
        datetime.date(2024, 5, 31),
        datetime.date(2024, 6, 5),
    ]
    # change the solve mode to before
    s.weekend_solve_mode = "before"
    assert s.xafter(datetime.date(2024, 5, 10), count=5) == [
        datetime.date(2024, 5, 10),
        # according to frontend, this entry happens twice
        datetime.date(2024, 5, 10),
        datetime.date(2024, 5, 24),
        datetime.date(2024, 5, 31),
        datetime.date(2024, 6, 5),
    ]
