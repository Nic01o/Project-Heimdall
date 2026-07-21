"""Tests for the lcd module: cols/rows-driven sizing (so the same module
drives different physical display sizes), the write_line/clear/show_lines
API, the alarm.triggered/alarm.stopped wiring, the idle clock loop, and
both the mock and real (RPLCD-mocked) drivers.
"""

import asyncio
import datetime
import sys
import types

from alarmclock.core.event_bus import EventBus
from alarmclock.modules.lcd.lcd import LCDModule
from alarmclock.modules.lcd.mock import MockLCDDriver


def make_module(**extra_settings) -> LCDModule:
    return LCDModule("lcd", EventBus(), dict(extra_settings))


def test_settings_schema_includes_driver_size_interface_and_pins():
    async def scenario():
        module = make_module()
        schema = await module.get_settings_schema()
        assert schema["driver"]["type"] == "select"
        assert schema["driver"]["options"] == ["mock", "real"]
        assert schema["cols"]["type"] == "int"
        assert schema["rows"]["type"] == "int"
        assert schema["interface"]["type"] == "select"
        assert schema["interface"]["options"] == ["i2c", "gpio"]
        assert schema["i2c_address"]["type"] == "string"
        for pin_field in ("rs_pin", "e_pin", "rw_pin", "d4_pin", "d5_pin", "d6_pin", "d7_pin"):
            assert schema[pin_field]["type"] == "int"

    asyncio.run(scenario())


def test_cols_rows_and_address_default():
    module = make_module()
    assert module.cols == 16
    assert module.rows == 2
    assert module.interface == "i2c"
    assert module.i2c_address == 0x27


def test_cols_rows_and_address_read_from_settings():
    module = make_module(cols=20, rows=4, i2c_address="0x3F")
    assert module.cols == 20
    assert module.rows == 4
    assert module.i2c_address == 0x3F


def test_gpio_pins_have_no_hidden_code_default():
    """GPIO wiring is board-specific - unlike cols/rows/i2c_address there's
    no sensible universal default, so these must come from config (same
    contract as OutputModule/InputModule's "pin") rather than silently
    falling back to a guessed pin number."""
    module = make_module()
    for attr in ("rs_pin", "e_pin", "rw_pin"):
        try:
            getattr(module, attr)
        except KeyError:
            pass
        else:
            raise AssertionError(f"{attr} should require an explicit setting")

    try:
        module.data_pins
    except KeyError:
        pass
    else:
        raise AssertionError("data_pins should require explicit d4-d7 settings")


def test_gpio_pins_read_from_settings():
    module = make_module(
        interface="gpio", rs_pin=1, e_pin=2, rw_pin=3, d4_pin=4, d5_pin=5, d6_pin=6, d7_pin=9
    )
    assert module.interface == "gpio"
    assert module.rs_pin == 1
    assert module.e_pin == 2
    assert module.rw_pin == 3
    assert module.data_pins == [4, 5, 6, 9]


def test_defaults_to_mock_driver_sized_to_settings():
    async def scenario():
        module = make_module(cols=20, rows=4)
        await module.init()
        assert isinstance(module._driver, MockLCDDriver)
        assert module._driver.cols == 20
        assert module._driver.rows == 4
        assert module._driver.lines == [""] * 4

    asyncio.run(scenario())


def test_write_line_pads_and_truncates_to_configured_width():
    async def scenario():
        module = make_module(cols=8, rows=2)
        await module.init()

        await module.write_line(0, "hi")
        assert module._driver.lines[0] == "hi      "

        await module.write_line(1, "way too long for 8 cols")
        assert module._driver.lines[1] == "way too "

    asyncio.run(scenario())


def test_write_line_ignores_out_of_range_row():
    async def scenario():
        module = make_module(cols=16, rows=2)
        await module.init()

        await module.write_line(5, "nope")
        assert module._driver.lines == ["", ""]

    asyncio.run(scenario())


def test_show_lines_fills_missing_rows_blank():
    async def scenario():
        module = make_module(cols=16, rows=4)
        await module.init()

        await module.show_lines(["only one line"])
        assert module._driver.lines[0].strip() == "only one line"
        assert module._driver.lines[1] == " " * 16
        assert module._driver.lines[2] == " " * 16
        assert module._driver.lines[3] == " " * 16

    asyncio.run(scenario())


def test_clear_blanks_all_rows():
    async def scenario():
        module = make_module(cols=16, rows=2)
        await module.init()
        await module.write_line(0, "hello")

        await module.clear()
        assert module._driver.lines == ["", ""]

    asyncio.run(scenario())


def test_alarm_triggered_shows_alarm_and_stopped_clears_the_flag():
    async def scenario():
        bus = EventBus()
        module = LCDModule("lcd", bus, {"cols": 16, "rows": 2})
        await module.init()

        await bus.emit("alarm.triggered", {"id": "a1", "label": "Wake up"})
        assert module._showing_alarm is True
        assert module._driver.lines[0].strip() == "ALARM!"
        assert module._driver.lines[1].strip() == "Wake up"

        await bus.emit("alarm.stopped", {"id": "a1"})
        assert module._showing_alarm is False

    asyncio.run(scenario())


def test_on_event_dispatches_like_the_bus_subscriptions():
    async def scenario():
        module = make_module()
        await module.init()

        await module.on_event("alarm.triggered", {"id": "a1", "label": "X"})
        assert module._showing_alarm is True

        await module.on_event("alarm.stopped", {"id": "a1"})
        assert module._showing_alarm is False

    asyncio.run(scenario())


def test_clock_loop_shows_time_and_date_while_idle():
    async def scenario():
        fixed_now = datetime.datetime(2026, 7, 21, 7, 30, 15)
        module = LCDModule("lcd", EventBus(), {"cols": 16, "rows": 2}, now=lambda: fixed_now)
        module.clock_interval = 0.01
        await module.init()

        await module.enable()
        await asyncio.sleep(0.03)
        await module.disable()

        # disable() clears the display again, so assert via a manual tick.
        assert module._clock_lines() == ["07:30:15", fixed_now.strftime("%a %d %b %Y")[:16]]

    asyncio.run(scenario())


def test_clock_loop_pauses_while_alarm_is_showing():
    async def scenario():
        bus = EventBus()
        module = LCDModule("lcd", bus, {"cols": 16, "rows": 2})
        module.clock_interval = 0.01
        await module.init()

        await module.enable()
        await bus.emit("alarm.triggered", {"id": "a1", "label": "Wake up"})
        await asyncio.sleep(0.03)  # several clock ticks while alarm is showing
        await module.disable()

        assert module._showing_alarm is True

    asyncio.run(scenario())


def test_disable_stops_clock_loop_and_clears_display():
    async def scenario():
        module = make_module()
        module.clock_interval = 0.01
        await module.init()
        await module.enable()
        assert module._clock_task is not None

        await module.disable()
        assert module._clock_task is None
        assert module.enabled is False
        assert module._driver.lines == ["", ""]

    asyncio.run(scenario())


class _FakeCharLCD:
    """Shared by both fake backends below: records init args, tracks
    cursor_pos/write_string/clear like the real RPLCD CharLCD classes do."""

    def __init__(self, calls: list[tuple], init_call: tuple) -> None:
        self._calls = calls
        calls.append(init_call)
        self.cursor_pos = (0, 0)
        self.written: list[tuple] = []

    def write_string(self, text):
        self.written.append((self.cursor_pos, text))

    def clear(self):
        self._calls.append(("clear",))


def _install_fake_rplcd_i2c() -> types.ModuleType:
    """Injects a fake RPLCD.i2c module into sys.modules so real.py's I2C
    driver logic can be verified without actual I2C hardware."""
    calls: list[tuple] = []

    class FakeCharLCD(_FakeCharLCD):
        def __init__(self, i2c_expander, address, cols, rows):
            self.cols = cols
            self.rows = rows
            super().__init__(calls, ("init", i2c_expander, address, cols, rows))

    fake_i2c = types.ModuleType("RPLCD.i2c")
    fake_i2c.CharLCD = FakeCharLCD
    fake_i2c.calls = calls

    fake_rplcd = types.ModuleType("RPLCD")
    fake_rplcd.i2c = fake_i2c

    sys.modules["RPLCD"] = fake_rplcd
    sys.modules["RPLCD.i2c"] = fake_i2c
    sys.modules.pop("alarmclock.modules.lcd.real", None)
    return fake_i2c


def _install_fake_rplcd_gpio() -> tuple[types.ModuleType, types.ModuleType]:
    """Injects fake RPLCD.gpio and RPi.GPIO modules into sys.modules so
    real.py's GPIO driver logic can be verified without actual hardware."""
    calls: list[tuple] = []

    class FakeCharLCD(_FakeCharLCD):
        def __init__(self, pin_rs, pin_rw, pin_e, pins_data, numbering_mode, cols, rows):
            self.cols = cols
            self.rows = rows
            super().__init__(
                calls,
                ("init", pin_rs, pin_rw, pin_e, tuple(pins_data), numbering_mode, cols, rows),
            )

    fake_gpio_backend = types.ModuleType("RPLCD.gpio")
    fake_gpio_backend.CharLCD = FakeCharLCD
    fake_gpio_backend.calls = calls

    fake_rplcd = types.ModuleType("RPLCD")
    fake_rplcd.gpio = fake_gpio_backend

    fake_rpi_gpio = types.ModuleType("RPi.GPIO")
    fake_rpi_gpio.BCM = "BCM"

    fake_rpi = types.ModuleType("RPi")
    fake_rpi.GPIO = fake_rpi_gpio

    sys.modules["RPLCD"] = fake_rplcd
    sys.modules["RPLCD.gpio"] = fake_gpio_backend
    sys.modules["RPi"] = fake_rpi
    sys.modules["RPi.GPIO"] = fake_rpi_gpio
    sys.modules.pop("alarmclock.modules.lcd.real", None)
    return fake_gpio_backend, fake_rpi_gpio


def _clear_fake_rplcd_modules() -> None:
    for mod in ("RPLCD", "RPLCD.i2c", "RPLCD.gpio", "RPi", "RPi.GPIO", "alarmclock.modules.lcd.real"):
        sys.modules.pop(mod, None)


def test_real_i2c_lcd_driver_sets_up_and_writes_over_i2c():
    fake_i2c = _install_fake_rplcd_i2c()
    try:
        from alarmclock.modules.lcd.real import RealI2CLCDDriver

        driver = RealI2CLCDDriver(i2c_address=0x27, cols=20, rows=4)
        assert fake_i2c.calls == [("init", "PCF8574", 0x27, 20, 4)]

        asyncio.run(driver.write_line(1, "hello"))
        assert driver._lcd.written == [((1, 0), "hello")]

        asyncio.run(driver.clear())
        assert fake_i2c.calls[-1] == ("clear",)
    finally:
        _clear_fake_rplcd_modules()


def test_lcd_module_selects_real_i2c_driver_when_configured():
    fake_i2c = _install_fake_rplcd_i2c()
    try:
        module = LCDModule(
            "lcd",
            EventBus(),
            {"driver": "real", "interface": "i2c", "i2c_address": "0x27", "cols": 20, "rows": 4},
        )
        asyncio.run(module.init())

        from alarmclock.modules.lcd.real import RealI2CLCDDriver

        assert isinstance(module._driver, RealI2CLCDDriver)
        assert ("init", "PCF8574", 0x27, 20, 4) in fake_i2c.calls
    finally:
        _clear_fake_rplcd_modules()


def test_real_gpio_lcd_driver_sets_up_and_writes_over_raw_pins():
    fake_gpio_backend, _ = _install_fake_rplcd_gpio()
    try:
        from alarmclock.modules.lcd.real import RealGPIOLCDDriver

        driver = RealGPIOLCDDriver(
            rs_pin=7, e_pin=8, data_pins=[25, 24, 23, 18], rw_pin=None, cols=16, rows=2
        )
        assert fake_gpio_backend.calls == [
            ("init", 7, None, 8, (25, 24, 23, 18), "BCM", 16, 2)
        ]

        asyncio.run(driver.write_line(0, "hello"))
        assert driver._lcd.written == [((0, 0), "hello")]

        asyncio.run(driver.clear())
        assert fake_gpio_backend.calls[-1] == ("clear",)
    finally:
        _clear_fake_rplcd_modules()


def test_real_gpio_lcd_driver_passes_through_an_explicit_rw_pin():
    fake_gpio_backend, _ = _install_fake_rplcd_gpio()
    try:
        from alarmclock.modules.lcd.real import RealGPIOLCDDriver

        RealGPIOLCDDriver(
            rs_pin=7, e_pin=8, data_pins=[25, 24, 23, 18], rw_pin=12, cols=16, rows=2
        )
        assert fake_gpio_backend.calls == [
            ("init", 7, 12, 8, (25, 24, 23, 18), "BCM", 16, 2)
        ]
    finally:
        _clear_fake_rplcd_modules()


def test_lcd_module_selects_real_gpio_driver_when_configured():
    fake_gpio_backend, _ = _install_fake_rplcd_gpio()
    try:
        module = LCDModule(
            "lcd",
            EventBus(),
            {
                "driver": "real",
                "interface": "gpio",
                "rs_pin": 7,
                "e_pin": 8,
                "rw_pin": -1,
                "d4_pin": 25,
                "d5_pin": 24,
                "d6_pin": 23,
                "d7_pin": 18,
                "cols": 16,
                "rows": 2,
            },
        )
        asyncio.run(module.init())

        from alarmclock.modules.lcd.real import RealGPIOLCDDriver

        assert isinstance(module._driver, RealGPIOLCDDriver)
        assert ("init", 7, None, 8, (25, 24, 23, 18), "BCM", 16, 2) in fake_gpio_backend.calls
    finally:
        _clear_fake_rplcd_modules()
