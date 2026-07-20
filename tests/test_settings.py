"""Unit tests for the module settings pattern: shared field-type vocabulary,
generic schema validation, and the default Module.update_settings flow.
"""

import asyncio
from typing import Any

import pytest

from alarmclock.core.event_bus import EventBus
from alarmclock.modules.base import Module
from alarmclock.modules.settings_types import SettingsValidationError, validate_against_schema

LIGHT_SCHEMA = {
    "gpio_pin": {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"},
    "brightness": {"type": "int", "min": 0, "max": 100, "label": "Brightness"},
    "color": {"type": "color", "label": "Light color"},
    "mode": {"type": "select", "options": ["fade", "instant"], "label": "Transition"},
}


class LightModule(Module):
    """Minimal module used to exercise the default settings flow."""

    display_name = "Light"
    icon = "lightbulb"

    async def init(self) -> None:
        pass

    async def enable(self) -> None:
        self.enabled = True

    async def disable(self) -> None:
        self.enabled = False

    async def on_event(self, event: str, payload: Any = None) -> None:
        pass

    async def get_settings_schema(self) -> dict:
        return LIGHT_SCHEMA


def test_validate_against_schema_accepts_valid_values():
    values = {"gpio_pin": 17, "brightness": 80, "color": "#ff0000", "mode": "fade"}
    assert validate_against_schema(values, LIGHT_SCHEMA) == values


def test_validate_against_schema_rejects_unknown_key():
    with pytest.raises(SettingsValidationError):
        validate_against_schema({"nonexistent": 1}, LIGHT_SCHEMA)


def test_validate_against_schema_rejects_out_of_range_int():
    with pytest.raises(SettingsValidationError):
        validate_against_schema({"brightness": 150}, LIGHT_SCHEMA)


def test_validate_against_schema_rejects_wrong_type():
    with pytest.raises(SettingsValidationError):
        validate_against_schema({"gpio_pin": "not an int"}, LIGHT_SCHEMA)


def test_validate_against_schema_rejects_invalid_select_option():
    with pytest.raises(SettingsValidationError):
        validate_against_schema({"mode": "strobe"}, LIGHT_SCHEMA)


def test_validate_against_schema_coerces_float():
    validated = validate_against_schema({"gpio_pin": 5}, LIGHT_SCHEMA)
    assert validated["gpio_pin"] == 5


def test_module_default_settings_come_from_config_minus_reserved_keys():
    module = LightModule(
        "light",
        EventBus(),
        {"enabled": True, "class": "x:Y", "gpio_pin": 17, "brightness": 100},
    )
    assert module.settings == {"gpio_pin": 17, "brightness": 100}


def test_module_update_settings_validates_merges_and_emits_event():
    async def scenario():
        bus = EventBus()
        changed_events = []

        async def on_changed(payload):
            changed_events.append(payload)

        bus.subscribe("light.settings_changed", on_changed)
        module = LightModule("light", bus, {"gpio_pin": 17, "brightness": 100})

        await module.update_settings({"brightness": 50})

        assert module.settings == {"gpio_pin": 17, "brightness": 50}
        assert await module.get_settings() == module.settings
        assert changed_events == [{"gpio_pin": 17, "brightness": 50}]

    asyncio.run(scenario())


def test_module_update_settings_rejects_invalid_value_without_mutating_state():
    async def scenario():
        module = LightModule("light", EventBus(), {"gpio_pin": 17, "brightness": 100})

        with pytest.raises(SettingsValidationError):
            await module.update_settings({"brightness": 999})

        assert module.settings == {"gpio_pin": 17, "brightness": 100}

    asyncio.run(scenario())


def test_module_default_get_settings_schema_is_empty():
    async def scenario():
        class PlainModule(Module):
            async def init(self) -> None:
                pass

            async def enable(self) -> None:
                pass

            async def disable(self) -> None:
                pass

            async def on_event(self, event: str, payload: Any = None) -> None:
                pass

        module = PlainModule("plain", EventBus())
        assert await module.get_settings_schema() == {}

    asyncio.run(scenario())
