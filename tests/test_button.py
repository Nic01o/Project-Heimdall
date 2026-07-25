"""Tests for the button module: InputModule base behavior (pin, settings
schema, poll loop + edge detection), the button.pressed/button.released
events, the button.long_press/button.combo gestures derived from them, the
generic button.flag events those gestures are normalized into, and both the
mock and real (GPIO-mocked) button drivers.
"""

import asyncio
import sys
import types

from alarmclock.core.event_bus import EventBus
from alarmclock.modules.button.button import ButtonModule
from alarmclock.modules.button.mock import MockButtonDriver
from alarmclock.modules.flags import BUTTON_FLAGS


def make_module(**extra_settings) -> ButtonModule:
    module = ButtonModule("button", EventBus())
    module.settings.update(extra_settings)
    module.poll_interval = 0.01  # keep tests fast
    return module


async def press_and_release(module, hold_seconds):
    module._driver.press()
    await asyncio.sleep(hold_seconds)
    module._driver.release()


def test_settings_schema_includes_pin():
    module = make_module()
    schema = module.get_settings_schema()
    assert schema["pin"] == {
        "type": "int",
        "min": 0,
        "max": 40,
        "label": "GPIO Pin",
        "default": 27,
    }


def test_settings_schema_does_not_include_driver():
    module = make_module()
    schema = module.get_settings_schema()
    assert "driver" not in schema


def test_settings_schema_has_pattern_thresholds():
    module = make_module()
    schema = module.get_settings_schema()
    assert schema["long_press_seconds"]["type"] == "float"
    assert schema["combo_window_seconds"]["type"] == "float"


def test_settings_schema_has_flags_multiselect():
    module = make_module()
    schema = module.get_settings_schema()
    assert schema["flags"]["type"] == "multiselect"
    assert schema["flags"]["options"] == BUTTON_FLAGS


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
        module = ButtonModule("button", bus)
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
        module = ButtonModule("button", bus)
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
        module = ButtonModule("button", bus)
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


def test_single_short_press_emits_combo_count_one():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus)
        module.settings["combo_window_seconds"] = 0.03
        module.poll_interval = 0.01
        await module.init()

        combos = []

        async def on_combo(payload):
            combos.append(payload)

        bus.subscribe("button.combo", on_combo)

        await module.enable()
        await press_and_release(module, hold_seconds=0.01)
        await asyncio.sleep(0.06)  # past the combo window
        await module.disable()

        assert combos == [{"name": "button", "pin": 27, "count": 1}]

    asyncio.run(scenario())


def test_double_press_emits_combo_count_two():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus)
        module.settings["combo_window_seconds"] = 0.05
        module.settings["long_press_seconds"] = 1.0
        module.poll_interval = 0.01
        await module.init()

        combos = []

        async def on_combo(payload):
            combos.append(payload)

        bus.subscribe("button.combo", on_combo)

        await module.enable()
        await press_and_release(module, hold_seconds=0.01)
        await asyncio.sleep(0.02)  # well within the combo window
        await press_and_release(module, hold_seconds=0.01)
        await asyncio.sleep(0.08)  # now let the window elapse
        await module.disable()

        assert combos == [{"name": "button", "pin": 27, "count": 2}]

    asyncio.run(scenario())


def test_triple_press_emits_combo_count_three():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus)
        module.settings["combo_window_seconds"] = 0.05
        module.settings["long_press_seconds"] = 1.0
        module.poll_interval = 0.01
        await module.init()

        combos = []

        async def on_combo(payload):
            combos.append(payload)

        bus.subscribe("button.combo", on_combo)

        await module.enable()
        for _ in range(3):
            await press_and_release(module, hold_seconds=0.01)
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.08)
        await module.disable()

        assert combos == [{"name": "button", "pin": 27, "count": 3}]

    asyncio.run(scenario())


def test_long_press_emits_long_press_not_combo():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus)
        module.settings["combo_window_seconds"] = 0.03
        module.settings["long_press_seconds"] = 0.05
        module.poll_interval = 0.01
        await module.init()

        combos = []
        long_presses = []

        async def on_combo(payload):
            combos.append(payload)

        async def on_long_press(payload):
            long_presses.append(payload)

        bus.subscribe("button.combo", on_combo)
        bus.subscribe("button.long_press", on_long_press)

        await module.enable()
        await press_and_release(module, hold_seconds=0.08)
        await asyncio.sleep(0.05)
        await module.disable()

        assert combos == []
        assert len(long_presses) == 1
        assert long_presses[0]["name"] == "button"
        assert long_presses[0]["pin"] == 27
        assert long_presses[0]["duration"] >= 0.05

    asyncio.run(scenario())


def test_press_and_release_emit_press_and_release_flags():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus)
        module.poll_interval = 0.01
        await module.init()

        flags = []

        async def on_flag(payload):
            flags.append(payload)

        bus.subscribe("button.flag", on_flag)

        await module.enable()
        module._driver.press()
        await asyncio.sleep(0.03)
        module._driver.release()
        await asyncio.sleep(0.03)
        await module.disable()

        assert {"name": "button", "pin": 27, "flag": "press"} in flags
        assert {"name": "button", "pin": 27, "flag": "release"} in flags

    asyncio.run(scenario())


def test_click_double_click_and_multi_click_emit_matching_flags():
    async def scenario(presses, expected_flag):
        bus = EventBus()
        module = ButtonModule("button", bus)
        module.settings["combo_window_seconds"] = 0.03
        module.poll_interval = 0.01
        await module.init()

        flags = []

        async def on_flag(payload):
            if payload["flag"] != "press" and payload["flag"] != "release":
                flags.append(payload["flag"])

        bus.subscribe("button.flag", on_flag)

        await module.enable()
        for _ in range(presses):
            await press_and_release(module, hold_seconds=0.01)
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.06)
        await module.disable()

        assert flags == [expected_flag]

    asyncio.run(scenario(1, "click"))
    asyncio.run(scenario(2, "double_click"))
    asyncio.run(scenario(3, "multi_click"))


def test_long_press_emits_long_press_flag():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus)
        module.settings["combo_window_seconds"] = 0.03
        module.settings["long_press_seconds"] = 0.05
        module.poll_interval = 0.01
        await module.init()

        flags = []

        async def on_flag(payload):
            flags.append(payload["flag"])

        bus.subscribe("button.flag", on_flag)

        await module.enable()
        await press_and_release(module, hold_seconds=0.08)
        await asyncio.sleep(0.05)
        await module.disable()

        assert "long_press" in flags
        assert "click" not in flags

    asyncio.run(scenario())


def test_flags_setting_filters_which_flags_are_sent():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus)
        module.settings["flags"] = ["long_press"]
        module.poll_interval = 0.01
        await module.init()

        flags = []

        async def on_flag(payload):
            flags.append(payload["flag"])

        bus.subscribe("button.flag", on_flag)

        await module.enable()
        module._driver.press()
        await asyncio.sleep(0.03)
        module._driver.release()
        await asyncio.sleep(0.05)
        await module.disable()

        assert flags == []  # "press"/"release"/"click" excluded by the flags setting

    asyncio.run(scenario())


def test_two_buttons_track_combos_independently():
    async def scenario():
        bus = EventBus()
        module_a = ButtonModule("button_a", bus)
        module_a.settings["pin"] = 17
        module_a.settings["combo_window_seconds"] = 0.03
        module_b = ButtonModule("button_b", bus)
        module_b.settings["combo_window_seconds"] = 0.03
        module_a.poll_interval = 0.01
        module_b.poll_interval = 0.01
        await module_a.init()
        await module_b.init()

        combos = []

        async def on_combo(payload):
            combos.append(payload)

        bus.subscribe("button.combo", on_combo)

        await module_a.enable()
        await module_b.enable()
        await press_and_release(module_a, hold_seconds=0.01)
        await press_and_release(module_b, hold_seconds=0.01)
        await asyncio.sleep(0.06)
        await module_a.disable()
        await module_b.disable()

        assert {"name": "button_a", "pin": 17, "count": 1} in combos
        assert {"name": "button_b", "pin": 27, "count": 1} in combos
        assert len(combos) == 2

    asyncio.run(scenario())


def test_release_without_matching_press_is_ignored():
    async def scenario():
        module = make_module()
        await module.init()

        # Should not raise even though the button was never marked pressed.
        await module._on_deactivated()

    asyncio.run(scenario())


def test_disable_cancels_pending_combo_task():
    async def scenario():
        bus = EventBus()
        module = ButtonModule("button", bus)
        module.settings["combo_window_seconds"] = 1.0
        module.poll_interval = 0.01
        await module.init()

        await module.enable()
        await press_and_release(module, hold_seconds=0.01)
        await asyncio.sleep(0.03)
        assert module._pending_task is not None

        await module.disable()
        assert module.enabled is False
        assert module._pending_task is None

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
        module = ButtonModule("button", EventBus(), {"driver": "real"})
        asyncio.run(module.init())

        from alarmclock.modules.button.real import RealButtonDriver

        assert isinstance(module._driver, RealButtonDriver)
        assert ("setup", 27, "IN", "PUD_UP") in fake_gpio.calls
    finally:
        sys.modules.pop("RPi", None)
        sys.modules.pop("RPi.GPIO", None)
        sys.modules.pop("alarmclock.modules.button.real", None)
