"""Sound module: a speaker/buzzer wired to a GPIO pin. Turns on when an
alarm fires, off when it's stopped. Real hardware access lives behind a
driver picked via the "driver" setting - "mock" (default, safe everywhere)
or "real" (actual Raspberry Pi GPIO)."""

from __future__ import annotations

from typing import Any

from alarmclock.modules.base import OutputModule


class SoundModule(OutputModule):
    display_name = "Sound"
    icon = "speaker"

    async def init(self) -> None:
        self._driver = self._make_driver()
        self.bus.subscribe("alarm.triggered", self._on_alarm_triggered)
        self.bus.subscribe("alarm.stopped", self._on_alarm_stopped)

    def _make_driver(self) -> Any:
        if self.config.get("driver", "mock") == "real":
            from alarmclock.modules.sound.real import RealSpeakerDriver

            return RealSpeakerDriver(self.pin)
        from alarmclock.modules.sound.mock import MockSpeakerDriver

        return MockSpeakerDriver(self.pin)

    async def _write(self, on: bool) -> None:
        await self._driver.write(on)

    async def _on_alarm_triggered(self, payload: dict[str, Any]) -> None:
        await self.set_output(True)

    async def _on_alarm_stopped(self, payload: dict[str, Any]) -> None:
        await self.set_output(False)

    async def on_event(self, event: str, payload: Any = None) -> None:
        if event == "alarm.triggered":
            await self._on_alarm_triggered(payload)
        elif event == "alarm.stopped":
            await self._on_alarm_stopped(payload)

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(super().get_settings_schema())
        schema["pin"] = {**schema["pin"], "default": 18}
        return schema
