"""LCD module: a character LCD (HD44780-compatible) used to show the time
and alarm status. Physical displays come in different sizes (16x2, 20x4,
16x4, ...) - the "cols"/"rows" settings size the driver accordingly, and
every write clamps/pads to that size so the same module code drives any of
them unchanged. It also supports both common wiring styles - an I2C
backpack (PCF8574) or the display wired directly to raw GPIO pins
(RS/E/D4-D7) - picked via the "interface" setting. Real hardware access
lives behind a driver picked via the "driver" setting - "mock" (default,
safe everywhere) or "real" (actual hardware, via RPLCD).
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any, Callable

from alarmclock.modules.base import Module

DEFAULT_COLS = 16
DEFAULT_ROWS = 2
DEFAULT_I2C_ADDRESS = "0x27"
NOT_CONNECTED = -1  # rw_pin sentinel: not wired, R/W tied to GND

NowFn = Callable[[], datetime.datetime]


class LCDModule(Module):
    display_name = "LCD Display"
    icon = "display"

    clock_interval: float = 1.0

    def __init__(
        self,
        name: str,
        bus: Any,
        config: dict[str, Any] | None = None,
        store: Any = None,
        *,
        now: NowFn | None = None,
    ) -> None:
        super().__init__(name, bus, config, store)
        self._now = now or datetime.datetime.now
        self._driver: Any = None
        self._clock_task: asyncio.Task[None] | None = None
        self._showing_alarm = False

    async def init(self) -> None:
        self._driver = self._make_driver()
        self.bus.subscribe("alarm.triggered", self._on_alarm_triggered)
        self.bus.subscribe("alarm.stopped", self._on_alarm_stopped)

    def _make_driver(self) -> Any:
        if self.settings.get("driver", "mock") == "real":
            if self.interface == "gpio":
                from alarmclock.modules.lcd.real import RealGPIOLCDDriver

                rw_pin = self.rw_pin if self.rw_pin != NOT_CONNECTED else None
                return RealGPIOLCDDriver(
                    self.rs_pin, self.e_pin, self.data_pins, rw_pin, self.cols, self.rows
                )

            from alarmclock.modules.lcd.real import RealI2CLCDDriver

            return RealI2CLCDDriver(self.i2c_address, self.cols, self.rows)

        from alarmclock.modules.lcd.mock import MockLCDDriver

        return MockLCDDriver(self.cols, self.rows)

    @property
    def cols(self) -> int:
        return self.settings.get("cols", DEFAULT_COLS)

    @property
    def rows(self) -> int:
        return self.settings.get("rows", DEFAULT_ROWS)

    @property
    def interface(self) -> str:
        return self.settings.get("interface", "i2c")

    @property
    def i2c_address(self) -> int:
        raw = self.settings.get("i2c_address", DEFAULT_I2C_ADDRESS)
        return int(raw, 0) if isinstance(raw, str) else raw

    @property
    def rs_pin(self) -> int:
        return self.settings["rs_pin"]

    @property
    def e_pin(self) -> int:
        return self.settings["e_pin"]

    @property
    def rw_pin(self) -> int:
        return self.settings["rw_pin"]

    @property
    def data_pins(self) -> list[int]:
        return [
            self.settings["d4_pin"],
            self.settings["d5_pin"],
            self.settings["d6_pin"],
            self.settings["d7_pin"],
        ]

    # -- generic display API, sized to whatever "cols"/"rows" are set to ----

    async def write_line(self, row: int, text: str) -> None:
        if not 0 <= row < self.rows:
            return
        await self._driver.write_line(row, text[: self.cols].ljust(self.cols))

    async def clear(self) -> None:
        await self._driver.clear()

    async def show_lines(self, lines: list[str]) -> None:
        for row in range(self.rows):
            await self.write_line(row, lines[row] if row < len(lines) else "")

    # -- clock display (idle state, while no alarm is ringing) -------------

    def _clock_lines(self) -> list[str]:
        now = self._now()
        lines = [now.strftime("%H:%M:%S")]
        if self.rows > 1:
            lines.append(now.strftime("%a %d %b %Y")[: self.cols])
        return lines

    async def _clock_loop(self) -> None:
        while True:
            if not self._showing_alarm:
                await self.show_lines(self._clock_lines())
            await asyncio.sleep(self.clock_interval)

    # -- alarm reactions ------------------------------------------------------

    async def _on_alarm_triggered(self, payload: dict[str, Any]) -> None:
        self._showing_alarm = True
        await self.show_lines(["ALARM!", payload.get("label") or ""])

    async def _on_alarm_stopped(self, payload: dict[str, Any]) -> None:
        self._showing_alarm = False

    async def on_event(self, event: str, payload: Any = None) -> None:
        if event == "alarm.triggered":
            await self._on_alarm_triggered(payload)
        elif event == "alarm.stopped":
            await self._on_alarm_stopped(payload)

    # -- lifecycle --------------------------------------------------------

    async def enable(self) -> None:
        self.enabled = True
        self._showing_alarm = False
        self._clock_task = asyncio.create_task(self._clock_loop())

    async def disable(self) -> None:
        self.enabled = False
        if self._clock_task is not None:
            self._clock_task.cancel()
            try:
                await self._clock_task
            except asyncio.CancelledError:
                pass
            self._clock_task = None
        await self.clear()

    # -- settings ---------------------------------------------------------

    async def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(await super().get_settings_schema())
        schema["driver"] = {
            "type": "select",
            "options": ["mock", "real"],
            "label": "Driver",
        }
        schema["cols"] = {"type": "int", "min": 8, "max": 40, "label": "Columns"}
        schema["rows"] = {"type": "int", "min": 1, "max": 4, "label": "Rows"}
        schema["interface"] = {
            "type": "select",
            "options": ["i2c", "gpio"],
            "label": "Interface",
        }
        schema["i2c_address"] = {
            "type": "string",
            "label": "I2C Address (hex, e.g. 0x27)",
        }
        schema["rs_pin"] = {"type": "int", "min": 0, "max": 40, "label": "RS Pin (GPIO)"}
        schema["e_pin"] = {"type": "int", "min": 0, "max": 40, "label": "E Pin (GPIO)"}
        schema["rw_pin"] = {
            "type": "int",
            "min": -1,
            "max": 40,
            "label": "R/W Pin (GPIO, -1 = tied to GND)",
        }
        schema["d4_pin"] = {"type": "int", "min": 0, "max": 40, "label": "D4 Pin (GPIO)"}
        schema["d5_pin"] = {"type": "int", "min": 0, "max": 40, "label": "D5 Pin (GPIO)"}
        schema["d6_pin"] = {"type": "int", "min": 0, "max": 40, "label": "D6 Pin (GPIO)"}
        schema["d7_pin"] = {"type": "int", "min": 0, "max": 40, "label": "D7 Pin (GPIO)"}
        return schema
