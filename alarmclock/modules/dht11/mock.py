"""Mock DHT11 driver: no hardware access, readings are set manually (e.g.
from tests). Used on dev machines/CI and whenever a module's `driver`
setting is "mock" (the default).
"""

from __future__ import annotations


class MockDHT11Driver:
    def __init__(self, pin: int) -> None:
        self.pin = pin
        self.temperature_c = 21.0
        self.humidity = 45.0
        self.fail = False

    async def read(self) -> tuple[float, float] | None:
        if self.fail:
            return None
        return self.temperature_c, self.humidity
