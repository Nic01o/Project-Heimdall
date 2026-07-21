"""Sleep plan data model."""

from __future__ import annotations

import dataclasses
import datetime
import enum
import uuid
from typing import Any


class Weekday(enum.IntEnum):
    """Matches `datetime.date.weekday()`: Monday = 0 ... Sunday = 6."""

    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


WEEKDAYS = frozenset(
    {Weekday.MONDAY, Weekday.TUESDAY, Weekday.WEDNESDAY, Weekday.THURSDAY, Weekday.FRIDAY}
)
WEEKEND = frozenset({Weekday.SATURDAY, Weekday.SUNDAY})
EVERY_DAY = frozenset(Weekday)


@dataclasses.dataclass
class SleepPlanGroup:
    """A set of weekdays that share one wake time. Groups are disjoint: a
    weekday belongs to at most one group at a time."""

    days: frozenset[Weekday]
    time: datetime.time
    id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if not self.days:
            raise ValueError("SleepPlanGroup needs at least one weekday")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "days": sorted(int(day) for day in self.days),
            "time": self.time.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SleepPlanGroup":
        return cls(
            days=frozenset(Weekday(day) for day in data["days"]),
            time=datetime.time.fromisoformat(data["time"]),
            id=data["id"],
        )


@dataclasses.dataclass
class SleepPlan:
    """The single sleep schedule: 0-7 disjoint weekday groups, plus one-time
    exceptions (`overrides`, keyed by concrete date - a `None` value means
    "skip this one occurrence") and a transient `snooze_until`.
    """

    groups: list[SleepPlanGroup] = dataclasses.field(default_factory=list)
    overrides: dict[datetime.date, datetime.time | None] = dataclasses.field(
        default_factory=dict
    )
    enabled: bool = True
    snooze_until: datetime.datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [group.to_dict() for group in self.groups],
            "overrides": {
                date.isoformat(): (time.isoformat() if time else None)
                for date, time in self.overrides.items()
            },
            "enabled": self.enabled,
            "snooze_until": self.snooze_until.isoformat() if self.snooze_until else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SleepPlan":
        return cls(
            groups=[SleepPlanGroup.from_dict(group) for group in data.get("groups", [])],
            overrides={
                datetime.date.fromisoformat(date): (
                    datetime.time.fromisoformat(time) if time else None
                )
                for date, time in data.get("overrides", {}).items()
            },
            enabled=data.get("enabled", True),
            snooze_until=(
                datetime.datetime.fromisoformat(data["snooze_until"])
                if data.get("snooze_until")
                else None
            ),
        )
