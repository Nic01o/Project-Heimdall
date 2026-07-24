"""LED module: a status LED wired to a GPIO pin. Gives visual feedback for
bus signals from other modules, each with its own light pattern:

- `alarm.triggered` -> blinks continuously (grabs attention while ringing).
- `alarm.stopped` -> stops blinking, LED off.
- `alarm.snoozed` -> stops blinking, flashes twice (acknowledged), LED off.
- `button.flag` -> reacts to whichever of the shared `flags.BUTTON_FLAGS`
  it's configured (via the `reacts_to` setting) to listen for, differently
  per flag: "press"/"release" turn the LED solid on/off (so a press reads
  as "seen" even before whatever it triggers happens); "click"/
  "double_click"/"multi_click"/"long_press" each flash a distinct number of
  times. This is the "linking" the settings UI exposes - which flags this
  LED reacts to is independent of which flags a given button is configured
  to send (see button's `flags` setting), the two are only connected
  through the shared vocabulary of flag names. Reactions never fire while
  the alarm blink pattern is running, so they can't fight it.

Real hardware access lives behind a driver picked via the "driver" setting -
"mock" (default, safe everywhere) or "real" (actual Raspberry Pi GPIO).
"""

from __future__ import annotations

import asyncio
from typing import Any

from alarmclock.modules.base import OutputModule
from alarmclock.modules.flags import BUTTON_FLAGS

DEFAULT_BLINK_INTERVAL_SECONDS = 0.3
DEFAULT_FLASH_SECONDS = 0.15

# How many times to flash for each non-hold flag - distinct counts so each
# gesture reads as visibly different.
_FLASH_COUNTS = {
    "click": 1,
    "double_click": 2,
    "multi_click": 3,
    "long_press": 4,
}


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
        if self.settings.get("driver", "mock") == "real":
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
        interval = self.settings.get("blink_interval_seconds", DEFAULT_BLINK_INTERVAL_SECONDS)
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
        if flag not in self.settings.get("reacts_to", BUTTON_FLAGS):
            return
        if self._blink_task is not None:
            return  # never fight the alarm blink pattern
        if flag == "press":
            await self.set_output(True)
        elif flag == "release":
            await self.set_output(False)
        else:
            await self._flash(times=_FLASH_COUNTS.get(flag, 1))

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

    async def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(await super().get_settings_schema())
        schema["driver"] = {
            "type": "select",
            "options": ["mock", "real"],
            "label": "Driver",
        }
        schema["blink_interval_seconds"] = {
            "type": "float",
            "min": 0.05,
            "max": 10,
            "label": "Blink interval (s)",
        }
        schema["reacts_to"] = {
            "type": "multiselect",
            "options": BUTTON_FLAGS,
            "label": "React to flags",
        }
        return schema
