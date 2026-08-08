"""Sleep-plan scheduler: computes the next due wake-up and fires bus events.
Hardware-independent."""

from __future__ import annotations
import asyncio
import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo
from alarmclock.core.alarm import AlarmStatus, SleepPlan, SleepPlanGroup, Weekday
from alarmclock.core.event_bus import EventBus
from alarmclock.core.logger_wrapper import logger
from alarmclock.modules.base import Configurable

NowFn = Callable[[], datetime.datetime]

class Scheduler(Configurable):
    """Waits for the next due wake-up and emits `alarm.triggered` on the bus.

    Knows nothing about hardware. The clock (`now`) is injectable so the
    scheduling logic can be unit-tested without waiting on real time.

    Persists the sleep plan the same way a Module persists its settings: as
    a Configurable, under `[settings.<name>]` in settings.toml, with
    `get_settings_schema()` describing the same fields `SleepPlan.to_dict()`
    produces (see `alarmclock.core.alarm`) - `load_config`/`save_config`
    round-trip that dict directly. The owner (daemon.py) must
    `await scheduler.load_config(scheduler.name)` once after construction,
    exactly like it does for every other module.
    """

    def __init__(
        self,
        bus: EventBus,
        timezone: str = "UTC",
        *,
        name: str,
        settings_path: Path | None = None,
        now: NowFn | None = None,
    ) -> None:
        super().__init__(name, bus, settings_path=settings_path)
        self.tz = ZoneInfo(timezone)
        self._now = now or (lambda: datetime.datetime.now(self.tz))
        self._plan = SleepPlan()
        self._task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "enabled": {"type": "bool", "default": True, "label": "Wecker aktiv"},
            "groups": {"type": "list", "default": [], "label": "Wochenplan-Gruppen"},
            "overrides": {"type": "list", "default": {}, "label": "Einmalige Ausnahmen"},
            "snooze_until": {"type": "string", "default": "", "label": "Schlummern bis"},
        }

    async def load_config(self, instance_name: str) -> None:
        await super().load_config(instance_name)
        self._plan = SleepPlan.from_dict(self._instance_config)
        await self._prune_stale_overrides()

    async def _persist(self) -> None:
        await self.save_config(self.name, self._plan.to_dict())

    # -- plan access -----------------------------------------------------------

    async def get_plan(self) -> SleepPlan:
        await self._prune_stale_overrides()
        return self._plan

    def is_day_assigned(self, day: Weekday) -> bool:
        return day in self._assigned_days()

    def _time_for_day(self, plan: SleepPlan, day: Weekday) -> datetime.time | None:
        for group in plan.groups:
            if group.enabled and day in group.days:
                return group.time
        return None

    def _find_group(self, group_id: str) -> SleepPlanGroup:
        for group in self._plan.groups:
            if group.id == group_id:
                return group
        raise ValueError(f"unknown sleep plan group {group_id!r}")

    def day_owner(self) -> dict[Weekday, str]:
        """Weekday -> id of the enabled group currently claiming it. A
        disabled (hidden) group's days aren't in here - they're released so
        another group can claim them."""
        owner: dict[Weekday, str] = {}
        for group in self._plan.groups:
            if not group.enabled:
                continue
            for day in group.days:
                owner[day] = group.id
        return owner

    def _assigned_days(self) -> set[Weekday]:
        return set(self.day_owner())

    def blocking_days(self, group: SleepPlanGroup) -> list[Weekday]:
        """Days of a disabled `group` now owned by another enabled group, in
        weekday order - the reason it can't be re-enabled yet. Empty for an
        already-enabled group."""
        if group.enabled:
            return []
        owner = self.day_owner()
        return sorted(group.days & owner.keys(), key=lambda d: d.value)

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

    async def create_group(self, days: frozenset[Weekday], time: datetime.time) -> SleepPlanGroup:
        already_taken = days & self._assigned_days()
        if already_taken:
            names = ", ".join(day.name.capitalize() for day in sorted(already_taken))
            raise ValueError(f"already assigned to a sleep plan: {names}")
        group = SleepPlanGroup(days=frozenset(days), time=time)
        self._plan.groups.append(group)
        self._plan.enabled = True
        self._changed.set()
        await self._persist()
        return group

    async def set_group_time(self, group_id: str, time: datetime.time, *, permanent: bool) -> None:
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
        await self._persist()

    async def update_group(self, group_id: str, days: frozenset[Weekday], time: datetime.time) -> None:
        """Permanently replace a group's weekdays and time (used by the
        edit-in-place row in the UI). Days already owned by this same group
        are exempt from the conflict check."""
        group = self._find_group(group_id)
        if not days:
            raise ValueError("SleepPlanGroup needs at least one weekday")
        conflicting = (days - group.days) & self._assigned_days()
        if conflicting:
            names = ", ".join(day.name.capitalize() for day in sorted(conflicting))
            raise ValueError(f"already assigned to a sleep plan: {names}")
        group.days = frozenset(days)
        group.time = time
        self._changed.set()
        await self._persist()

    async def set_group_enabled(self, group_id: str, enabled: bool) -> None:
        """Pause or resume a single group without deleting it or releasing
        its weekdays. Disabling a group frees its weekdays for other groups;
        re-enabling it re-claims them, so it is rejected if another enabled
        group has since taken one of those days."""
        group = self._find_group(group_id)
        if enabled and not group.enabled:
            conflicting = group.days & self._assigned_days()
            if conflicting:
                names = ", ".join(day.name.capitalize() for day in sorted(conflicting))
                raise ValueError(f"already assigned to a sleep plan: {names}")
        group.enabled = enabled
        self._changed.set()
        await self._persist()

    async def toggle_group_enabled(self, group_id: str) -> bool:
        group = self._find_group(group_id)
        await self.set_group_enabled(group_id, not group.enabled)
        return group.enabled

    async def delete_group(self, group_id: str) -> None:
        group = self._find_group(group_id)
        self._plan.groups.remove(group)
        self._changed.set()
        await self._persist()

    async def set_day_once(self, day: Weekday, time: datetime.time | None) -> None:
        """Set (or clear) a one-time exception for a currently unassigned
        weekday, without creating a permanent group."""
        now = self._now()
        reference_time = time if time is not None else self._time_for_day(self._plan, day)
        target_date = self._next_date_for_weekday(day, now, reference_time=reference_time)
        self._plan.overrides[target_date] = time
        self._plan.enabled = True
        self._changed.set()
        await self._persist()

    async def set_enabled(self, enabled: bool) -> None:
        self._plan.enabled = enabled
        self._changed.set()
        await self._persist()

    # -- master toggle / "next alarm" override -----------------------------
    # The web UI's top-level toggle only ever acts on *one* occurrence: the
    # single upcoming date implied by the recurring groups (ignoring any
    # override already sitting on it). That date is the "reference date" -
    # skipping it sets overrides[date] = None, retiming it sets
    # overrides[date] = <time>, and both share the same dict entry, so
    # setting a time always supersedes an active skip and vice versa.

    def _next_reference_date(self, after: datetime.datetime) -> datetime.date | None:
        for offset in range(8):
            candidate_date = (after + datetime.timedelta(days=offset)).date()
            day_time = self._time_for_day(self._plan, Weekday(candidate_date.weekday()))
            if day_time is None:
                continue
            candidate_dt = datetime.datetime.combine(candidate_date, day_time, tzinfo=after.tzinfo)
            if candidate_dt > after:
                return candidate_date
        return None

    def get_alarm_reference_date(self, now: datetime.datetime | None = None) -> datetime.date | None:
        """The date the master toggle (skip) and the override display/clear
        act on: the earlier of the next recurring-group occurrence and the
        soonest future date already carrying a standalone override. The
        override half matters once `override_next_alarm_time` has placed an
        override on a day no group covers (e.g. plan only has Monday, but
        an override sits on tomorrow) - without it, this would keep
        pointing at the far-off group day and the override would be
        invisible to the UI and to `clear_alarm_override`/`skip_next_alarm`."""
        now = now or self._now()
        group_reference = self._next_reference_date(now)
        future_overrides = [date for date in self._plan.overrides if date >= now.date()]
        override_reference = min(future_overrides) if future_overrides else None
        candidates = [d for d in (group_reference, override_reference) if d is not None]
        return min(candidates) if candidates else None

    def is_next_alarm_skipped(self, now: datetime.datetime | None = None) -> bool:
        now = now or self._now()
        reference = self.get_alarm_reference_date(now)
        return (
            reference is not None
            and reference in self._plan.overrides
            and self._plan.overrides[reference] is None
        )

    def get_alarm_status(self, now: datetime.datetime | None = None) -> AlarmStatus:
        """Bundle the reference date, its skip/override state, which group
        it belongs to, and the actual next trigger - everything the web
        UI's index page needs about "what's the next alarm doing"."""
        now = now or self._now()
        plan = self._plan
        reference_date = self.get_alarm_reference_date(now)
        is_skipped = self.is_next_alarm_skipped(now)

        override_time: datetime.time | None = None
        if reference_date is not None and not is_skipped and reference_date in plan.overrides:
            override_time = plan.overrides[reference_date]

        affected_group_id: str | None = None
        if reference_date is not None:
            affected_weekday = Weekday(reference_date.weekday())
            affected_group_id = next(
                (
                    group.id
                    for group in plan.groups
                    if group.enabled and affected_weekday in group.days
                ),
                None,
            )

        trigger = self.next_trigger(plan, now)
        return AlarmStatus(reference_date, is_skipped, override_time, affected_group_id, trigger)

    async def skip_next_alarm(self, now: datetime.datetime | None = None) -> bool:
        """Skip the single next occurrence. Returns False (no-op) if there's
        nothing scheduled to skip."""
        now = now or self._now()
        reference = self.get_alarm_reference_date(now)
        if reference is None:
            return False
        self._plan.overrides[reference] = None
        self._changed.set()
        await self._persist()
        return True

    async def clear_alarm_override(self, now: datetime.datetime | None = None) -> bool:
        """Clear whatever override (skip or retimed) currently sits on the
        reference date, reverting to the plain recurring schedule."""
        now = now or self._now()
        reference = self.get_alarm_reference_date(now)
        if reference is None or reference not in self._plan.overrides:
            return False
        del self._plan.overrides[reference]
        self._changed.set()
        await self._persist()
        return True

    def _immediate_reference_date(self, now: datetime.datetime) -> datetime.date:
        """The single calendar date `override_next_alarm_time` acts on:
        today, unless today's effective wake-up (its own override if any,
        else its recurring group time) has already happened - then
        tomorrow. A day with nothing scheduled counts as "not yet
        happened", so it's still fair game for today.

        Deliberately never looks further than tomorrow, unlike
        `get_alarm_reference_date` (which skip_next_alarm uses and which
        may point at a group day a week out): the override always overrides
        the *next* morning, whether or not the plan says anything about it,
        not the next day the recurring plan happens to mention."""
        today = now.date()
        effective_time = self._plan.overrides.get(
            today, self._time_for_day(self._plan, Weekday(today.weekday()))
        )
        if effective_time is not None:
            today_dt = datetime.datetime.combine(today, effective_time, tzinfo=now.tzinfo)
            if today_dt <= now:
                return today + datetime.timedelta(days=1)
        return today

    async def override_next_alarm_time(
        self, time: datetime.time, now: datetime.datetime | None = None
    ) -> datetime.date:
        """Replace tomorrow's wake-up with `time` for this one instance only
        - or today's, if today's own alarm hasn't rung yet. Applies
        regardless of what the recurring plan says for that date, so a
        one-off alarm can be set even on a day the plan leaves empty."""
        now = now or self._now()
        reference = self._immediate_reference_date(now)
        self._plan.overrides[reference] = time
        self._changed.set()
        await self._persist()
        return reference

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

    async def _prune_stale_overrides(self) -> None:
        today = self._now().date()
        stale = [date for date in self._plan.overrides if date < today]
        if not stale:
            return
        for date in stale:
            del self._plan.overrides[date]
        await self._persist()

    async def _next_due(self) -> datetime.datetime | None:
        await self._prune_stale_overrides()
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
            trigger = await self._next_due()
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
        await self._persist()
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
            await self._persist()
        await self.bus.emit("alarm.stopped", {})

    async def snooze_alarm(self, minutes: float = 9) -> datetime.datetime:
        """Ring again `minutes` from now."""
        snoozed_until = self._now() + datetime.timedelta(minutes=minutes)
        self._plan.snooze_until = snoozed_until
        self._changed.set()
        await self._persist()
        await self.bus.emit(
            "alarm.snoozed", {"minutes": minutes, "until": snoozed_until.isoformat()}
        )
        return snoozed_until