import datetime
import enum
import typing

import pydantic
from dateutil.rrule import (
    DAILY,
    MONTHLY,
    WEEKLY,
    YEARLY,
    rrule,
    rruleset,
    weekday,
    weekdays,
)


def date_to_datetime(date: typing.Optional[datetime.date]) -> typing.Optional[datetime.datetime]:
    """Converts one object from date to datetime object. The reverse is possible directly by calling datetime.date()."""
    if date is None:
        return None
    return datetime.datetime.combine(date, datetime.time.min)


def day_to_ordinal(day: int) -> str:
    """Converts an integer day to an ordinal number, i.e. 1 -> 1st, 32 -> 32nd"""
    if 11 <= (day % 100) <= 13:
        suffix = "th"
    else:
        suffix = ["th", "st", "nd", "rd", "th"][min(day % 10, 4)]
    return f"{day}{suffix}"


class EndMode(enum.Enum):
    AFTER_N_OCCURRENCES = "after_n_occurrences"
    ON_DATE = "on_date"
    NEVER = "never"


class Frequency(enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"

    def as_dateutil(self) -> int:
        frequency_map = {"YEARLY": YEARLY, "MONTHLY": MONTHLY, "WEEKLY": WEEKLY, "DAILY": DAILY}
        return frequency_map[self.name]


class WeekendSolveMode(enum.Enum):
    BEFORE = "before"
    AFTER = "after"


class PatternType(enum.Enum):
    SUNDAY = "SU"
    MONDAY = "MO"
    TUESDAY = "TU"
    WEDNESDAY = "WE"
    THURSDAY = "TH"
    FRIDAY = "FR"
    SATURDAY = "SA"
    DAY = "day"

    def as_dateutil(self) -> weekday:
        weekday_map = {str(w): w for w in weekdays}
        return weekday_map[self.value]


class Pattern(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(validate_assignment=True)

    value: int
    type: PatternType

    def __str__(self) -> str:
        if self.value == -1:
            qualifier = "last"
        else:
            qualifier = day_to_ordinal(self.value)
        type_str = ""
        if self.type != PatternType.DAY:
            type_str = f" {self.type.name.lower().capitalize()}"
        elif self.value == -1:
            type_str = " day"
        return f"{qualifier}{type_str}"


class Schedule(pydantic.BaseModel):
    """
    Implements basic schedules. They are described in https://actualbudget.org/docs/budgeting/schedules/

    Schedules are part of a rule which then compares if the date found would fit within the schedule by the .is_approx()
    method. If it does, and the other conditions match, the transaction will then be linked with the schedule id
    (stored in the database).
    """

    model_config = pydantic.ConfigDict(validate_assignment=True)

    start: datetime.date = pydantic.Field(..., description="Start date of the schedule.")
    interval: int = pydantic.Field(1, description="Repeat every interval at frequency unit.")
    frequency: Frequency = pydantic.Field(Frequency.MONTHLY, description="Unit for the defined interval.")
    patterns: typing.List[Pattern] = pydantic.Field(default_factory=list)
    skip_weekend: bool = pydantic.Field(
        False, alias="skipWeekend", description="If should move schedule before or after a weekend."
    )
    weekend_solve_mode: WeekendSolveMode = pydantic.Field(
        WeekendSolveMode.AFTER,
        alias="weekendSolveMode",
        description="When skipping weekend, the value should be set before or after the weekend interval.",
    )
    end_mode: EndMode = pydantic.Field(
        EndMode.NEVER,
        alias="endMode",
        description="If the schedule should run forever or end at a certain date or number of occurrences.",
    )
    end_occurrences: int = pydantic.Field(
        1, alias="endOccurrences", description="Number of occurrences before the schedule ends."
    )
    end_date: datetime.date = pydantic.Field(None, alias="endDate")

    def __str__(self) -> str:
        # evaluate frequency: handle the case where DAILY convert to 'dai' instead of 'day'
        interval = "day" if self.frequency == Frequency.DAILY else self.frequency.value.rstrip("ly")
        frequency = interval if self.interval == 1 else f"{self.interval} {interval}s"
        # evaluate
        if self.frequency == Frequency.YEARLY:
            target = f" on {self.start.strftime('%b %d')}"
        elif self.frequency == Frequency.MONTHLY:
            if not self.patterns:
                target = f" on the {day_to_ordinal(self.start.day)}"
            else:
                patterns_str = []
                for pattern in self.patterns:
                    patterns_str.append(str(pattern))
                target = " on the " + ", ".join(patterns_str)
        elif self.frequency == Frequency.WEEKLY:
            target = f" on {self.start.strftime('%A')}"
        else:  # DAILY
            target = ""
        # end date part
        if self.end_mode == EndMode.ON_DATE:
            end = f", until {self.end_date}"
        elif self.end_mode == EndMode.AFTER_N_OCCURRENCES:
            end = ", once" if self.end_occurrences == 1 else f", {self.end_occurrences} times"
        else:
            end = ""
        # weekend skips
        move = f" ({self.weekend_solve_mode.value} weekend)" if self.skip_weekend else ""
        return f"Every {frequency}{target}{end}{move}"

    @pydantic.model_validator(mode="after")
    def validate_end_date(self):
        if self.end_mode == EndMode.ON_DATE and self.end_date is None:
            raise ValueError("endDate cannot be 'None' when ")
        if self.end_date is None:
            self.end_date = self.start
        return self

    def is_approx(self, date: datetime.date, interval: datetime.timedelta = datetime.timedelta(days=2)) -> bool:
        """This function checks if the input date could fit inside of this schedule. It will use the interval as the
        maximum threshold before and after the specified date to look for. This defaults on Actual to 2 days."""
        if date < self.start or (self.end_mode == EndMode.ON_DATE and self.end_date < date):
            return False
        before = self.before(date)
        after = self.xafter(date, 1)
        if before and (before - interval <= date <= before + interval):
            return True
        if after and (after[0] - interval <= date <= after[0] + interval):
            return True
        return False

    def rruleset(self) -> rruleset:
        """Returns the rruleset from dateutil library. This is used internally to calculate the schedule dates.

        For information on how to use this object check the official documentation https://dateutil.readthedocs.io"""
        rule_sets_configs = []
        config = dict(freq=self.frequency.as_dateutil(), dtstart=self.start, interval=self.interval)
        # add termination options
        if self.end_mode == EndMode.ON_DATE:
            config["until"] = self.end_date
        elif self.end_mode == EndMode.AFTER_N_OCCURRENCES:
            config["count"] = self.end_occurrences
        if self.frequency == Frequency.MONTHLY and self.patterns:
            by_month_day, by_weekday = [], []
            for p in self.patterns:
                if p.type == PatternType.DAY:
                    by_month_day.append(p.value)
                else:  # it's a weekday
                    by_weekday.append(p.type.as_dateutil()(p.value))
            # for the month or weekday rules, add a different rrule to the ruleset. This is because otherwise the rule
            # would only look for, for example, days that are 15 that are also Fridays, and that is not desired
            if by_month_day:
                monthly_config = config.copy()
                monthly_config.update({"bymonthday": by_month_day})
                rule_sets_configs.append(monthly_config)
            if by_weekday:
                weekly_config = config.copy()
                weekly_config.update({"byweekday": by_weekday})
                rule_sets_configs.append(weekly_config)
        # if ruleset does not contain multiple rules, add the current rule as default
        if not rule_sets_configs:
            rule_sets_configs.append(config)
        # create rule set
        rs = rruleset(cache=True)
        for cfg in rule_sets_configs:
            rs.rrule(rrule(**cfg))
        return rs

    def do_skip_weekend(
        self, dt_start: datetime.datetime, value: datetime.datetime
    ) -> typing.Optional[datetime.datetime]:
        if value.weekday() in (5, 6) and self.skip_weekend:
            if self.weekend_solve_mode == WeekendSolveMode.AFTER:
                value = value + datetime.timedelta(days=7 - value.weekday())
                if self.end_mode == EndMode.ON_DATE and value > date_to_datetime(self.end_date):
                    return None
            else:  # BEFORE
                value_before = value - datetime.timedelta(days=value.weekday() - 4)
                if value_before < dt_start:
                    # value is in the past, skip and look for another
                    return None
                value = value_before
        return value

    def before(self, date: datetime.date = None) -> typing.Optional[datetime.date]:
        if not date:
            date = datetime.date.today()
        dt_start = date_to_datetime(date)
        # we also always use the day before since today can also be a valid entry for our time
        rs = self.rruleset()
        before_datetime = rs.before(dt_start)
        if not before_datetime:
            return None
        with_weekend_skip = self.do_skip_weekend(date_to_datetime(self.start), before_datetime)
        if not with_weekend_skip:
            return None
        return with_weekend_skip.date()

    def xafter(self, date: datetime.date = None, count: int = 1) -> typing.List[datetime.date]:
        if not date:
            date = datetime.date.today()
        # dateutils only accepts datetime for evaluation
        dt_start = datetime.datetime.combine(date, datetime.time.min)
        # we also always use the day before since today can also be a valid entry for our time
        rs = self.rruleset()

        ret = []
        for value in rs.xafter(dt_start, count, inc=True):
            if value := self.do_skip_weekend(dt_start, value):
                # convert back to date
                ret.append(value.date())
            if len(ret) == count:
                break
        return sorted(ret)
