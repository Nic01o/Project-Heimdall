"""Real LCD drivers: drive an actual HD44780-compatible character LCD,
either over I2C (PCF8574 backpack) or wired directly to raw GPIO pins
(RS/E/D4-D7, 4-bit mode) - both via RPLCD. Only ever imported when a
module's "driver" setting is "real" - keeps RPLCD/RPi.GPIO (only
installable/importable on actual Raspberry Pi hardware) out of the import
path everywhere else. Each driver lazily imports its own backend so picking
one interface doesn't require the dependencies of the other (e.g. the GPIO
driver doesn't need smbus2).
"""

from __future__ import annotations

from typing import Any


class _RPLCDDriver:
    """Shared read/write behavior for both RPLCD-backed drivers - only how
    the underlying `CharLCD` gets constructed differs between them."""

    def __init__(self, lcd: Any, cols: int, rows: int) -> None:
        self._lcd = lcd
        self.cols = cols
        self.rows = rows

    async def write_line(self, row: int, text: str) -> None:
        self._lcd.cursor_pos = (row, 0)
        self._lcd.write_string(text)

    async def clear(self) -> None:
        self._lcd.clear()


class RealI2CLCDDriver(_RPLCDDriver):
    def __init__(self, i2c_address: int, cols: int, rows: int) -> None:
        from RPLCD.i2c import CharLCD

        lcd = CharLCD(i2c_expander="PCF8574", address=i2c_address, cols=cols, rows=rows)
        super().__init__(lcd, cols, rows)


class RealGPIOLCDDriver(_RPLCDDriver):
    """Classic parallel wiring: RS/E plus 4 data lines (D4-D7), 4-bit mode.
    `rw_pin` may be `None` for the common wiring where R/W is hardwired to
    GND (display permanently in write mode) instead of using a pin.
    """

    def __init__(
        self,
        rs_pin: int,
        e_pin: int,
        data_pins: list[int],
        rw_pin: int | None,
        cols: int,
        rows: int,
    ) -> None:
        import RPi.GPIO as GPIO
        from RPLCD.gpio import CharLCD

        lcd = CharLCD(
            pin_rs=rs_pin,
            pin_rw=rw_pin,
            pin_e=e_pin,
            pins_data=data_pins,
            numbering_mode=GPIO.BCM,
            cols=cols,
            rows=rows,
        )
        super().__init__(lcd, cols, rows)
