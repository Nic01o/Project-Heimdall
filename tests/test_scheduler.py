"""Unit tests for the scheduler. Pure logic is tested with an injected clock
(no real waiting); only the run-loop tests wait on a short real delay.
"""

import asyncio
import datetime
import time

import pytest
from zoneinfo import ZoneInfo

from alarmclock.core.alarm import SleepPlan, SleepPlanGroup, Weekday
from alarmclock.core.event_bus import EventBus
from alarmclock.core.persistence import JSONStore
from alarmclock.core.scheduler import Scheduler

TZ = ZoneInfo("UTC")


def dt(y, m, d, h, mi):
    return datetime.datetime(y, m, d, h, mi, tzinfo=TZ)


def make_scheduler(*, now=None, store=None):
    return Scheduler(EventBus(), timezone="UTC", now=now, store=store)


def make_moving_clock(start_fake: datetime.datetime):
    """A `now` callable whose fake time advances in lockstep with real
    (monotonic) time, starting at `start_fake`. Lets run-loop tests cross a
    trigger threshold for real via a short `asyncio.sleep`, so the scheduler
    naturally rolls over to the next occurrence afterwards - unlike a frozen
    clock, which would keep re-triggering the same instant forever."""
    start_real = time.monotonic()

    def moving_now() -> datetime.datetime:
        return start_fake + datetime.timedelta(seconds=time.monotonic() - start_real)

    return moving_now


# -- SleepPlanGroup / SleepPlan model -----------------------------------------


def test_sleep_plan_group_requires_at_least_one_day():
    with pytest.raises(ValueError):
        SleepPlanGroup(days=frozenset(), time=datetime.time(7, 0))


def test_sleep_plan_round_trips_to_dict():
    plan = SleepPlan(
        groups=[SleepPlanGroup(days=frozenset({Weekday.MONDAY, Weekday.TUESDAY}), time=datetime.time(6, 30))],
        overrides={
            datetime.date(2026, 7, 27): datetime.time(9, 0),
            datetime.date(2026, 7, 29): None,
        },
        enabled=False,
        snooze_until=dt(2026, 7, 20, 7, 9),
    )
    restored = SleepPlan.from_dict(plan.to_dict())
    assert restored == plan


# -- next_trigger: weekly/override merge --------------------------------------


def test_next_trigger_recurring_same_day():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 7, 0)  # Monday
    plan = SleepPlan(groups=[SleepPlanGroup(days=frozenset({Weekday.MONDAY}), time=datetime.time(7, 30))])
    assert sched.next_trigger(plan, now) == dt(2026, 7, 20, 7, 30)


def test_next_trigger_rolls_to_next_matching_weekday():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 8, 0)  # Monday, 7:30 already passed
    plan = SleepPlan(groups=[SleepPlanGroup(days=frozenset({Weekday.MONDAY}), time=datetime.time(7, 30))])
    assert sched.next_trigger(plan, now) == dt(2026, 7, 27, 7, 30)


def test_next_trigger_disabled_plan_returns_none():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 7, 0)
    plan = SleepPlan(
        groups=[SleepPlanGroup(days=frozenset({Weekday.MONDAY}), time=datetime.time(7, 30))],
        enabled=False,
    )
    assert sched.next_trigger(plan, now) is None


def test_next_trigger_unassigned_day_has_no_trigger():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 7, 0)  # Monday
    plan = SleepPlan(groups=[SleepPlanGroup(days=frozenset({Weekday.TUESDAY}), time=datetime.time(7, 30))])
    assert sched.next_trigger(plan, now) == dt(2026, 7, 21, 7, 30)


def test_next_trigger_override_replaces_that_dates_time():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 6, 0)  # Monday
    plan = SleepPlan(
        groups=[SleepPlanGroup(days=frozenset({Weekday.MONDAY}), time=datetime.time(7, 30))],
        overrides={datetime.date(2026, 7, 20): datetime.time(9, 0)},
    )
    assert sched.next_trigger(plan, now) == dt(2026, 7, 20, 9, 0)


def test_next_trigger_override_skip_falls_through_to_next_week():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 6, 0)  # Monday
    plan = SleepPlan(
        groups=[SleepPlanGroup(days=frozenset({Weekday.MONDAY}), time=datetime.time(7, 30))],
        overrides={datetime.date(2026, 7, 20): None},
    )
    assert sched.next_trigger(plan, now) == dt(2026, 7, 27, 7, 30)


def test_next_trigger_override_on_otherwise_unassigned_day():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 6, 0)  # Monday
    plan = SleepPlan(overrides={datetime.date(2026, 7, 20): datetime.time(8, 0)})
    assert sched.next_trigger(plan, now) == dt(2026, 7, 20, 8, 0)


def test_next_trigger_snoozed_wins_when_sooner():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 6, 0)
    plan = SleepPlan(
        groups=[SleepPlanGroup(days=frozenset({Weekday.MONDAY}), time=datetime.time(7, 30))],
        snooze_until=dt(2026, 7, 20, 6, 9),
    )
    assert sched.next_trigger(plan, now) == dt(2026, 7, 20, 6, 9)


def test_next_trigger_weekly_wins_when_sooner_than_snooze():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 7, 20)
    plan = SleepPlan(
        groups=[SleepPlanGroup(days=frozenset({Weekday.MONDAY}), time=datetime.time(7, 30))],
        snooze_until=dt(2026, 7, 20, 9, 0),
    )
    assert sched.next_trigger(plan, now) == dt(2026, 7, 20, 7, 30)


def test_next_trigger_disabled_plan_ignores_override_too():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 6, 0)
    plan = SleepPlan(
        overrides={datetime.date(2026, 7, 20): datetime.time(8, 0)},
        enabled=False,
    )
    assert sched.next_trigger(plan, now) is None


def test_next_trigger_disabled_plan_still_honors_snooze():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 6, 0)
    plan = SleepPlan(snooze_until=dt(2026, 7, 20, 6, 9), enabled=False)
    assert sched.next_trigger(plan, now) == dt(2026, 7, 20, 6, 9)


# -- create_group ---------------------------------------------------------


def test_create_group_success():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY, Weekday.TUESDAY}), datetime.time(6, 30))
    assert group in sched.get_plan().groups
    assert sched.get_plan().enabled is True


def test_create_group_rejects_already_assigned_day():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    with pytest.raises(ValueError):
        sched.create_group(frozenset({Weekday.MONDAY, Weekday.TUESDAY}), datetime.time(7, 0))
    # the rejected call must not have partially applied (Tuesday still free)
    assert Weekday.TUESDAY not in sched._assigned_days()


# -- set_group_time --------------------------------------------------------


def test_set_group_time_permanent_changes_all_member_days():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY, Weekday.TUESDAY}), datetime.time(6, 30))
    sched.set_group_time(group.id, datetime.time(7, 0), permanent=True)
    assert group.time == datetime.time(7, 0)
    assert sched.get_plan().overrides == {}


def test_set_group_time_next_only_overrides_earliest_member_day_only():
    now = dt(2026, 7, 20, 6, 0)  # Monday
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY, Weekday.TUESDAY}), datetime.time(6, 30))
    sched.set_group_time(group.id, datetime.time(9, 0), permanent=False)
    assert group.time == datetime.time(6, 30)  # permanent time untouched
    assert sched.get_plan().overrides == {datetime.date(2026, 7, 20): datetime.time(9, 0)}


def test_set_group_time_next_only_reverts_after_it_would_have_fired():
    now = dt(2026, 7, 20, 6, 0)  # Monday
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.set_group_time(group.id, datetime.time(9, 0), permanent=False)
    later = dt(2026, 7, 20, 9, 30)  # after the one-time 9:00 would have fired
    assert sched.next_trigger(sched.get_plan(), later) == dt(2026, 7, 27, 6, 30)


def test_set_group_time_reactivates_disabled_plan():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.set_enabled(False)
    sched.set_group_time(group.id, datetime.time(7, 0), permanent=True)
    assert sched.get_plan().enabled is True


def test_set_group_time_next_only_reactivates_disabled_plan():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.set_enabled(False)
    sched.set_group_time(group.id, datetime.time(9, 0), permanent=False)
    assert sched.get_plan().enabled is True


def test_set_group_time_unknown_group_raises():
    sched = make_scheduler()
    with pytest.raises(ValueError):
        sched.set_group_time("nope", datetime.time(7, 0), permanent=True)


# -- delete_group -----------------------------------------------------------


def test_delete_group_frees_its_days():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY, Weekday.TUESDAY}), datetime.time(6, 30))
    sched.delete_group(group.id)
    assert sched.get_plan().groups == []
    assert sched._assigned_days() == set()


def test_delete_group_leaves_overrides_intact():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.set_group_time(group.id, datetime.time(9, 0), permanent=False)
    sched.delete_group(group.id)
    assert sched.get_plan().overrides == {datetime.date(2026, 7, 20): datetime.time(9, 0)}


# -- set_group_enabled / toggle_group_enabled -------------------------------


def test_disabling_group_frees_its_days():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY, Weekday.TUESDAY}), datetime.time(6, 30))
    sched.set_group_enabled(group.id, False)
    assert sched._assigned_days() == set()


def test_disabled_groups_days_can_be_claimed_by_a_new_group():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.set_group_enabled(group.id, False)
    other = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))
    assert other in sched.get_plan().groups
    assert Weekday.MONDAY in sched._assigned_days()


def test_reenabling_group_rejects_conflict_with_day_since_claimed():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.set_group_enabled(group.id, False)
    sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))
    with pytest.raises(ValueError):
        sched.set_group_enabled(group.id, True)
    assert group.enabled is False


def test_toggle_group_enabled_rejects_conflict_with_day_since_claimed():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.toggle_group_enabled(group.id)  # disable, frees Monday
    sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))
    with pytest.raises(ValueError):
        sched.toggle_group_enabled(group.id)  # re-enable attempt
    assert group.enabled is False


def test_reenabling_group_with_no_conflict_succeeds():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.set_group_enabled(group.id, False)
    sched.set_group_enabled(group.id, True)
    assert group.enabled is True
    assert Weekday.MONDAY in sched._assigned_days()


# -- set_day_once (unassigned days) -----------------------------------------


def test_set_day_once_on_free_day_does_not_create_a_group():
    now = dt(2026, 7, 20, 6, 0)  # Monday
    sched = make_scheduler(now=lambda: now)
    sched.set_day_once(Weekday.TUESDAY, datetime.time(8, 0))
    assert sched.get_plan().groups == []
    assert sched.get_plan().overrides == {datetime.date(2026, 7, 21): datetime.time(8, 0)}


def test_set_day_once_skip_on_already_empty_day_is_a_noop_trigger_wise():
    now = dt(2026, 7, 20, 6, 0)  # Monday
    sched = make_scheduler(now=lambda: now)
    sched.set_day_once(Weekday.TUESDAY, None)
    assert sched.next_trigger(sched.get_plan(), now) is None


def test_set_day_once_reactivates_disabled_plan():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    sched.set_enabled(False)
    sched.set_day_once(Weekday.TUESDAY, datetime.time(8, 0))
    assert sched.get_plan().enabled is True


# -- /plan/days shortcut behavior (permanent = create a solo group) --------


def test_create_group_of_one_is_equivalent_to_a_permanent_day_shortcut():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.WEDNESDAY}), datetime.time(7, 0))
    assert group.days == frozenset({Weekday.WEDNESDAY})
    assert sched._time_for_day(sched.get_plan(), Weekday.WEDNESDAY) == datetime.time(7, 0)


# -- set_enabled --------------------------------------------------------


def test_set_enabled_does_not_touch_groups_or_overrides():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.set_group_time(group.id, datetime.time(9, 0), permanent=False)
    sched.set_enabled(False)
    assert sched.get_plan().enabled is False
    assert group.time == datetime.time(6, 30)
    assert sched.get_plan().overrides == {datetime.date(2026, 7, 20): datetime.time(9, 0)}


# -- overlapping-edit isolation -----------------------------------------


def test_permanent_edit_on_one_group_leaves_pending_override_on_another_untouched():
    now = dt(2026, 7, 20, 6, 0)  # Monday
    sched = make_scheduler(now=lambda: now)
    mon = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    wed = sched.create_group(frozenset({Weekday.WEDNESDAY}), datetime.time(6, 45))
    sched.set_group_time(wed.id, datetime.time(9, 0), permanent=False)
    sched.set_group_time(mon.id, datetime.time(7, 15), permanent=True)
    assert sched.get_plan().overrides == {datetime.date(2026, 7, 22): datetime.time(9, 0)}
    assert mon.time == datetime.time(7, 15)


def test_setting_override_twice_on_same_date_overwrites():
    now = dt(2026, 7, 20, 6, 0)  # Monday
    sched = make_scheduler(now=lambda: now)
    group = sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    sched.set_group_time(group.id, datetime.time(9, 0), permanent=False)
    sched.set_group_time(group.id, datetime.time(10, 0), permanent=False)
    assert sched.get_plan().overrides == {datetime.date(2026, 7, 20): datetime.time(10, 0)}


# -- stale override pruning -----------------------------------------------


def test_stale_overrides_are_pruned_on_next_due_check():
    now = dt(2026, 7, 20, 6, 0)
    sched = make_scheduler(now=lambda: now)
    sched.get_plan().overrides[datetime.date(2026, 7, 1)] = datetime.time(7, 0)
    sched._prune_stale_overrides()
    assert sched.get_plan().overrides == {}


def test_stale_overrides_pruned_on_construction_from_store(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    plan = SleepPlan(overrides={datetime.date(2020, 1, 1): datetime.time(7, 0)})
    store.set("sleep_plan", plan.to_dict())
    now = dt(2026, 7, 20, 6, 0)
    sched = Scheduler(EventBus(), timezone="UTC", now=lambda: now, store=store)
    assert sched.get_plan().overrides == {}


# -- stop / snooze --------------------------------------------------------


def test_snooze_sets_snooze_until_and_emits_event():
    async def scenario():
        now = dt(2026, 7, 20, 7, 0)
        sched = make_scheduler(now=lambda: now)
        events = []

        async def on_snoozed(payload):
            events.append(payload)

        sched.bus.subscribe("alarm.snoozed", on_snoozed)
        result = await sched.snooze_alarm(minutes=9)
        assert result == dt(2026, 7, 20, 7, 9)
        assert sched.get_plan().snooze_until == dt(2026, 7, 20, 7, 9)
        assert events and events[0]["minutes"] == 9

    asyncio.run(scenario())


def test_stop_clears_pending_snooze():
    async def scenario():
        now = dt(2026, 7, 20, 7, 0)
        sched = make_scheduler(now=lambda: now)
        await sched.snooze_alarm(minutes=9)
        await sched.stop_alarm()
        assert sched.get_plan().snooze_until is None

    asyncio.run(scenario())


def test_stop_emits_event_even_without_pending_snooze():
    async def scenario():
        sched = make_scheduler()
        events = []

        async def on_stopped(payload):
            events.append(payload)

        sched.bus.subscribe("alarm.stopped", on_stopped)
        await sched.stop_alarm()
        assert events == [{}]

    asyncio.run(scenario())


# -- persistence ------------------------------------------------------------


def test_persists_plan_to_store_on_create_group(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    now = dt(2026, 7, 20, 6, 0)
    sched = Scheduler(EventBus(), timezone="UTC", now=lambda: now, store=store)
    sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))
    assert store.get("sleep_plan")["groups"][0]["days"] == [0]


def test_persists_plan_to_store_on_set_enabled(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    sched = Scheduler(EventBus(), timezone="UTC", store=store)
    sched.set_enabled(False)
    assert store.get("sleep_plan")["enabled"] is False


def test_persists_plan_to_store_on_snooze(tmp_path):
    async def scenario():
        store = JSONStore(tmp_path / "state.json")
        now = dt(2026, 7, 20, 6, 0)
        sched = Scheduler(EventBus(), timezone="UTC", now=lambda: now, store=store)
        await sched.snooze_alarm(minutes=9)
        assert store.get("sleep_plan")["snooze_until"] == dt(2026, 7, 20, 6, 9).isoformat()

    asyncio.run(scenario())


def test_loads_persisted_plan_on_construction(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    plan = SleepPlan(groups=[SleepPlanGroup(days=frozenset({Weekday.MONDAY}), time=datetime.time(6, 30))])
    store.set("sleep_plan", plan.to_dict())
    sched = Scheduler(EventBus(), timezone="UTC", store=store)
    assert sched.get_plan().groups[0].days == frozenset({Weekday.MONDAY})


# -- run loop ------------------------------------------------------------


def test_run_loop_fires_alarm_and_reverts_override_afterwards():
    async def scenario():
        fire_at = dt(2026, 7, 20, 7, 0)
        moving_now = make_moving_clock(fire_at - datetime.timedelta(milliseconds=100))

        sched = Scheduler(EventBus(), timezone="UTC", now=moving_now)
        sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0, 0))

        events = []

        async def on_triggered(payload):
            events.append(payload)

        sched.bus.subscribe("alarm.triggered", on_triggered)

        await sched.start()
        await asyncio.sleep(0.3)
        await sched.stop()

        assert len(events) == 1
        assert events[0]["source"] == "plan"

        later = dt(2026, 7, 20, 7, 30)
        assert sched.next_trigger(sched.get_plan(), later) == dt(2026, 7, 27, 7, 0)

    asyncio.run(scenario())


def test_run_loop_picks_up_newly_created_group_while_waiting():
    async def scenario():
        moving_now = make_moving_clock(
            datetime.datetime(2026, 7, 20, 6, 59, 59, 900000, tzinfo=TZ)
        )
        sched = Scheduler(EventBus(), timezone="UTC", now=moving_now)

        events = []

        async def on_triggered(payload):
            events.append(payload)

        sched.bus.subscribe("alarm.triggered", on_triggered)

        await sched.start()
        await asyncio.sleep(0.05)  # let the loop start waiting (nothing scheduled yet)

        sched.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))

        await asyncio.sleep(0.3)
        await sched.stop()

        assert len(events) == 1

    asyncio.run(scenario())
