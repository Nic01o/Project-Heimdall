"""Mock button driver: no hardware access, state is set manually (e.g. from
tests). Used on dev machines/CI and whenever a module's `driver` setting is
"mock" (the default).
"""

from __future__ import annotations


class MockButtonDriver:
    def __init__(self, pin: int) -> None:
        self.pin = pin
        self.is_pressed = False

    async def read(self) -> bool:
        return self.is_pressed

    def press(self) -> None:
        self.is_pressed = True

    def release(self) -> None:
        self.is_pressed = False
