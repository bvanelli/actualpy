import datetime
import enum

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


class EndMode(enum.Enum):
    AFTER_N_OCCURRENCES = "after_n_occurrences"
    ON_DATE = "on_date"
    NEVER = "never"


class Frequency(enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"

    def as_dateutil(self):
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
    value: int
    type: PatternType


class Schedule(pydantic.BaseModel):
    start: datetime.date = pydantic.Field(..., description="Start date of the schedule.")
    interval: int = pydantic.Field(1, description="Repeat every interval at frequency unit.")
    frequency: Frequency = pydantic.Field(Frequency.MONTHLY, description="Unit for the defined interval.")
    patterns: list[Pattern] = pydantic.Field(default_factory=list)
    skip_weekend: bool = pydantic.Field(
        alias="skipWeekend", description="If should move schedule before or after a weekend."
    )
    weekend_solve_mode: WeekendSolveMode = pydantic.Field(
        alias="weekendSolveMode",
        description="When skipping weekend, the value should be set before or after the weekend interval.",
    )
    end_mode: EndMode = pydantic.Field(
        EndMode.NEVER,
        alias="endMode",
        description="If the schedule should run forever or end at a certain date or number of occurrences.",
    )
    end_occurrences: int = pydantic.Field(
        WeekendSolveMode.AFTER, alias="endOccurrences", description="Number of occurrences before the schedule ends."
    )
    end_date: datetime.date = pydantic.Field(alias="endDate")

    def is_approx(self, date: datetime.date) -> bool:
        pass

    def rruleset(self) -> rruleset:
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
            if by_month_day:
                monthly_config = config.copy() | {"bymonthday": by_month_day}
                rule_sets_configs.append(monthly_config)
            if by_weekday:
                rule_sets_configs.append(config.copy() | {"byweekday": by_weekday})
        if not rule_sets_configs:
            rule_sets_configs.append(config)
        # create rule set
        rs = rruleset(cache=True)
        for cfg in rule_sets_configs:
            rs.rrule(rrule(**cfg))
        return rs

    def xafter(self, date: datetime.date = None, count: int = 1) -> list[datetime.date]:
        if not date:
            date = datetime.date.today()
        # dateutils only accepts datetime for evaluation
        dt_start = datetime.datetime.combine(date, datetime.time.min)
        # we also always use the day before since today can also be a valid entry for our time
        rs = self.rruleset()

        ret, i = [], 0
        for value in rs:
            value: datetime.datetime
            if value.weekday() in (5, 6) and self.skip_weekend:
                if self.weekend_solve_mode == WeekendSolveMode.AFTER:
                    value = value + datetime.timedelta(days=7 - value.weekday())
                else:  # BEFORE
                    value_after = value - datetime.timedelta(days=value.weekday() - 4)
                    if value_after < dt_start:
                        # value is in the past, skip and look for another
                        continue
                    value = value_after
            i += 1
            dt = value
            # convert back to date
            ret.append(dt.date())
            if len(ret) == count:
                break
        return sorted(ret)
