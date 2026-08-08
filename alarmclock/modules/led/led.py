"""LED module: a status LED wired to a GPIO pin. Gives visual feedback for
bus signals from other modules.

Which flags this LED reacts to (and how) is entirely driven by the generic
`reaction_<flag>` settings from `OutputModule` (see
`alarmclock.modules.flags.FLAG_SOURCES` for the full list of flags, covering
both alarm lifecycle events and button gestures) - "ignore" means it doesn't
react at all; "on"/"off" turn the LED solid; "toggle" flips whatever the LED
is currently doing; "flash_1".."flash_4" each flash a distinct number of
times; "blink" blinks continuously until another flag's reaction replaces it
(alarm_triggered defaults to this). This is the "linking" the settings UI
exposes - which flags this LED reacts to is independent of which flags a
given button is configured to send (see button's `send_<flag>` settings),
the two are only connected through the shared vocabulary of flag names.

Real hardware access lives behind a driver picked via the "driver" setting -
"mock" (default, safe everywhere) or "real" (actual Raspberry Pi GPIO).
"""

from __future__ import annotations
import asyncio
from typing import Any
from alarmclock.modules.base import OutputModule
from alarmclock.modules.led.mock import MockLEDDriver

# Default pin for LED module
DEFAULT_PIN = 22


class LEDModule(OutputModule):
    display_name = "LED"
    icon = "led"

    # LEDs can do more than a plain on/off - extend OutputModule's base
    # reaction vocabulary with flash patterns and continuous blinking.
    reactions = OutputModule.reactions + ["blink", "flash_1", "flash_2", "flash_3", "flash_4"]
    default_reactions = {"alarm_triggered": "blink"}

    # Seconds between on/off toggles while a "blink" reaction is running.
    blink_interval: float = 0.5
    # Seconds each flash's on/off half lasts.
    flash_interval: float = 0.2

    async def init(self) -> None:
        self._driver = self._make_driver()
        await self.subscribe_flags()

    async def _apply_pattern_reaction(self, reaction: str) -> None:
        if reaction == "blink":
            self._is_blinking = True
            self._blink_task = asyncio.create_task(self._blink_loop())
        elif reaction.startswith("flash_"):
            self._blink_task = asyncio.create_task(self._flash_loop(int(reaction.removeprefix("flash_"))))

    async def _blink_loop(self) -> None:
        while True:
            await self.set_output(not self.is_on)
            await asyncio.sleep(self.blink_interval)

    async def _flash_loop(self, count: int) -> None:
        for _ in range(count):
            await self.set_output(True)
            await asyncio.sleep(self.flash_interval)
            await self.set_output(False)
            await asyncio.sleep(self.flash_interval)

    def _make_driver(self) -> Any:
        if self.config.get("driver", "mock") == "real":
            from alarmclock.modules.led.real import RealLEDDriver

            return RealLEDDriver(self.pin)
        from alarmclock.modules.led.mock import MockLEDDriver

        return MockLEDDriver(self.pin)

    async def _write(self, on: bool) -> None:
        await self._driver.write(on)

    async def on_event(self, event: str, payload: Any = None) -> None:
        """Generic dispatch entry point, in case something calls it directly
        - actual event handling happens via the flag subscriptions set up in
        `init()` (see `OutputModule.subscribe_flags`)."""

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(super().get_settings_schema())
        schema["pin"] = {**schema["pin"], "default": DEFAULT_PIN}
        return schema
