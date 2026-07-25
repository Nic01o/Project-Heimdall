"""DHT11 module: a DHT11 temperature/humidity sensor wired to a single GPIO
pin. Polls the sensor every `read_interval_seconds` and emits
`dht11.reading` {"name", "pin", "temperature_c", "humidity"} on bus on every
successful read - the DHT11's single-wire protocol is timing-sensitive and
occasionally fails a checksum, so a failed read is logged and skipped
rather than raised, and the next poll just tries again.

Real hardware access lives behind a driver picked via the "driver" setting -
"mock" (default, safe everywhere) or "real" (actual Raspberry Pi GPIO, via
Adafruit_DHT).
"""

from __future__ import annotations

import asyncio
from typing import Any

from alarmclock.modules.base import Module

DEFAULT_READ_INTERVAL_SECONDS = 2.0


class DHT11Module(Module):
    display_name = "DHT11 Temperature Sensor"
    icon = "thermometer"

    def __init__(
        self,
        name: str,
        bus: Any,
        config: dict[str, Any] | None = None,
        store: Any = None,
    ) -> None:
        super().__init__(name, bus, config, store)
        self._driver: Any = None
        self._poll_task: asyncio.Task[None] | None = None

    async def init(self) -> None:
        self._driver = self._make_driver()

    def _make_driver(self) -> Any:
        if self.config.get("driver", "mock") == "real":
            from alarmclock.modules.dht11.real import RealDHT11Driver

            return RealDHT11Driver(self.pin)
        from alarmclock.modules.dht11.mock import MockDHT11Driver

        return MockDHT11Driver(self.pin)

    @property
    def pin(self) -> int:
        return self.settings["pin"]

    async def _read_once(self) -> None:
        reading = await self._driver.read()
        if reading is None:
            self.logger.warning("dht11 read failed (pin %s)", self.pin)
            return
        temperature_c, humidity = reading
        await self.bus.emit(
            "dht11.reading",
            {
                "name": self.name,
                "pin": self.pin,
                "temperature_c": temperature_c,
                "humidity": humidity,
            },
        )

    async def _poll_loop(self) -> None:
        interval = self.settings["read_interval_seconds"]
        while True:
            await self._read_once()
            await asyncio.sleep(interval)

    async def on_event(self, event: str, payload: Any = None) -> None:
        pass

    async def enable(self) -> None:
        self.enabled = True
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def disable(self) -> None:
        self.enabled = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(super().get_settings_schema())
        schema["pin"] = {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin", "default": 4}
        schema["read_interval_seconds"] = {
            "type": "float",
            "min": 1,
            "max": 60,
            "label": "Read interval (s)",
            "default": DEFAULT_READ_INTERVAL_SECONDS,
        }
        return schema
