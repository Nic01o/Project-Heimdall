"""Sleep-plan scheduler: computes the next due wake-up and fires bus events.
Hardware-independent."""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Callable
from zoneinfo import ZoneInfo

from alarmclock.core.alarm import SleepPlan, SleepPlanGroup, Weekday
from alarmclock.core.event_bus import EventBus
from alarmclock.core.persistence import JSONStore

logger = logging.getLogger("alarmclock.core.scheduler")

NowFn = Callable[[], datetime.datetime]


class Scheduler:
    """Waits for the next due wake-up and emits `alarm.triggered` on the bus.

    Knows nothing about hardware. The clock (`now`) is injectable so the
    scheduling logic can be unit-tested without waiting on real time. If a
    `store` is given, the plan is persisted under the "sleep_plan" key on
    every change and restored from it at construction time.
    """

    def __init__(
        self,
        bus: EventBus,
        timezone: str = "UTC",
        *,
        now: NowFn | None = None,
        store: JSONStore | None = None,
    ) -> None:
        self.bus = bus
        self.tz = ZoneInfo(timezone)
        self._now = now or (lambda: datetime.datetime.now(self.tz))
        self._store = store
        self._plan = SleepPlan()
        self._task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()

        if self._store is not None:
            data = self._store.get("sleep_plan")
            if data is not None:
                self._plan = SleepPlan.from_dict(data)
        self._prune_stale_overrides()

    # -- persistence ---------------------------------------------------------

    def _persist(self) -> None:
        if self._store is not None:
            self._store.set("sleep_plan", self._plan.to_dict())

    # -- plan access -----------------------------------------------------------

    def get_plan(self) -> SleepPlan:
        self._prune_stale_overrides()
        return self._plan

    def is_day_assigned(self, day: Weekday) -> bool:
        return day in self._assigned_days()

    def _time_for_day(self, plan: SleepPlan, day: Weekday) -> datetime.time | None:
        for group in plan.groups:
            if day in group.days:
                return group.time
        return None

    def _find_group(self, group_id: str) -> SleepPlanGroup:
        for group in self._plan.groups:
            if group.id == group_id:
                return group
        raise ValueError(f"unknown sleep plan group {group_id!r}")

    def _assigned_days(self) -> set[Weekday]:
        assigned: set[Weekday] = set()
        for group in self._plan.groups:
            assigned |= group.days
        return assigned

    def _next_date_for_weekday(
        self, day: Weekday, after: datetime.datetime, reference_time: datetime.time | None
    ) -> datetime.date:
        """Next calendar date `day` occurs on, at or after `after`. Today only
        counts if `reference_time` (combined with today's date) is still
        strictly ahead of `after`; if `reference_time` is None (a no-op skip
        of an already-empty day) the exact date doesn't matter, so it just
        rolls to next week for determinism."""
        for offset in range(8):
            candidate_date = (after + datetime.timedelta(days=offset)).date()
            if Weekday(candidate_date.weekday()) != day:
                continue
            if reference_time is None:
                return candidate_date
            candidate_dt = datetime.datetime.combine(
                candidate_date, reference_time, tzinfo=after.tzinfo
            )
            if candidate_dt > after:
                return candidate_date
        raise AssertionError("unreachable: a full week was scanned")

    # -- plan management --------------------------------------------------

    def create_group(self, days: frozenset[Weekday], time: datetime.time) -> SleepPlanGroup:
        already_taken = days & self._assigned_days()
        if already_taken:
            names = ", ".join(day.name.capitalize() for day in sorted(already_taken))
            raise ValueError(f"already assigned to a sleep plan: {names}")
        group = SleepPlanGroup(days=frozenset(days), time=time)
        self._plan.groups.append(group)
        self._plan.enabled = True
        self._changed.set()
        self._persist()
        return group

    def set_group_time(self, group_id: str, time: datetime.time, *, permanent: bool) -> None:
        group = self._find_group(group_id)
        if permanent:
            group.time = time
        else:
            now = self._now()
            target_date = min(
                self._next_date_for_weekday(day, now, reference_time=time)
                for day in group.days
            )
            self._plan.overrides[target_date] = time
        self._plan.enabled = True
        self._changed.set()
        self._persist()

    def delete_group(self, group_id: str) -> None:
        group = self._find_group(group_id)
        self._plan.groups.remove(group)
        self._changed.set()
        self._persist()

    def set_day_once(self, day: Weekday, time: datetime.time | None) -> None:
        """Set (or clear) a one-time exception for a currently unassigned
        weekday, without creating a permanent group."""
        now = self._now()
        reference_time = time if time is not None else self._time_for_day(self._plan, day)
        target_date = self._next_date_for_weekday(day, now, reference_time=reference_time)
        self._plan.overrides[target_date] = time
        self._plan.enabled = True
        self._changed.set()
        self._persist()

    def set_enabled(self, enabled: bool) -> None:
        self._plan.enabled = enabled
        self._changed.set()
        self._persist()

    # -- trigger computation (pure, unit-testable) -------------------------

    def _next_weekly_occurrence(
        self, plan: SleepPlan, after: datetime.datetime
    ) -> datetime.datetime | None:
        if not plan.enabled:
            return None
        # offset 0 is today (which may already be past its time), so scan
        # through offset 7 (today + 1 week) to guarantee every weekday gets
        # a second chance at its next occurrence.
        for offset in range(8):
            candidate_date = (after + datetime.timedelta(days=offset)).date()
            if candidate_date in plan.overrides:
                day_time = plan.overrides[candidate_date]
            else:
                day_time = self._time_for_day(plan, Weekday(candidate_date.weekday()))
            if day_time is None:
                continue
            candidate_dt = datetime.datetime.combine(candidate_date, day_time, tzinfo=after.tzinfo)
            if candidate_dt > after:
                return candidate_dt
        return None

    def next_trigger(self, plan: SleepPlan, after: datetime.datetime) -> datetime.datetime | None:
        """Next tz-aware datetime `plan` fires strictly after `after`, or None."""
        candidates = [
            trigger
            for trigger in (plan.snooze_until, self._next_weekly_occurrence(plan, after))
            if trigger is not None and trigger > after
        ]
        return min(candidates) if candidates else None

    def _prune_stale_overrides(self) -> None:
        today = self._now().date()
        stale = [date for date in self._plan.overrides if date < today]
        if not stale:
            return
        for date in stale:
            del self._plan.overrides[date]
        self._persist()

    def _next_due(self) -> datetime.datetime | None:
        self._prune_stale_overrides()
        return self.next_trigger(self._plan, self._now())

    # -- run loop ------------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            self._changed.clear()
            trigger = self._next_due()
            if trigger is None:
                await self._changed.wait()
                continue
            delay = max((trigger - self._now()).total_seconds(), 0)
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=delay)
                continue  # plan changed, recompute
            except asyncio.TimeoutError:
                pass
            await self._fire(trigger)

    async def _fire(self, trigger: datetime.datetime) -> None:
        logger.info("alarm triggered for %s", trigger)
        if self._plan.snooze_until == trigger:
            source = "snooze"
            self._plan.snooze_until = None
        elif trigger.date() in self._plan.overrides:
            source = "override"
            del self._plan.overrides[trigger.date()]
        else:
            source = "plan"
        self._persist()
        await self.bus.emit(
            "alarm.triggered",
            {"date": trigger.date().isoformat(), "time": trigger.time().isoformat(), "source": source},
        )

    # -- ringing control, per the Wecker-Events TODO --------------------------

    async def stop_alarm(self) -> None:
        """Stop a currently ringing alarm (modules like sound listen for
        this). Also cancels a pending snooze, so a stopped alarm doesn't
        quietly ring again a few minutes later."""
        if self._plan.snooze_until is not None:
            self._plan.snooze_until = None
            self._changed.set()
            self._persist()
        await self.bus.emit("alarm.stopped", {})

    async def snooze_alarm(self, minutes: float = 9) -> datetime.datetime:
        """Ring again `minutes` from now."""
        snoozed_until = self._now() + datetime.timedelta(minutes=minutes)
        self._plan.snooze_until = snoozed_until
        self._changed.set()
        self._persist()
        await self.bus.emit(
            "alarm.snoozed", {"minutes": minutes, "until": snoozed_until.isoformat()}
        )
        return snoozed_until
