"""Tests for the sound module: OutputModule base behavior (pin, settings
schema, safe disable), the alarm.triggered/alarm.stopped wiring, and both
the mock and real (GPIO-mocked) speaker drivers.
"""

import asyncio
import sys
import types

from alarmclock.core.event_bus import EventBus
from alarmclock.modules.sound.mock import MockSpeakerDriver
from alarmclock.modules.sound.sound import SoundModule


def make_module(**extra_settings) -> SoundModule:
    module = SoundModule("sound", EventBus())
    module.settings.update(extra_settings)
    return module


def test_settings_schema_includes_pin():
    module = make_module()
    schema = module.get_settings_schema()
    assert schema["pin"] == {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin", "default": 18}


def test_settings_schema_does_not_include_driver():
    module = make_module()
    schema = module.get_settings_schema()
    assert "driver" not in schema


def test_pin_property_reads_from_settings():
    module = make_module()
    assert module.pin == 18


def test_defaults_to_mock_driver():
    async def scenario():
        module = make_module()
        await module.init()
        assert isinstance(module._driver, MockSpeakerDriver)
        assert module._driver.pin == 18

    asyncio.run(scenario())


def test_alarm_triggered_turns_speaker_on_and_stopped_turns_it_off():
    async def scenario():
        bus = EventBus()
        module = SoundModule("sound", bus)
        await module.init()
        await module.enable()

        await bus.emit("alarm.triggered", {"id": "a1"})
        assert module._driver.is_on is True

        await bus.emit("alarm.stopped", {"id": "a1"})
        assert module._driver.is_on is False

    asyncio.run(scenario())


def test_disable_turns_speaker_off():
    async def scenario():
        module = make_module()
        await module.init()
        await module.enable()
        await module.set_output(True)
        assert module._driver.is_on is True

        await module.disable()
        assert module._driver.is_on is False
        assert module.enabled is False

    asyncio.run(scenario())


def test_on_event_dispatches_like_the_bus_subscriptions():
    async def scenario():
        module = make_module()
        await module.init()

        await module.on_event("alarm.triggered", {"id": "a1"})
        assert module._driver.is_on is True

        await module.on_event("alarm.stopped", {"id": "a1"})
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
    sys.modules.pop("alarmclock.modules.sound.real", None)
    return fake_gpio


def test_real_speaker_driver_sets_up_and_writes_to_gpio():
    fake_gpio = _install_fake_rpi_gpio()
    try:
        from alarmclock.modules.sound.real import RealSpeakerDriver

        driver = RealSpeakerDriver(pin=18)
        assert fake_gpio.calls == [("setmode", "BCM"), ("setup", 18, "OUT")]

        asyncio.run(driver.write(True))
        assert fake_gpio.calls[-1] == ("output", 18, 1)

        asyncio.run(driver.write(False))
        assert fake_gpio.calls[-1] == ("output", 18, 0)
    finally:
        sys.modules.pop("RPi", None)
        sys.modules.pop("RPi.GPIO", None)
        sys.modules.pop("alarmclock.modules.sound.real", None)


def test_sound_module_selects_real_driver_when_configured():
    fake_gpio = _install_fake_rpi_gpio()
    try:
        module = SoundModule("sound", EventBus(), {"driver": "real"})
        asyncio.run(module.init())

        from alarmclock.modules.sound.real import RealSpeakerDriver

        assert isinstance(module._driver, RealSpeakerDriver)
        assert ("setup", 18, "OUT") in fake_gpio.calls
    finally:
        sys.modules.pop("RPi", None)
        sys.modules.pop("RPi.GPIO", None)
        sys.modules.pop("alarmclock.modules.sound.real", None)
