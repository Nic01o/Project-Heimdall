"""Alarm data model."""

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
class Alarm:
    """A recurring alarm (`time` + `repeat`) or a one-shot alarm (`at`).

    Exactly one of `time` or `at` must be set; one-shots can't repeat.
    """

    time: datetime.time | None = None
    at: datetime.datetime | None = None
    label: str = ""
    repeat: frozenset[Weekday] = dataclasses.field(default_factory=frozenset)
    enabled: bool = True
    id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if (self.time is None) == (self.at is None):
            raise ValueError("Alarm needs exactly one of `time` or `at`")
        if self.at is not None and self.repeat:
            raise ValueError("one-shot alarms (`at`) cannot repeat")

    @property
    def is_one_shot(self) -> bool:
        return self.at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "time": self.time.isoformat() if self.time else None,
            "at": self.at.isoformat() if self.at else None,
            "repeat": sorted(int(day) for day in self.repeat),
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Alarm":
        """Inverse of to_dict(), used to restore persisted alarms."""
        return cls(
            time=datetime.time.fromisoformat(data["time"]) if data["time"] else None,
            at=datetime.datetime.fromisoformat(data["at"]) if data["at"] else None,
            label=data["label"],
            repeat=frozenset(Weekday(day) for day in data["repeat"]),
            enabled=data["enabled"],
            id=data["id"],
        )