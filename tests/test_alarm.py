"""Tests for alarmclock.core.alarm — SleepPlanGroup, SleepPlan, Weekday."""

import datetime
import pytest
from alarmclock.core.alarm import (
    Weekday,
    SleepPlanGroup,
    SleepPlan,
    WEEKDAYS,
    WEEKEND,
    EVERY_DAY,
)


# -- Weekday ------------------------------------------------------------------

class TestWeekday:
    def test_weekday_matches_date_weekday(self):
        """Weekday enum values match datetime.date.weekday()."""
        # 2023-10-23 is a Monday
        monday = datetime.date(2023, 10, 23)
        assert Weekday(monday.weekday()) == Weekday.MONDAY

        # 2023-10-29 is a Sunday
        sunday = datetime.date(2023, 10, 29)
        assert Weekday(sunday.weekday()) == Weekday.SUNDAY

    def test_weekday_values_0_to_6(self):
        for day in Weekday:
            assert 0 <= day.value <= 6


class TestWeekdayCollections:
    def test_weekdays_contains_workdays(self):
        assert Weekday.MONDAY in WEEKDAYS
        assert Weekday.FRIDAY in WEEKDAYS
        assert Weekday.SATURDAY not in WEEKDAYS

    def test_weekend_contains_sat_sun(self):
        assert Weekday.SATURDAY in WEEKEND
        assert Weekday.SUNDAY in WEEKEND
        assert len(WEEKEND) == 2

    def test_every_day_contains_all(self):
        assert len(EVERY_DAY) == 7
        for day in Weekday:
            assert day in EVERY_DAY


# -- SleepPlanGroup -----------------------------------------------------------

class TestSleepPlanGroup:
    def test_create_group(self):
        group = SleepPlanGroup(
            days=frozenset([Weekday.MONDAY, Weekday.WEDNESDAY]),
            time=datetime.time(7, 0),
        )
        assert len(group.days) == 2
        assert group.time == datetime.time(7, 0)
        assert group.enabled is True

    def test_empty_days_raises(self):
        with pytest.raises(ValueError, match="at least one weekday"):
            SleepPlanGroup(days=frozenset(), time=datetime.time(6, 0))

    def test_to_dict_and_back(self):
        group = SleepPlanGroup(
            days=frozenset([Weekday.MONDAY, Weekday.FRIDAY]),
            time=datetime.time(8, 30),
            enabled=False,
        )
        data = group.to_dict()
        assert data["days"] == [0, 4]
        assert data["time"] == "08:30:00"
        assert data["enabled"] is False

        restored = SleepPlanGroup.from_dict(data)
        assert restored.days == frozenset([Weekday.MONDAY, Weekday.FRIDAY])
        assert restored.time == datetime.time(8, 30)
        assert restored.enabled is False

    def test_from_dict_defaults_enabled(self):
        data = {"id": "abc", "days": [2], "time": "06:00:00"}
        group = SleepPlanGroup.from_dict(data)
        assert group.enabled is True

    def test_id_is_uuid_hex(self):
        group = SleepPlanGroup(
            days=frozenset([Weekday.TUESDAY]),
            time=datetime.time(9, 0),
        )
        # UUID hex is 32 chars
        assert len(group.id) == 32


# -- SleepPlan ----------------------------------------------------------------

class TestSleepPlan:
    def test_default_plan(self):
        plan = SleepPlan()
        assert plan.groups == []
        assert plan.overrides == {}
        assert plan.enabled is True
        assert plan.snooze_until is None

    def test_to_dict_and_back_empty(self):
        plan = SleepPlan()
        data = plan.to_dict()
        restored = SleepPlan.from_dict(data)
        assert restored.groups == []
        assert restored.overrides == {}
        assert restored.enabled is True
        assert restored.snooze_until is None

    def test_to_dict_and_back_with_groups(self):
        group = SleepPlanGroup(
            days=frozenset([Weekday.MONDAY, Weekday.WEDNESDAY]),
            time=datetime.time(7, 0),
        )
        plan = SleepPlan(groups=[group], enabled=True)
        data = plan.to_dict()

        restored = SleepPlan.from_dict(data)
        assert len(restored.groups) == 1
        assert restored.groups[0].days == frozenset([Weekday.MONDAY, Weekday.WEDNESDAY])
        assert restored.groups[0].time == datetime.time(7, 0)

    def test_to_dict_and_back_with_overrides(self):
        skip_date = datetime.date(2024, 1, 15)
        retimed_date = datetime.date(2024, 1, 16)
        plan = SleepPlan(
            overrides={
                skip_date: None,  # skipped
                retimed_date: datetime.time(9, 30),
            }
        )
        data = plan.to_dict()

        restored = SleepPlan.from_dict(data)
        assert restored.overrides[skip_date] is None
        assert restored.overrides[retimed_date] == datetime.time(9, 30)

    def test_to_dict_and_back_with_snooze(self):
        snooze_at = datetime.datetime(2024, 6, 15, 7, 9, 0)
        plan = SleepPlan(snooze_until=snooze_at)
        data = plan.to_dict()

        restored = SleepPlan.from_dict(data)
        assert restored.snooze_until == snooze_at

    def test_from_dict_defaults(self):
        data = {}
        plan = SleepPlan.from_dict(data)
        assert plan.enabled is True
        assert plan.groups == []
        assert plan.overrides == {}
        assert plan.snooze_until is None

    def test_disabled_plan_serializes(self):
        plan = SleepPlan(enabled=False)
        data = plan.to_dict()
        assert data["enabled"] is False

        restored = SleepPlan.from_dict(data)
        assert restored.enabled is False
