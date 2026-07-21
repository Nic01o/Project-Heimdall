"""Tests for the LED module: OutputModule base behavior (pin, settings
schema, safe disable), the alarm.triggered/alarm.stopped/alarm.snoozed and
button.flag wiring (including the reacts_to filter and per-flag reaction
patterns), and both the mock and real (GPIO-mocked) LED drivers.
"""

import asyncio
import sys
import types

from alarmclock.core.event_bus import EventBus
from alarmclock.modules.flags import BUTTON_FLAGS
from alarmclock.modules.led.led import LEDModule
from alarmclock.modules.led.mock import MockLEDDriver


def make_module(**extra_settings) -> LEDModule:
    config = {"pin": 22, **extra_settings}
    return LEDModule("led", EventBus(), config)


def test_settings_schema_includes_pin_driver_and_blink_interval():
    async def scenario():
        module = make_module()
        schema = await module.get_settings_schema()
        assert schema["pin"] == {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"}
        assert schema["driver"]["type"] == "select"
        assert schema["driver"]["options"] == ["mock", "real"]
        assert schema["blink_interval_seconds"]["type"] == "float"
        assert schema["reacts_to"]["type"] == "multiselect"
        assert schema["reacts_to"]["options"] == BUTTON_FLAGS

    asyncio.run(scenario())


def test_pin_property_reads_from_settings():
    module = make_module()
    assert module.pin == 22


def test_defaults_to_mock_driver():
    async def scenario():
        module = make_module()
        await module.init()
        assert isinstance(module._driver, MockLEDDriver)
        assert module._driver.pin == 22

    asyncio.run(scenario())


def test_alarm_triggered_blinks_until_stopped():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus, {"pin": 22, "blink_interval_seconds": 0.01})
        await module.init()
        await module.enable()

        await bus.emit("alarm.triggered", {"id": "a1"})
        await asyncio.sleep(0.03)
        assert module._blink_task is not None

        # it actually toggles, not just stays on
        states = set()
        for _ in range(4):
            states.add(module._driver.is_on)
            await asyncio.sleep(0.01)
        assert states == {True, False}

        await bus.emit("alarm.stopped", {"id": "a1"})
        assert module._blink_task is None
        assert module._driver.is_on is False

        await module.disable()

    asyncio.run(scenario())


def test_alarm_snoozed_flashes_twice_then_off():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus, {"pin": 22, "blink_interval_seconds": 0.01})
        await module.init()
        await module.enable()

        await bus.emit("alarm.triggered", {"id": "a1"})
        await asyncio.sleep(0.02)

        await bus.emit("alarm.snoozed", {"id": "a1", "minutes": 9, "new_id": "a2"})

        assert module._blink_task is None
        assert module._driver.is_on is False

        await module.disable()

    asyncio.run(scenario())


def test_button_press_flag_lights_led_while_idle():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus, {"pin": 22})
        await module.init()
        await module.enable()

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "press"})
        assert module._driver.is_on is True

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "release"})
        assert module._driver.is_on is False

        await module.disable()

    asyncio.run(scenario())


def test_button_press_flag_does_not_override_alarm_blink():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus, {"pin": 22, "blink_interval_seconds": 0.05})
        await module.init()
        await module.enable()

        await bus.emit("alarm.triggered", {"id": "a1"})
        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "press"})

        assert module._blink_task is not None

        await module.disable()

    asyncio.run(scenario())


def test_click_flag_flashes_once():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus, {"pin": 22})
        await module.init()
        await module.enable()

        flashes = []
        original_flash = module._flash

        async def counting_flash(times):
            flashes.append(times)
            await original_flash(times)

        module._flash = counting_flash

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "click"})
        assert flashes == [1]
        assert module._driver.is_on is False  # ends off

        await module.disable()

    asyncio.run(scenario())


def test_different_flags_flash_different_counts():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus, {"pin": 22})
        await module.init()
        await module.enable()

        flashes = []

        async def counting_flash(times):
            flashes.append(times)

        module._flash = counting_flash

        for flag in ("double_click", "multi_click", "long_press"):
            await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": flag})

        assert flashes == [2, 3, 4]

        await module.disable()

    asyncio.run(scenario())


def test_reacts_to_filters_out_unselected_flags():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus, {"pin": 22, "reacts_to": ["long_press"]})
        await module.init()
        await module.enable()

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "press"})
        assert module._driver.is_on is False  # "press" not in reacts_to, ignored

        await module.disable()

    asyncio.run(scenario())


def test_disable_stops_blinking_and_turns_led_off():
    async def scenario():
        module = make_module(blink_interval_seconds=0.01)
        await module.init()
        await module.enable()

        await module.on_event("alarm.triggered", {"id": "a1"})
        await asyncio.sleep(0.02)

        await module.disable()
        assert module._blink_task is None
        assert module._driver.is_on is False
        assert module.enabled is False

    asyncio.run(scenario())


def test_on_event_dispatches_like_the_bus_subscriptions():
    async def scenario():
        module = make_module()
        await module.init()

        await module.on_event("button.flag", {"name": "button", "pin": 27, "flag": "press"})
        assert module._driver.is_on is True

        await module.on_event("button.flag", {"name": "button", "pin": 27, "flag": "release"})
        assert module._driver.is_on is False

    asyncio.run(scenario())


def _install_fake_rpi_gpio() -> types.ModuleType:
    """Injects a fake RPi.GPIO module into sys.modules so real.py's driver
    logic can be verified without actual Raspberry Pi hardware."""
    calls: list[tuple] = []

    fake_gpio = types.ModuleType("RPi.GPIO")
    fake_gpio.BCM = "BCM"
    fake_gpio.OUT = "OUT"
    fake_gpio.HIGH = 1
    fake_gpio.LOW = 0
    fake_gpio.calls = calls
    fake_gpio.setmode = lambda mode: calls.append(("setmode", mode))
    fake_gpio.setup = lambda pin, mode: calls.append(("setup", pin, mode))
    fake_gpio.output = lambda pin, value: calls.append(("output", pin, value))

    fake_rpi = types.ModuleType("RPi")
    fake_rpi.GPIO = fake_gpio

    sys.modules["RPi"] = fake_rpi
    sys.modules["RPi.GPIO"] = fake_gpio
    sys.modules.pop("alarmclock.modules.led.real", None)
    return fake_gpio


def test_real_led_driver_sets_up_and_writes_to_gpio():
    fake_gpio = _install_fake_rpi_gpio()
    try:
        from alarmclock.modules.led.real import RealLEDDriver

        driver = RealLEDDriver(pin=22)
        assert fake_gpio.calls == [("setmode", "BCM"), ("setup", 22, "OUT")]

        asyncio.run(driver.write(True))
        assert fake_gpio.calls[-1] == ("output", 22, 1)

        asyncio.run(driver.write(False))
        assert fake_gpio.calls[-1] == ("output", 22, 0)
    finally:
        sys.modules.pop("RPi", None)
        sys.modules.pop("RPi.GPIO", None)
        sys.modules.pop("alarmclock.modules.led.real", None)


def test_led_module_selects_real_driver_when_configured():
    fake_gpio = _install_fake_rpi_gpio()
    try:
        module = LEDModule("led", EventBus(), {"pin": 22, "driver": "real"})
        asyncio.run(module.init())

        from alarmclock.modules.led.real import RealLEDDriver

        assert isinstance(module._driver, RealLEDDriver)
        assert ("setup", 22, "OUT") in fake_gpio.calls
    finally:
        sys.modules.pop("RPi", None)
        sys.modules.pop("RPi.GPIO", None)
        sys.modules.pop("alarmclock.modules.led.real", None)
