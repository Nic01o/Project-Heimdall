"""Unit tests for the scheduler. Pure logic is tested with an injected clock
(no real waiting); only the run-loop test waits on a short real delay.
"""

import asyncio
import datetime

import pytest
from zoneinfo import ZoneInfo

from alarmclock.core.alarm import Alarm, Weekday
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler

TZ = ZoneInfo("UTC")


def dt(y, m, d, h, mi):
    return datetime.datetime(y, m, d, h, mi, tzinfo=TZ)


def make_scheduler():
    return Scheduler(EventBus(), timezone="UTC")


def test_next_trigger_one_shot_future():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 7, 0)
    alarm = Alarm(at=now + datetime.timedelta(minutes=5))
    assert sched.next_trigger(alarm, now) == alarm.at


def test_next_trigger_one_shot_past_returns_none():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 7, 0)
    alarm = Alarm(at=now - datetime.timedelta(minutes=5))
    assert sched.next_trigger(alarm, now) is None


def test_next_trigger_disabled_returns_none():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 7, 0)
    alarm = Alarm(time=datetime.time(7, 30), enabled=False)
    assert sched.next_trigger(alarm, now) is None


def test_next_trigger_recurring_same_day():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 7, 0)  # Monday
    alarm = Alarm(time=datetime.time(7, 30), repeat=frozenset({Weekday.MONDAY}))
    assert sched.next_trigger(alarm, now) == dt(2026, 7, 20, 7, 30)


def test_next_trigger_recurring_rolls_to_next_matching_weekday():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 8, 0)  # Monday, already past 07:30
    alarm = Alarm(time=datetime.time(7, 30), repeat=frozenset({Weekday.MONDAY, Weekday.WEDNESDAY}))
    assert sched.next_trigger(alarm, now) == dt(2026, 7, 22, 7, 30)  # next Wednesday


def test_next_trigger_no_repeat_rolls_to_tomorrow():
    sched = make_scheduler()
    now = dt(2026, 7, 20, 8, 0)
    alarm = Alarm(time=datetime.time(7, 30))
    assert sched.next_trigger(alarm, now) == dt(2026, 7, 21, 7, 30)


def test_alarm_requires_exactly_one_of_time_or_at():
    with pytest.raises(ValueError):
        Alarm()
    with pytest.raises(ValueError):
        Alarm(time=datetime.time(7, 0), at=dt(2026, 7, 20, 7, 0))


def test_snooze_creates_new_one_shot_alarm():
    async def scenario():
        now = dt(2026, 7, 20, 7, 0)
        bus = EventBus()
        snoozed_events = []

        async def on_snoozed(payload):
            snoozed_events.append(payload)

        bus.subscribe("alarm.snoozed", on_snoozed)
        sched = Scheduler(bus, timezone="UTC", now=lambda: now)
        original = sched.add_alarm(Alarm(time=datetime.time(7, 0), label="wake up"))

        new_alarm = await sched.snooze_alarm(original.id, minutes=9)

        assert new_alarm.is_one_shot
        assert new_alarm.at == now + datetime.timedelta(minutes=9)
        assert new_alarm.label == "wake up"
        assert sched.get_alarm(new_alarm.id) is new_alarm
        assert snoozed_events == [{"id": original.id, "minutes": 9, "new_id": new_alarm.id}]

    asyncio.run(scenario())


def test_run_loop_fires_alarm_and_removes_one_shot():
    async def scenario():
        bus = EventBus()
        fired = []

        async def on_alarm(payload):
            fired.append(payload)

        bus.subscribe("alarm.triggered", on_alarm)
        sched = Scheduler(bus, timezone="UTC")
        alarm = sched.add_alarm(
            Alarm(at=datetime.datetime.now(TZ) + datetime.timedelta(milliseconds=50))
        )

        await sched.start()
        await asyncio.sleep(0.2)
        await sched.stop()

        assert len(fired) == 1
        assert fired[0]["id"] == alarm.id
        assert sched.get_alarm(alarm.id) is None  # one-shot removed after firing

    asyncio.run(scenario())


def test_run_loop_picks_up_newly_added_alarm_while_waiting():
    async def scenario():
        bus = EventBus()
        fired = []

        async def on_alarm(payload):
            fired.append(payload)

        bus.subscribe("alarm.triggered", on_alarm)
        sched = Scheduler(bus, timezone="UTC")

        await sched.start()
        await asyncio.sleep(0.05)  # loop is idle-waiting, no alarms yet
        alarm = sched.add_alarm(
            Alarm(at=datetime.datetime.now(TZ) + datetime.timedelta(milliseconds=50))
        )
        await asyncio.sleep(0.2)
        await sched.stop()

        assert len(fired) == 1
        assert fired[0]["id"] == alarm.id

    asyncio.run(scenario())