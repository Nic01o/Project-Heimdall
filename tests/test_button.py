"""Tests for the button module: InputModule base behavior (pin, settings
schema, poll loop + edge detection), the button.pressed event, and both the
mock and real (GPIO-mocked) button drivers.
"""

import asyncio
import sys
import types

from alarmclock.core.event_bus import EventBus
from alarmclock.modules.button.button import ButtonModule
from alarmclock.modules.button.mock import MockButtonDriver


def make_module(**extra_settings) -> ButtonModule:
    config = {"pin": 27, **extra_settings}
    module = ButtonModule("button", EventBus(), config)
    module.poll_interval = 0.01  # keep tests fast
    return module


def test_settings_schema_includes_pin_and_driver():
    async def scenario():
        module = make_module()
        schema = await module.get_settings_schema()
        assert schema["pin"] == {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"}
        assert schema["driver"]["type"] == "select"
        assert schema["driver"]["options"] == ["mock", "real"]

    asyncio.run(scenario())


def test_pin_property_reads_from_settings():
    module = make_module()
    assert module.pin == 27


def test_defaults_to_mock_driver():
    async def scenario():
        module = make_module()
        await module.init()
        assert isinstance(module._driver, MockButtonDriver)
        assert module._driver.pin == 27

    asyncio.run(scenario())


def test_pressing_button_emits_pressed_event():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus, {"pin": 27})
        module.poll_interval = 0.01
        await module.init()

        events = []

        async def on_pressed(payload):
            events.append(payload)

        bus.subscribe("button.pressed", on_pressed)

        await module.enable()
        module._driver.press()
        await asyncio.sleep(0.05)
        await module.disable()

        assert events == [{"name": "button", "pin": 27}]

    asyncio.run(scenario())


def test_releasing_button_emits_released_event():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus, {"pin": 27})
        module.poll_interval = 0.01
        await module.init()

        events = []

        async def on_released(payload):
            events.append(payload)

        bus.subscribe("button.released", on_released)

        await module.enable()
        module._driver.press()
        await asyncio.sleep(0.03)
        module._driver.release()
        await asyncio.sleep(0.03)
        await module.disable()

        assert events == [{"name": "button", "pin": 27}]

    asyncio.run(scenario())


def test_holding_button_only_emits_once_per_press():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus, {"pin": 27})
        module.poll_interval = 0.01
        await module.init()

        events = []

        async def on_pressed(payload):
            events.append(payload)

        bus.subscribe("button.pressed", on_pressed)

        await module.enable()
        module._driver.press()
        await asyncio.sleep(0.05)  # held down across several poll ticks

        assert len(events) == 1

        module._driver.release()
        await asyncio.sleep(0.03)
        module._driver.press()
        await asyncio.sleep(0.03)
        await module.disable()

        assert len(events) == 2  # press, release, press again -> two edges

    asyncio.run(scenario())


def test_disable_stops_the_poll_loop():
    async def scenario():
        module = make_module()
        await module.init()
        await module.enable()
        assert module._poll_task is not None

        await module.disable()
        assert module._poll_task is None
        assert module.enabled is False

    asyncio.run(scenario())


def _install_fake_rpi_gpio() -> types.ModuleType:
    calls: list[tuple] = []

    fake_gpio = types.ModuleType("RPi.GPIO")
    fake_gpio.BCM = "BCM"
    fake_gpio.IN = "IN"
    fake_gpio.PUD_UP = "PUD_UP"
    fake_gpio.LOW = 0
    fake_gpio.HIGH = 1
    fake_gpio.calls = calls
    fake_gpio.pin_state = {}
    fake_gpio.setmode = lambda mode: calls.append(("setmode", mode))
    fake_gpio.setup = lambda pin, mode, pull_up_down=None: calls.append(
        ("setup", pin, mode, pull_up_down)
    )
    fake_gpio.input = lambda pin: fake_gpio.pin_state.get(pin, fake_gpio.HIGH)

    fake_rpi = types.ModuleType("RPi")
    fake_rpi.GPIO = fake_gpio

    sys.modules["RPi"] = fake_rpi
    sys.modules["RPi.GPIO"] = fake_gpio
    sys.modules.pop("alarmclock.modules.button.real", None)
    return fake_gpio


def test_real_button_driver_sets_up_and_reads_gpio():
    fake_gpio = _install_fake_rpi_gpio()
    try:
        from alarmclock.modules.button.real import RealButtonDriver

        driver = RealButtonDriver(pin=27)
        assert fake_gpio.calls == [("setmode", "BCM"), ("setup", 27, "IN", "PUD_UP")]

        assert asyncio.run(driver.read()) is False  # idle = HIGH

        fake_gpio.pin_state[27] = fake_gpio.LOW  # pressed
        assert asyncio.run(driver.read()) is True
    finally:
        sys.modules.pop("RPi", None)
        sys.modules.pop("RPi.GPIO", None)
        sys.modules.pop("alarmclock.modules.button.real", None)


def test_button_module_selects_real_driver_when_configured():
    fake_gpio = _install_fake_rpi_gpio()
    try:
        module = ButtonModule("button", EventBus(), {"pin": 27, "driver": "real"})
        asyncio.run(module.init())

        from alarmclock.modules.button.real import RealButtonDriver

        assert isinstance(module._driver, RealButtonDriver)
        assert ("setup", 27, "IN", "PUD_UP") in fake_gpio.calls
    finally:
        sys.modules.pop("RPi", None)
        sys.modules.pop("RPi.GPIO", None)
        sys.modules.pop("alarmclock.modules.button.real", None)
