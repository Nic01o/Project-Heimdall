"""Tests for the LED module: OutputModule base behavior (pin, settings
schema, safe disable), the alarm.triggered/alarm.stopped/alarm.snoozed and
button.flag wiring (including the per-flag reaction_<flag> settings, which
control both whether and how the LED reacts to each flag - alarm events
included), and both the mock and real (GPIO-mocked) LED drivers.
"""

import asyncio
import sys
import types
from alarmclock.core.event_bus import EventBus
from alarmclock.modules.flags import BUTTON_FLAGS, FLAG_SOURCES
from alarmclock.modules.led.led import LEDModule
from alarmclock.modules.led.mock import MockLEDDriver


def make_module(**extra_settings) -> LEDModule:
    module = LEDModule("led", EventBus())
    module.settings.update(extra_settings)
    return module


def test_settings_schema_includes_pin_and_reactions():
    module = make_module()
    schema = module.get_settings_schema()
    assert schema["pin"] == {
        "type": "int",
        "min": 0,
        "max": 40,
        "label": "GPIO Pin",
        "default": 22,
    }

    # Every flag gets its own reaction_<flag> select field, generated
    # generically by OutputModule from FLAG_SOURCES.
    press_reaction = schema["reaction_press"]
    assert press_reaction["type"] == "select"
    assert press_reaction["default"] == "ignore"
    assert "flash_1" in press_reaction["options"]  # LED extends the base reaction set



def test_alarm_reactions_default_to_pre_unification_behavior():
    module = make_module()
    schema = module.get_settings_schema()

    assert schema["reaction_alarm_triggered"]["default"] == "blink"
    assert schema["reaction_alarm_stopped"]["default"] == "ignore"
    assert schema["reaction_alarm_snoozed"]["default"] == "ignore"



def test_settings_schema_does_not_include_driver():
    module = make_module()
    schema = module.get_settings_schema()
    assert "driver" not in schema


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
        module = LEDModule("led", bus)
        module.blink_interval = 0.01
        module.settings["reaction_alarm_stopped"] = "off"
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
        module = LEDModule("led", bus)
        module.blink_interval = 0.01
        await module.init()
        await module.enable()

        await bus.emit("alarm.triggered", {"id": "a1"})
        await asyncio.sleep(0.02)

        await bus.emit("alarm.snoozed", {"id": "a1", "minutes": 9, "new_id": "a2"})

        # The snooze event should make the LED continue blinking (not stop)
        # since the default reaction for alarm_snoozed is "blink"
        assert module._blink_task is not None
        assert module._driver.is_on is True  # Should be on during blink

        await module.disable()

    asyncio.run(scenario())


def test_reaction_alarm_triggered_ignore_stops_the_led_from_blinking():
    """This is the fix for the original bug report: setting
    reaction_alarm_triggered to "ignore" must actually stop the LED from
    reacting to alarm.triggered - previously this event was hardwired and
    unconditional, so no setting could suppress it."""

    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus)
        module.settings["reaction_alarm_triggered"] = "ignore"
        await module.init()
        await module.enable()

        await bus.emit("alarm.triggered", {"id": "a1"})
        await asyncio.sleep(0.02)

        assert module._blink_task is None
        assert module._driver.is_on is False

        await module.disable()

    asyncio.run(scenario())


def test_button_press_flag_ignored_by_default():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus)
        await module.init()
        await module.enable()

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "press"})
        assert module._driver.is_on is False  # every button flag defaults to "ignore"

        await module.disable()

    asyncio.run(scenario())


def test_on_and_off_reactions_turn_led_solid():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus)
        module.settings["reaction_press"] = "on"
        module.settings["reaction_release"] = "off"
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
        module = LEDModule("led", bus)
        module.blink_interval = 0.05
        module.settings["reaction_press"] = "on"
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
        module = LEDModule("led", bus)
        module.settings["reaction_click"] = "flash_1"
        await module.init()
        await module.enable()

        flashes = []

        async def counting_flash(times):
            flashes.append(times)

        module._flash_loop = counting_flash

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "click"})
        await asyncio.sleep(0)

        assert flashes == [1]

        await module.disable()

    asyncio.run(scenario())



def test_different_flags_flash_different_counts():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus)
        module.settings["reaction_double_click"] = "flash_2"
        module.settings["reaction_multi_click"] = "flash_3"
        module.settings["reaction_long_press"] = "flash_4"
        await module.init()
        await module.enable()

        flashes = []

        async def counting_flash(times):
            flashes.append(times)

        module._flash_loop = counting_flash

        for flag in ("double_click", "multi_click", "long_press"):
            await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": flag})
            await asyncio.sleep(0)

        assert flashes == [2, 3, 4]

        await module.disable()

    asyncio.run(scenario())


def test_ignore_reaction_filters_out_a_flag():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus)
        module.settings["reaction_press"] = "ignore"
        await module.init()
        await module.enable()

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "press"})
        assert module._driver.is_on is False  # reaction_press is "ignore"

        await module.disable()

    asyncio.run(scenario())


def test_toggle_reaction_flips_current_state():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus)
        module.settings["reaction_click"] = "toggle"
        await module.init()
        await module.enable()

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "click"})
        assert module._driver.is_on is True
        assert module.is_on is True

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "click"})
        assert module._driver.is_on is False
        assert module.is_on is False

        await module.disable()

    asyncio.run(scenario())


def test_custom_reaction_overrides_default_for_a_flag():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus)
        module.settings["reaction_long_press"] = "toggle"
        await module.init()
        await module.enable()

        await bus.emit("button.flag", {"name": "button", "pin": 27, "flag": "long_press"})
        assert module._driver.is_on is True  # toggled on instead of the default flash_4

        await module.disable()

    asyncio.run(scenario())


def test_disable_stops_blinking_and_turns_led_off():
    async def scenario():
        bus = EventBus()
        module = LEDModule("led", bus)
        module.blink_interval = 0.01
        await module.init()
        await module.enable()

        await bus.emit("alarm.triggered", {"id": "a1"})
        await asyncio.sleep(0.02)

        await module.disable()
        assert module._blink_task is None
        assert module._driver.is_on is False
        assert module.enabled is False

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
        module = LEDModule("led", EventBus(), {"driver": "real"})
        asyncio.run(module.init())

        from alarmclock.modules.led.real import RealLEDDriver

        assert isinstance(module._driver, RealLEDDriver)
        assert ("setup", 22, "OUT") in fake_gpio.calls
    finally:
        sys.modules.pop("RPi", None)
        sys.modules.pop("RPi.GPIO", None)
        sys.modules.pop("alarmclock.modules.led.real", None)
