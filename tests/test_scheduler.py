"""Tests for alarmclock.core.scheduler.

This file tests the sleep-plan scheduler which computes next wake-up times
and fires events. Tests are deterministic and use injected time functions.
"""

import asyncio
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
import pytest
import pytest_asyncio
from alarmclock.core.scheduler import Scheduler
from alarmclock.core.alarm import Weekday


class MockEventBus:
    """Mock event bus for testing."""

    def __init__(self):
        self.emitted = []

    async def emit(self, event_name, payload):
        self.emitted.append((event_name, payload))


@pytest.fixture(autouse=True)
def _isolate_settings_dir(tmp_path, monkeypatch):
    """Scheduler now persists through Configurable.save_config(), which
    defaults to config/settings.toml relative to the cwd - keep tests from
    writing into the real repo config."""
    (tmp_path / "config").mkdir()
    monkeypatch.chdir(tmp_path)


@pytest_asyncio.fixture
def mock_bus():
    """Async-safe bus fixture."""
    return MockEventBus()


def make_scheduler(bus, *, now, timezone="UTC", name="test_scheduler"):
    return Scheduler(bus=bus, timezone=timezone, name=name, now=now)


# Default datetime for all tests (Friday Oct 27, 2023 at 10:00 UTC)
_BASE_TIME = datetime(2023, 10, 27, 10, 0, 0, tzinfo=ZoneInfo("UTC"))


# ============================================================================
# Core calculation tests (pure, no persistence involved)
# ============================================================================

def test_next_date_for_weekday_basic():
    """Test basic calculation of next occurrence without reference time."""
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)

    date_result = sched._next_date_for_weekday(
        day=Weekday.MONDAY,
        after=sched._now(),
        reference_time=None
    )
    assert date_result == datetime(2023, 10, 30).date()


def test_next_date_with_past_reference():
    """Test calculation when target time has already passed today."""
    mock_now = datetime(2023, 10, 30, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(MockEventBus(), now=lambda: mock_now)

    date_result = sched._next_date_for_weekday(
        day=Weekday.MONDAY,
        after=mock_now,
        reference_time=time(8, 0)
    )
    # Should roll to next week since it's past the time today (10 AM > 8 AM)
    assert date_result == datetime(2023, 11, 6).date()


# ============================================================================
# Default scheduler fixture - creates a Scheduler with a Friday group at
# 8:00 AM. _BASE_TIME is Fri Oct 27, 10:00 UTC, so get_alarm_reference_date()
# returns Fri Nov 3 (next Friday at 8 AM).
# ============================================================================

@pytest_asyncio.fixture
async def default_scheduler(mock_bus):
    sched = make_scheduler(mock_bus, now=lambda: _BASE_TIME)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))
    return sched


# ============================================================================
# Reference date / master toggle tests
# ============================================================================

@pytest.mark.asyncio
async def test_get_alarm_reference_date(default_scheduler):
    """Today is Friday Oct 27 at 10:00 AM, with a group configured for
    Friday at 8 AM. The next occurrence is next Friday."""
    ref = default_scheduler.get_alarm_reference_date()
    assert ref == datetime(2023, 11, 3).date()


@pytest.mark.asyncio
async def test_skip_next_alarm(default_scheduler):
    pre_date = default_scheduler.get_alarm_reference_date()
    assert pre_date == datetime(2023, 11, 3).date()

    result = await default_scheduler.skip_next_alarm()
    assert result is True
    assert default_scheduler.is_next_alarm_skipped()


@pytest.mark.asyncio
async def test_skip_returns_false_when_nothing_to_skip():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)

    result = await sched.skip_next_alarm()
    assert result is False


# ============================================================================
# Group management tests
# ============================================================================

@pytest.mark.asyncio
async def test_create_group_valid():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)

    group = await sched.create_group(frozenset([Weekday.MONDAY]), time(8, 0))
    assert group is not None


@pytest.mark.asyncio
async def test_create_group_conflict():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    await sched.create_group(frozenset([Weekday.MONDAY]), time(8, 0))

    with pytest.raises(ValueError, match="already assigned"):
        await sched.create_group(frozenset([Weekday.MONDAY]), time(9, 0))


@pytest.mark.asyncio
async def test_set_group_time_permanent():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    group = await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    new_time = time(9, 30)
    await sched.set_group_time(group.id, new_time, permanent=True)

    plan = await sched.get_plan()
    assert plan.groups[0].time == new_time


@pytest.mark.asyncio
async def test_update_group_days_and_time():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    group = await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    new_days = frozenset([Weekday.WEDNESDAY, Weekday.FRIDAY])
    new_time = time(19, 30)
    await sched.update_group(group.id, new_days, new_time)

    plan = await sched.get_plan()
    assert len(plan.groups) == 1
    assert plan.groups[0].days == new_days
    assert plan.groups[0].time == new_time


@pytest.mark.asyncio
async def test_update_group_conflict_raises():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    monday_group = await sched.create_group(frozenset([Weekday.MONDAY]), time(8, 0))
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    with pytest.raises(ValueError):
        # Friday group is created above but we try to expand Monday's group
        # into a day (Friday) that's already claimed.
        await sched.update_group(
            monday_group.id, frozenset([Weekday.MONDAY, Weekday.FRIDAY]), time(9, 0)
        )


@pytest.mark.asyncio
async def test_delete_group():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    group = await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    plan = await sched.get_plan()
    assert len(plan.groups) == 1

    await sched.delete_group(group.id)

    plan = await sched.get_plan()
    assert len(plan.groups) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_group():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)

    with pytest.raises(ValueError):
        await sched.delete_group("nonexistent_group")


@pytest.mark.asyncio
async def test_set_day_once():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    await sched.set_day_once(Weekday.MONDAY, time(7, 30))

    plan = await sched.get_plan()
    assert len(plan.overrides) == 1


@pytest.mark.asyncio
async def test_set_day_once_with_none_time():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    await sched.set_day_once(Weekday.MONDAY, None)

    plan = await sched.get_plan()
    assert len(plan.overrides) == 1


# ============================================================================
# Enable/disable tests
# ============================================================================

@pytest.mark.asyncio
async def test_toggle_group_enabled_when_enabled():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    created_group = await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    result = await sched.toggle_group_enabled(created_group.id)
    assert result is False  # enabled=True -> disabled=False


@pytest.mark.asyncio
async def test_set_group_enabled_enables_again():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    created_group = await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    # Disable, releasing Friday, then re-enable.
    await sched.set_group_enabled(created_group.id, False)
    await sched.set_group_enabled(created_group.id, True)

    plan = await sched.get_plan()
    assert plan.groups[0].enabled is True


@pytest.mark.asyncio
async def test_set_group_enabled_conflict_on_reenable():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    group = await sched.create_group(frozenset([Weekday.FRIDAY, Weekday.MONDAY]), time(8, 0))

    # Disabling releases Fri & Mon so another group can claim them.
    await sched.set_group_enabled(group.id, False)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    # Re-enabling the original group now conflicts on Friday.
    with pytest.raises(ValueError, match="already assigned"):
        await sched.set_group_enabled(group.id, True)


@pytest.mark.asyncio
async def test_set_enabled_false_disables_trigger():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    await sched.set_enabled(False)

    assert await sched._next_due() is None


# ============================================================================
# Override tests
# ============================================================================

@pytest.mark.asyncio
async def test_clear_alarm_override():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    await sched.override_next_alarm_time(time(10, 0))

    result = await sched.clear_alarm_override()
    assert result is True


@pytest.mark.asyncio
async def test_clear_nothing_to_clear():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)

    result = await sched.clear_alarm_override()
    assert result is False


@pytest.mark.asyncio
async def test_override_next_alarm_time():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)

    reference = await sched.override_next_alarm_time(time(7, 0))

    plan = await sched.get_plan()
    assert plan.overrides[reference] == time(7, 0)


# ============================================================================
# Run loop tests
# ============================================================================

@pytest.mark.asyncio
async def test_scheduler_start():
    bus = MockEventBus()
    now_fn = lambda: datetime(2023, 10, 27, 8, 59, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(bus, now=now_fn)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    assert sched._task is None
    await sched.start()
    assert sched._task is not None

    await sched.stop()


@pytest.mark.asyncio
async def test_scheduler_stop():
    bus = MockEventBus()
    now_fn = lambda: datetime(2023, 10, 27, 8, 59, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(bus, now=now_fn)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    await sched.start()
    assert sched._task is not None and not sched._task.done()

    await asyncio.sleep(0.1)  # let the loop process at least one iteration
    await sched.stop()

    assert sched._task is None


@pytest.mark.asyncio
async def test_scheduler_start_twice():
    bus = MockEventBus()
    now_fn = lambda: datetime(2023, 10, 27, 8, 59, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(bus, now=now_fn)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    await sched.start()
    task_id = id(sched._task)

    await sched.start()  # should reuse same task
    assert id(sched._task) == task_id

    await sched.stop()


@pytest.mark.asyncio
async def test_no_event_without_groups():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)

    trigger = await sched._next_due()
    assert trigger is None


@pytest.mark.asyncio
async def test_disabled_plan_no_trigger():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))
    await sched.set_enabled(False)

    trigger = await sched._next_due()
    assert trigger is None


@pytest.mark.asyncio
async def test_fire_method():
    bus = MockEventBus()
    now_fn = lambda: datetime(2023, 10, 27, 8, 59, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(bus, now=now_fn)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    trigger = datetime.combine(date.today(), time(8, 0), tzinfo=ZoneInfo("UTC"))
    await sched._fire(trigger)

    assert len(bus.emitted) == 1
    assert bus.emitted[0][0] == "alarm.triggered"


@pytest.mark.asyncio
async def test_scheduler_fires_alarm_event():
    """End-to-end: start() actually fires alarm.triggered when the alarm is due."""
    bus = MockEventBus()
    now = datetime(2023, 10, 27, 8, 59, 59, 900000, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(bus, now=lambda: now)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(9, 0))

    await sched.start()
    await asyncio.sleep(0.2)
    await sched.stop()

    assert len(bus.emitted) == 1
    assert bus.emitted[0][0] == "alarm.triggered"


# ============================================================================
# Alarm control tests
# ============================================================================

@pytest.mark.asyncio
async def test_snooze_alarm():
    bus = MockEventBus()
    now_fn = lambda: datetime(2023, 10, 27, 8, 59, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(bus, now=now_fn)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    result = await sched.snooze_alarm(minutes=9)
    assert isinstance(result, datetime)
    assert result == now_fn() + __import__("datetime").timedelta(minutes=9)


@pytest.mark.asyncio
async def test_stop_alarm():
    bus = MockEventBus()
    now_fn = lambda: datetime(2023, 10, 27, 8, 59, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(bus, now=now_fn)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))
    await sched.snooze_alarm(minutes=9)

    await sched.stop_alarm()

    plan = await sched.get_plan()
    assert plan.snooze_until is None
    assert bus.emitted[-1][0] == "alarm.stopped"


# ============================================================================
# Persistence (real Configurable/TOML round trip)
# ============================================================================

@pytest.mark.asyncio
async def test_plan_survives_restart(tmp_path):
    """A group, an override, and a snooze all round-trip through settings.toml
    into a freshly constructed Scheduler that loads the same instance name."""
    settings_path = tmp_path / "settings.toml"
    bus = MockEventBus()

    sched = Scheduler(bus=bus, name="scheduler", settings_path=settings_path, now=lambda: _BASE_TIME)
    await sched.load_config(sched.name)
    await sched.create_group(frozenset([Weekday.MONDAY]), time(7, 0))
    await sched.set_day_once(Weekday.SATURDAY, None)  # skip override

    restarted = Scheduler(bus=bus, name="scheduler", settings_path=settings_path, now=lambda: _BASE_TIME)
    await restarted.load_config(restarted.name)
    plan = await restarted.get_plan()

    assert len(plan.groups) == 1
    assert plan.groups[0].days == frozenset([Weekday.MONDAY])
    assert plan.groups[0].time == time(7, 0)
    assert None in plan.overrides.values()


@pytest.mark.asyncio
async def test_snooze_persists_across_restart(tmp_path):
    settings_path = tmp_path / "settings.toml"
    bus = MockEventBus()

    sched = Scheduler(bus=bus, name="scheduler", settings_path=settings_path, now=lambda: _BASE_TIME)
    await sched.load_config(sched.name)
    await sched.snooze_alarm(minutes=9)

    restarted = Scheduler(bus=bus, name="scheduler", settings_path=settings_path, now=lambda: _BASE_TIME)
    await restarted.load_config(restarted.name)
    plan = await restarted.get_plan()
    assert plan.snooze_until == _BASE_TIME + __import__("datetime").timedelta(minutes=9)