"""LED module: a status LED wired to a GPIO pin. Gives visual feedback for
bus signals from other modules, each with its own light pattern:

- `alarm.triggered` -> blinks continuously (grabs attention while ringing).
- `alarm.stopped` -> stops blinking, LED off.
- `alarm.snoozed` -> stops blinking, flashes twice (acknowledged), LED off.
- `button.flag` -> for each flag in the shared `flags.BUTTON_FLAGS`
  vocabulary, a per-flag `reaction_<flag>` setting picks both *whether* this
  LED reacts to it ("ignore" means it doesn't) and *how* ("on"/"off" turn
  the LED solid; "toggle" flips whatever the LED is currently doing;
  "flash_1".."flash_4" each flash a distinct number of times). This is the
  "linking" the settings UI exposes - which flags this LED reacts to (and
  how) is independent of which flags a given button is configured to send
  (see button's `flags` setting), the two are only connected through the
  shared vocabulary of flag names. Reactions never fire while the alarm
  blink pattern is running, so they can't fight it.

Real hardware access lives behind a driver picked via the "driver" setting -
"mock" (default, safe everywhere) or "real" (actual Raspberry Pi GPIO).
"""

from __future__ import annotations

import asyncio
from typing import Any

from alarmclock.modules.base import OutputModule
from alarmclock.modules.flags import BUTTON_FLAGS

from alarmclock.modules.led.default_settings import (
    DEFAULT_BLINK_INTERVAL_SECONDS,
    DEFAULT_FLASH_SECONDS,
    REACTIONS,
    _FLASH_TIMES,
    _DEFAULT_REACTIONS,
    DEFAULT_PIN
)


def _reaction_setting(flag: str) -> str:
    return f"reaction_{flag}"


class LEDModule(OutputModule):
    display_name = "LED"
    icon = "led"

    async def init(self) -> None:
        self._driver = self._make_driver()
        self._blink_task: asyncio.Task[None] | None = None
        self.bus.subscribe("alarm.triggered", self._on_alarm_triggered)
        self.bus.subscribe("alarm.stopped", self._on_alarm_stopped)
        self.bus.subscribe("alarm.snoozed", self._on_alarm_snoozed)
        self.bus.subscribe("button.flag", self._on_button_flag)

    def _make_driver(self) -> Any:
        if self.config.get("driver", "mock") == "real":
            from alarmclock.modules.led.real import RealLEDDriver

            return RealLEDDriver(self.pin)
        from alarmclock.modules.led.mock import MockLEDDriver

        return MockLEDDriver(self.pin)

    async def _write(self, on: bool) -> None:
        await self._driver.write(on)

    # -- blink/flash patterns ----------------------------------------------

    async def _stop_blinking(self) -> None:
        task = self._blink_task
        self._blink_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _blink_loop(self) -> None:
        interval = self.settings["blink_interval_seconds"]
        try:
            while True:
                await self.set_output(True)
                await asyncio.sleep(interval)
                await self.set_output(False)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _flash(self, times: int) -> None:
        for _ in range(times):
            await self.set_output(True)
            await asyncio.sleep(DEFAULT_FLASH_SECONDS)
            await self.set_output(False)
            await asyncio.sleep(DEFAULT_FLASH_SECONDS)

    # -- alarm reactions -----------------------------------------------------

    async def _on_alarm_triggered(self, payload: dict[str, Any]) -> None:
        await self._stop_blinking()
        self._blink_task = asyncio.create_task(self._blink_loop())

    async def _on_alarm_stopped(self, payload: dict[str, Any]) -> None:
        await self._stop_blinking()
        await self.set_output(False)

    async def _on_alarm_snoozed(self, payload: dict[str, Any]) -> None:
        await self._stop_blinking()
        await self._flash(times=2)

    # -- button reactions ------------------------------------------------------

    async def _on_button_flag(self, payload: dict[str, Any]) -> None:
        flag = payload.get("flag")
        reaction = self.settings.get(_reaction_setting(flag), "ignore")
        if reaction == "ignore":
            return
        if self._blink_task is not None:
            return  # never fight the alarm blink pattern
        await self._apply_reaction(reaction)

    async def _apply_reaction(self, reaction: str) -> None:
        if reaction == "on":
            await self.set_output(True)
        elif reaction == "off":
            await self.set_output(False)
        elif reaction == "toggle":
            await self.set_output(not self.is_on)
        else:
            await self._flash(times=_FLASH_TIMES.get(reaction, 1))

    async def on_event(self, event: str, payload: Any = None) -> None:
        if event == "alarm.triggered":
            await self._on_alarm_triggered(payload)
        elif event == "alarm.stopped":
            await self._on_alarm_stopped(payload)
        elif event == "alarm.snoozed":
            await self._on_alarm_snoozed(payload)
        elif event == "button.flag":
            await self._on_button_flag(payload)

    async def disable(self) -> None:
        await self._stop_blinking()
        await super().disable()

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(super().get_settings_schema())
        schema["pin"] = {**schema["pin"], "default": DEFAULT_PIN}
        schema["blink_interval_seconds"] = {
            "type": "float",
            "min": 0.05,
            "max": 10,
            "label": "Blink interval (s)",
            "default": DEFAULT_BLINK_INTERVAL_SECONDS,
        }
        for flag in BUTTON_FLAGS:
            schema[_reaction_setting(flag)] = {
                "type": "select",
                "options": REACTIONS,
                "label": f"Reaction: {flag}",
                "default": _DEFAULT_REACTIONS.get(flag, "ignore"),
            }
        return schema
