"""Mock LED driver: no hardware access, just tracks state. Used on dev
machines/CI and whenever a module's `driver` setting is "mock" (the default).
"""

from __future__ import annotations


class MockLEDDriver:
    def __init__(self, pin: int) -> None:
        self.pin = pin
        self.is_on = False

    async def write(self, on: bool) -> None:
        self.is_on = on
