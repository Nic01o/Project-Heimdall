"""Alarm scheduler: computes due alarms and fires bus events. Hardware-independent."""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Callable
from zoneinfo import ZoneInfo

from alarmclock.core.alarm import Alarm, Weekday
from alarmclock.core.event_bus import EventBus
from alarmclock.core.persistence import JSONStore

logger = logging.getLogger("alarmclock.core.scheduler")

NowFn = Callable[[], datetime.datetime]


class Scheduler:
    """Waits for the next due alarm and emits `alarm.triggered` on the bus.

    Knows nothing about hardware. The clock (`now`) is injectable so the
    scheduling logic can be unit-tested without waiting on real time. If a
    `store` is given, alarms are persisted under the "alarms" key on every
    change and restored from it at construction time.
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
        self._alarms: dict[str, Alarm] = {}
        self._task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()

        if self._store is not None:
            for data in self._store.get("alarms", []):
                alarm = Alarm.from_dict(data)
                self._alarms[alarm.id] = alarm

    # -- persistence ---------------------------------------------------------

    def _persist(self) -> None:
        if self._store is not None:
            self._store.set("alarms", [alarm.to_dict() for alarm in self._alarms.values()])

    # -- alarm management --------------------------------------------------

    def add_alarm(self, alarm: Alarm) -> Alarm:
        self._alarms[alarm.id] = alarm
        self._changed.set()
        self._persist()
        return alarm

    def remove_alarm(self, alarm_id: str) -> None:
        if self._alarms.pop(alarm_id, None) is not None:
            self._changed.set()
            self._persist()

    def get_alarm(self, alarm_id: str) -> Alarm | None:
        return self._alarms.get(alarm_id)

    def list_alarms(self) -> list[Alarm]:
        return list(self._alarms.values())

    # -- trigger computation (pure, unit-testable) -------------------------

    def next_trigger(self, alarm: Alarm, after: datetime.datetime) -> datetime.datetime | None:
        """Next tz-aware datetime `alarm` fires strictly after `after`, or None."""
        if not alarm.enabled:
            return None
        if alarm.is_one_shot:
            assert alarm.at is not None
            return alarm.at if alarm.at > after else None

        assert alarm.time is not None
        candidate = after.replace(
            hour=alarm.time.hour, minute=alarm.time.minute, second=0, microsecond=0
        )
        if candidate <= after:
            candidate += datetime.timedelta(days=1)
        if not alarm.repeat:
            return candidate
        for _ in range(7):
            if Weekday(candidate.weekday()) in alarm.repeat:
                return candidate
            candidate += datetime.timedelta(days=1)
        return None  # repeat set was empty despite the check above; unreachable

    def _next_due(self) -> tuple[Alarm, datetime.datetime] | None:
        now = self._now()
        best: tuple[Alarm, datetime.datetime] | None = None
        for alarm in self._alarms.values():
            trigger = self.next_trigger(alarm, now)
            if trigger is not None and (best is None or trigger < best[1]):
                best = (alarm, trigger)
        return best

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
            due = self._next_due()
            if due is None:
                await self._changed.wait()
                continue
            alarm, trigger = due
            delay = max((trigger - self._now()).total_seconds(), 0)
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=delay)
                continue  # alarms were added/removed/changed, recompute
            except asyncio.TimeoutError:
                pass
            await self._fire(alarm)

    async def _fire(self, alarm: Alarm) -> None:
        logger.info("alarm %s triggered", alarm.id)
        if alarm.is_one_shot:
            self._alarms.pop(alarm.id, None)
            self._persist()
        await self.bus.emit("alarm.triggered", alarm.to_dict())

    # -- ringing control, per the Wecker-Events TODO --------------------------

    async def stop_alarm(self, alarm_id: str) -> None:
        """Stop a currently ringing alarm (modules like sound listen for this)."""
        await self.bus.emit("alarm.stopped", {"id": alarm_id})

    async def snooze_alarm(self, alarm_id: str, minutes: int = 9) -> Alarm:
        """Schedule a one-shot re-trigger `minutes` from now for `alarm_id`."""
        original = self._alarms.get(alarm_id)
        label = original.label if original else ""
        snoozed = self.add_alarm(
            Alarm(at=self._now() + datetime.timedelta(minutes=minutes), label=label)
        )
        await self.bus.emit(
            "alarm.snoozed", {"id": alarm_id, "minutes": minutes, "new_id": snoozed.id}
        )
        return snoozed