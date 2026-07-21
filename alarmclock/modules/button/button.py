"""Button module: a physical button wired to a GPIO pin. Emits the raw
`button.pressed` / `button.released` events on every press/release, and also
turns each full press+release cycle into a higher-level gesture of its own -
other buttons never interfere since each has its own pattern state:

- `button.long_press` {"name", "pin", "duration"} - released after being
  held at least `long_press_seconds`.
- `button.combo` {"name", "pin", "count"} - one or more quick presses in a
  row, each within `combo_window_seconds` of the previous release. Emitted
  once no further press arrives within the window, so `count` is final
  (this means even a single click is reported with a short delay - the
  price of being able to tell "1 click" from "the first of 2").

`button.long_press` and `button.combo` are mutually exclusive per press+
release cycle. Real hardware access lives behind a driver picked via the
"driver" setting - "mock" (default, safe everywhere) or "real" (actual
Raspberry Pi GPIO).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from alarmclock.modules.base import InputModule

DEFAULT_LONG_PRESS_SECONDS = 1.0
DEFAULT_COMBO_WINDOW_SECONDS = 0.4


class ButtonModule(InputModule):
    display_name = "Button"
    icon = "button"

    async def init(self) -> None:
        self._driver = self._make_driver()
        self._pressed_at: float | None = None
        self._pending_count = 0
        self._pending_task: asyncio.Task[None] | None = None

    def _make_driver(self) -> Any:
        if self.settings.get("driver", "mock") == "real":
            from alarmclock.modules.button.real import RealButtonDriver

            return RealButtonDriver(self.pin)
        from alarmclock.modules.button.mock import MockButtonDriver

        return MockButtonDriver(self.pin)

    async def _read(self) -> bool:
        return await self._driver.read()

    async def _on_activated(self) -> None:
        self.logger.info("button pressed (pin %s)", self.pin)
        self._pressed_at = time.monotonic()
        await self.bus.emit("button.pressed", {"name": self.name, "pin": self.pin})

    async def _on_deactivated(self) -> None:
        self.logger.info("button released (pin %s)", self.pin)
        await self.bus.emit("button.released", {"name": self.name, "pin": self.pin})
        await self._register_release()

    async def _register_release(self) -> None:
        pressed_at = self._pressed_at
        self._pressed_at = None
        if pressed_at is None:
            return

        duration = time.monotonic() - pressed_at
        long_press_seconds = self.settings.get("long_press_seconds", DEFAULT_LONG_PRESS_SECONDS)
        if duration >= long_press_seconds:
            await self.bus.emit(
                "button.long_press", {"name": self.name, "pin": self.pin, "duration": duration}
            )
            return

        await self._register_short_press()

    async def _register_short_press(self) -> None:
        if self._pending_task is not None:
            self._pending_task.cancel()

        self._pending_count += 1
        combo_window = self.settings.get("combo_window_seconds", DEFAULT_COMBO_WINDOW_SECONDS)
        self._pending_task = asyncio.create_task(self._finalize_combo(combo_window))

    async def _finalize_combo(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        count = self._pending_count
        self._pending_count = 0
        self._pending_task = None
        if count:
            await self.bus.emit("button.combo", {"name": self.name, "pin": self.pin, "count": count})

    async def disable(self) -> None:
        if self._pending_task is not None:
            self._pending_task.cancel()
            self._pending_task = None
        await super().disable()

    async def on_event(self, event: str, payload: Any = None) -> None:
        pass

    async def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(await super().get_settings_schema())
        schema["driver"] = {
            "type": "select",
            "options": ["mock", "real"],
            "label": "Driver",
        }
        schema["long_press_seconds"] = {
            "type": "float",
            "min": 0.1,
            "max": 10,
            "label": "Long press threshold (s)",
        }
        schema["combo_window_seconds"] = {
            "type": "float",
            "min": 0.1,
            "max": 3,
            "label": "Combo window (s)",
        }
        return schema
