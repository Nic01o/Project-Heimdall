"""Tests for alarmclock.core.scheduler.

This file tests the sleep-plan scheduler which computes next wake-up times
and fires events. Tests are deterministic and use injected time functions.
"""

import asyncio
from datetime import date, datetime, time, timedelta
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


@pytest.mark.asyncio
async def test_override_targets_uncovered_tomorrow_instead_of_next_covered_day():
    """Regression test for the reported bug: the plan covers Mon-Sat (not
    Sunday) at 16:11. On a Saturday evening, after Saturday's alarm has
    already rung, the override must land on Sunday - the very next day,
    even though the plan says nothing about it - not skip all the way to
    the following Monday, which is what a plan-only reference-date scan
    used to do."""
    saturday_evening = datetime(2023, 10, 28, 20, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(MockEventBus(), now=lambda: saturday_evening)
    await sched.create_group(
        frozenset(
            [
                Weekday.MONDAY,
                Weekday.TUESDAY,
                Weekday.WEDNESDAY,
                Weekday.THURSDAY,
                Weekday.FRIDAY,
                Weekday.SATURDAY,
            ]
        ),
        time(16, 11),
    )

    reference = await sched.override_next_alarm_time(time(10, 0))

    assert reference == date(2023, 10, 29)  # Sunday - tomorrow, not next Monday


@pytest.mark.asyncio
async def test_override_stays_on_today_before_todays_alarm_has_rung():
    """Set at 02:00, an override for a plan whose alarm hasn't fired yet
    today (08:00 is still ahead) must land on today, not tomorrow."""
    early_morning = datetime(2023, 10, 28, 2, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(MockEventBus(), now=lambda: early_morning)
    await sched.create_group(frozenset([Weekday.SATURDAY]), time(8, 0))

    reference = await sched.override_next_alarm_time(time(10, 0))

    assert reference == date(2023, 10, 28)


@pytest.mark.asyncio
async def test_override_rolls_to_tomorrow_once_todays_alarm_has_rung():
    """Set right at/after today's own alarm time, the override must roll
    over to tomorrow instead of overwriting an alarm that already fired."""
    right_after_wakeup = datetime(2023, 10, 28, 8, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(MockEventBus(), now=lambda: right_after_wakeup)
    await sched.create_group(frozenset([Weekday.SATURDAY]), time(8, 0))

    reference = await sched.override_next_alarm_time(time(10, 0))

    assert reference == date(2023, 10, 29)


@pytest.mark.asyncio
async def test_get_alarm_status_shows_override_on_a_day_the_plan_does_not_cover():
    """Once override_next_alarm_time has placed an override on a day no
    group covers, get_alarm_reference_date (and therefore the web UI's
    display, and clear_alarm_override) must follow it there instead of
    still pointing at the plan's next covered day."""
    saturday_evening = datetime(2023, 10, 28, 20, 0, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(MockEventBus(), now=lambda: saturday_evening)
    await sched.create_group(frozenset([Weekday.MONDAY]), time(16, 11))

    # Saturday has no group at all, so "hasn't rung yet" trivially holds -
    # the override lands on today, not the plan's next covered day (Monday).
    reference = await sched.override_next_alarm_time(time(10, 0))
    assert reference == date(2023, 10, 28)

    status = sched.get_alarm_status(saturday_evening)
    assert status.reference_date == date(2023, 10, 28)
    assert status.override_time == time(10, 0)

    result = await sched.clear_alarm_override(saturday_evening)
    assert result is True
    assert date(2023, 10, 28) not in (await sched.get_plan()).overrides


# ============================================================================
# Trigger computation tests (direct, pure - `next_trigger` /
# `_next_weekly_occurrence`). These previously were only exercised
# indirectly through `_next_due()`, which hid several distinct behaviors
# behind a single None/not-None assertion.
# ============================================================================

@pytest.mark.asyncio
async def test_next_trigger_uses_override_time_instead_of_group_time(default_scheduler):
    """A retimed override wins over a group's regular time on the same
    date, not just get added alongside it. `default_scheduler`'s Friday
    group already rang for today (08:00 < _BASE_TIME's 10:00), so the
    override lands on tomorrow, Sat Oct 28 - a date the plan doesn't cover
    at all - confirming the override doesn't wait for the plan's next
    Friday."""
    await default_scheduler.override_next_alarm_time(time(7, 0))
    plan = await default_scheduler.get_plan()

    trigger = default_scheduler.next_trigger(plan, _BASE_TIME)
    assert trigger == datetime(2023, 10, 28, 7, 0, tzinfo=ZoneInfo("UTC"))


@pytest.mark.asyncio
async def test_next_trigger_skips_overridden_day_and_finds_next_occurrence(default_scheduler):
    """Skipping the next occurrence (override value None) must not make the
    alarm disappear entirely - it should fall through to the following
    week's occurrence of the same group once queried from a point in time
    past the skipped date (the run loop naturally slides `after` forward
    like this on every re-check)."""
    await default_scheduler.skip_next_alarm()  # skips Fri Nov 3
    plan = await default_scheduler.get_plan()

    after = datetime(2023, 11, 4, 0, 0, tzinfo=ZoneInfo("UTC"))
    trigger = default_scheduler.next_trigger(plan, after)
    assert trigger == datetime(2023, 11, 10, 8, 0, tzinfo=ZoneInfo("UTC"))


@pytest.mark.asyncio
async def test_next_trigger_none_right_after_skipping_the_only_occurrence(default_scheduler):
    """Non-obvious boundary: the scan window is only 8 days wide, so
    skipping the sole occurrence inside that window yields None when
    queried from the same reference time - it does *not* jump straight to
    next week. Only once `after` itself advances past the skip does the
    following week's occurrence come into view (see the test above)."""
    await default_scheduler.skip_next_alarm()  # skips Fri Nov 3, the only Friday in range
    plan = await default_scheduler.get_plan()

    trigger = default_scheduler.next_trigger(plan, _BASE_TIME)
    assert trigger is None


@pytest.mark.asyncio
async def test_next_trigger_prefers_earlier_snooze_over_weekly_occurrence(default_scheduler):
    await default_scheduler.snooze_alarm(minutes=5)  # far earlier than next Friday
    plan = await default_scheduler.get_plan()

    trigger = default_scheduler.next_trigger(plan, _BASE_TIME)
    assert trigger == _BASE_TIME + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_next_trigger_prefers_earlier_weekly_occurrence_over_snooze():
    """When a stale/late snooze ends up later than the next regular
    occurrence, the earlier one must win - `next_trigger` takes the min of
    both candidates, not just "snooze always wins"."""
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))  # Nov 3, 08:00
    sched._plan.snooze_until = _BASE_TIME + timedelta(days=30)
    plan = await sched.get_plan()

    trigger = sched.next_trigger(plan, _BASE_TIME)
    assert trigger == datetime(2023, 11, 3, 8, 0, tzinfo=ZoneInfo("UTC"))


@pytest.mark.asyncio
async def test_next_trigger_snooze_fires_even_when_plan_disabled(default_scheduler):
    """`plan.enabled` only gates the recurring weekly schedule; a pending
    snooze is a standalone, one-shot trigger and must still fire."""
    await default_scheduler.snooze_alarm(minutes=5)
    await default_scheduler.set_enabled(False)
    plan = await default_scheduler.get_plan()

    trigger = default_scheduler.next_trigger(plan, _BASE_TIME)
    assert trigger == _BASE_TIME + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_next_trigger_none_when_nothing_scheduled():
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    plan = await sched.get_plan()

    assert sched.next_trigger(plan, _BASE_TIME) is None


@pytest.mark.asyncio
async def test_next_weekly_occurrence_picks_soonest_across_multiple_groups():
    """The scan must pick the chronologically nearest day across all
    groups, not the first group that happened to be created."""
    sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)
    await sched.create_group(frozenset([Weekday.MONDAY]), time(6, 0))    # created first, but due later (Oct 30)
    await sched.create_group(frozenset([Weekday.SATURDAY]), time(6, 0))  # created second, but due sooner (Oct 28)
    plan = await sched.get_plan()

    trigger = sched._next_weekly_occurrence(plan, _BASE_TIME)
    assert trigger == datetime(2023, 10, 28, 6, 0, tzinfo=ZoneInfo("UTC"))


# ============================================================================
# Timezone wiring tests - does `timezone=` passed into the Scheduler
# constructor (this is what daemon.py now reads from the webui's "Zeitzone"
# setting) actually reach the trigger computation, instead of silently
# staying UTC like it used to?
# ============================================================================

def test_scheduler_stores_the_configured_timezone():
    sched = Scheduler(bus=MockEventBus(), name="tz_check", timezone="Europe/Berlin")
    assert sched.tz == ZoneInfo("Europe/Berlin")


def test_scheduler_defaults_to_utc_when_no_timezone_given():
    """Regression guard for the exact bug: daemon.py used to construct the
    Scheduler without `timezone=` at all, silently falling back to this
    default no matter what was configured in the webui."""
    sched = Scheduler(bus=MockEventBus(), name="tz_check")
    assert sched.tz == ZoneInfo("UTC")


def test_scheduler_default_clock_uses_configured_timezone():
    """daemon.py never injects a `now=` callable - it relies on the default
    clock (`datetime.now(self.tz)`). This is the actual production code
    path, so it must be tz-aware in the *configured* zone, not UTC."""
    sched = Scheduler(bus=MockEventBus(), name="tz_check", timezone="Europe/Berlin")
    now = sched._now()
    assert now.tzinfo is sched.tz
    assert sched.tz.key == "Europe/Berlin"


@pytest.mark.asyncio
async def test_next_trigger_resolves_group_time_in_configured_timezone():
    """An alarm entered as "08:00" must resolve to 08:00 *in the configured
    zone*, with that zone's correct UTC offset - not 08:00 UTC."""
    berlin = ZoneInfo("Europe/Berlin")
    base_berlin = datetime(2023, 10, 27, 10, 0, 0, tzinfo=berlin)  # Fri, still CEST (UTC+2)
    sched = make_scheduler(MockEventBus(), now=lambda: base_berlin, timezone="Europe/Berlin")
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))
    plan = await sched.get_plan()

    trigger = sched.next_trigger(plan, base_berlin)
    assert trigger == datetime(2023, 11, 3, 8, 0, tzinfo=berlin)
    # By Nov 3 the EU has switched back to standard time (CET, UTC+1).
    assert trigger.utcoffset() == timedelta(hours=1)


@pytest.mark.asyncio
async def test_same_alarm_time_fires_at_different_utc_instant_by_timezone():
    """The concrete symptom of the bug: the same "08:00" entered by the
    user must resolve to a different real-world (UTC) instant depending on
    the scheduler's configured timezone. Before the fix, daemon.py always
    used UTC regardless of what was configured, so a machine running in a
    non-UTC zone would fire alarms hours later than the wall-clock time the
    user actually set."""
    utc_sched = make_scheduler(MockEventBus(), now=lambda: _BASE_TIME)  # implicit default: UTC
    await utc_sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))
    utc_trigger = utc_sched.next_trigger(await utc_sched.get_plan(), _BASE_TIME)

    berlin = ZoneInfo("Europe/Berlin")
    base_berlin = _BASE_TIME.astimezone(berlin)
    berlin_sched = make_scheduler(MockEventBus(), now=lambda: base_berlin, timezone="Europe/Berlin")
    await berlin_sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))
    berlin_trigger = berlin_sched.next_trigger(await berlin_sched.get_plan(), base_berlin)

    utc_instant = utc_trigger.astimezone(ZoneInfo("UTC"))
    berlin_instant = berlin_trigger.astimezone(ZoneInfo("UTC"))
    assert utc_instant != berlin_instant
    assert utc_instant - berlin_instant == timedelta(hours=1)  # CET is UTC+1 on Nov 3


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
async def test_fire_emits_full_payload_for_plan_source():
    """A trigger that is neither the active snooze nor an overridden date
    must be reported with source "plan" and the exact date/time it fired at."""
    bus = MockEventBus()
    sched = make_scheduler(bus, now=lambda: _BASE_TIME)
    await sched.create_group(frozenset([Weekday.FRIDAY]), time(8, 0))

    trigger = datetime(2023, 11, 3, 8, 0, tzinfo=ZoneInfo("UTC"))
    await sched._fire(trigger)

    assert bus.emitted == [
        ("alarm.triggered", {"date": "2023-11-03", "time": "08:00:00", "source": "plan"}),
    ]


@pytest.mark.asyncio
async def test_fire_with_snooze_source_clears_the_snooze():
    """Firing a snoozed trigger must be tagged source="snooze" and consume
    it, so the same snooze can't fire twice."""
    bus = MockEventBus()
    sched = make_scheduler(bus, now=lambda: _BASE_TIME)
    snoozed_until = await sched.snooze_alarm(minutes=9)

    await sched._fire(snoozed_until)

    plan = await sched.get_plan()
    assert plan.snooze_until is None
    assert bus.emitted[-1][1]["source"] == "snooze"


@pytest.mark.asyncio
async def test_fire_with_override_source_consumes_the_override():
    """Firing a retimed one-off override must be tagged source="override"
    and remove that override, so next week falls back to the plain group time."""
    bus = MockEventBus()
    sched = make_scheduler(bus, now=lambda: _BASE_TIME)
    reference = await sched.override_next_alarm_time(time(7, 0))

    trigger = datetime.combine(reference, time(7, 0), tzinfo=ZoneInfo("UTC"))
    await sched._fire(trigger)

    plan = await sched.get_plan()
    assert reference not in plan.overrides
    assert bus.emitted[-1][1]["source"] == "override"


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
async def test_run_loop_recomputes_when_plan_changes_mid_wait():
    """While the loop is parked waiting for a far-future trigger, adding a
    much sooner group must wake it via `_changed` and make it re-schedule
    against the new, closer trigger instead of sleeping until the old one."""
    bus = MockEventBus()
    now = datetime(2023, 10, 27, 8, 59, 59, 900000, tzinfo=ZoneInfo("UTC"))
    sched = make_scheduler(bus, now=lambda: now)
    await sched.create_group(frozenset([Weekday.SATURDAY]), time(9, 0))  # days away

    await sched.start()
    await asyncio.sleep(0.05)
    assert bus.emitted == []  # still parked on the far-future Saturday trigger

    await sched.create_group(frozenset([Weekday.FRIDAY]), time(9, 0))  # 100ms from `now`

    # `now` is a fixed lambda (doesn't advance like a real clock), so once
    # the loop fires it would immediately recompute the *same* due trigger
    # and refire forever - stop as soon as the first event lands instead of
    # sleeping a fixed duration, to avoid depending on that race.
    for _ in range(50):
        if bus.emitted:
            break
        await asyncio.sleep(0.01)
    await sched.stop()

    assert len(bus.emitted) == 1
    assert bus.emitted[0][0] == "alarm.triggered"
    assert bus.emitted[0][1]["date"] == "2023-10-27"


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