"""Button module: a physical button wired to a GPIO pin. Emits `button.pressed`
/ `button.released` on the bus - what happens next (stop an alarm, snooze it,
interpret press combos, ...) is up to whichever module reacts to those
events, same as any other bus event (see modules/button_combos for an
example). Real hardware access lives behind a driver picked via the "driver"
setting - "mock" (default, safe everywhere) or "real" (actual Raspberry Pi
GPIO).
"""

from __future__ import annotations

from typing import Any

from alarmclock.modules.base import InputModule


class ButtonModule(InputModule):
    display_name = "Button"
    icon = "button"

    async def init(self) -> None:
        self._driver = self._make_driver()

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
        await self.bus.emit("button.pressed", {"name": self.name, "pin": self.pin})

    async def _on_deactivated(self) -> None:
        self.logger.info("button released (pin %s)", self.pin)
        await self.bus.emit("button.released", {"name": self.name, "pin": self.pin})

    async def on_event(self, event: str, payload: Any = None) -> None:
        pass

    async def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(await super().get_settings_schema())
        schema["driver"] = {
            "type": "select",
            "options": ["mock", "real"],
            "label": "Driver",
        }
        return schema
