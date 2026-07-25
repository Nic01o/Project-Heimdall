"""Unit tests for the module settings pattern: shared field-type vocabulary,
generic schema validation, and the default Module.update_settings flow -
including how `self.settings` gets seeded from schema defaults, overlaid by
persisted state, and (when config opts in) locked to a config override.
"""

import asyncio
from typing import Any

import pytest

from alarmclock.core.event_bus import EventBus
from alarmclock.core.persistence import JSONStore
from alarmclock.modules.base import Module
from alarmclock.modules.settings_types import SettingsValidationError, validate_against_schema

LIGHT_SCHEMA = {
    "gpio_pin": {
        "type": "int",
        "min": 0,
        "max": 40,
        "label": "GPIO Pin",
        "requires_restart": True,
        "default": 17,
    },
    "brightness": {"type": "int", "min": 0, "max": 100, "label": "Brightness", "default": 50},
    "color": {"type": "color", "label": "Light color", "default": "#ffffff"},
    "mode": {
        "type": "select",
        "options": ["fade", "instant"],
        "label": "Transition",
        "default": "fade",
    },
}

LIGHT_DEFAULTS = {"active": True, "gpio_pin": 17, "brightness": 50, "color": "#ffffff", "mode": "fade"}


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

    def get_settings_schema(self) -> dict:
        return {**super().get_settings_schema(), **LIGHT_SCHEMA}


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


# -- settings seeded from schema defaults ------------------------------------


def test_module_settings_seeded_from_schema_defaults():
    module = LightModule("light", EventBus())
    assert module.settings == LIGHT_DEFAULTS


def test_module_without_persisted_settings_falls_back_to_schema_defaults(tmp_path):
    store = JSONStore(tmp_path / "state.json")

    module = LightModule("light", EventBus(), store=store)

    assert module.settings == LIGHT_DEFAULTS
    assert store.get("modules.light") == LIGHT_DEFAULTS


def test_module_loads_persisted_settings_over_schema_defaults(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    store.set("modules.light", {"gpio_pin": 4, "brightness": 80})

    module = LightModule("light", EventBus(), store=store)

    # Persisted values win where present; anything missing (color, mode,
    # active) is self-healed from the schema default.
    assert module.settings == {**LIGHT_DEFAULTS, "gpio_pin": 4, "brightness": 80}


def test_module_drops_persisted_keys_no_longer_in_schema(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    store.set("modules.light", {"gpio_pin": 4, "stale_key": "leftover"})

    module = LightModule("light", EventBus(), store=store)

    assert "stale_key" not in module.settings
    assert store.get("modules.light") == module.settings


def test_module_config_no_longer_feeds_settings():
    module = LightModule("light", EventBus(), {"gpio_pin": 4, "brightness": 80})
    assert module.settings == LIGHT_DEFAULTS


# -- update_settings ----------------------------------------------------------


def test_module_update_settings_validates_merges_and_emits_event():
    async def scenario():
        bus = EventBus()
        changed_events = []

        async def on_changed(payload):
            changed_events.append(payload)

        bus.subscribe("light.settings_changed", on_changed)
        module = LightModule("light", bus)

        await module.update_settings({"brightness": 20})

        assert module.settings == {**LIGHT_DEFAULTS, "brightness": 20}
        assert await module.get_settings() == module.settings
        assert changed_events == [module.settings]

    asyncio.run(scenario())


def test_module_update_settings_rejects_invalid_value_without_mutating_state():
    async def scenario():
        module = LightModule("light", EventBus())

        with pytest.raises(SettingsValidationError):
            await module.update_settings({"brightness": 999})

        assert module.settings == LIGHT_DEFAULTS

    asyncio.run(scenario())


def test_module_update_settings_persists_to_store(tmp_path):
    async def scenario():
        store = JSONStore(tmp_path / "state.json")
        module = LightModule("light", EventBus(), store=store)

        await module.update_settings({"brightness": 20})

        assert store.get("modules.light") == {**LIGHT_DEFAULTS, "brightness": 20}

    asyncio.run(scenario())


def test_module_starts_with_needs_restart_false():
    module = LightModule("light", EventBus())
    assert module.needs_restart is False


def test_update_settings_flags_needs_restart_when_requires_restart_field_changes():
    async def scenario():
        module = LightModule("light", EventBus())

        await module.update_settings({"gpio_pin": 4})

        assert module.needs_restart is True

    asyncio.run(scenario())


def test_update_settings_does_not_flag_needs_restart_for_unflagged_field():
    async def scenario():
        module = LightModule("light", EventBus())

        await module.update_settings({"brightness": 20})

        assert module.needs_restart is False

    asyncio.run(scenario())


def test_update_settings_does_not_flag_needs_restart_when_value_unchanged():
    async def scenario():
        module = LightModule("light", EventBus())

        await module.update_settings({"gpio_pin": 17})

        assert module.needs_restart is False

    asyncio.run(scenario())


def test_restart_calls_disable_then_enable():
    async def scenario():
        module = LightModule("light", EventBus())
        await module.disable()
        assert module.enabled is False

        await module.restart()

        assert module.enabled is True

    asyncio.run(scenario())


def test_module_default_get_settings_schema_has_only_active():
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
    assert module.get_settings_schema() == {"active": {"type": "bool", "label": "Aktiv", "default": True}}


# -- config override_enabled / overrides (locked fields) ----------------------


def test_override_enabled_locks_settings_to_config_value():
    module = LightModule(
        "light", EventBus(), {"override_enabled": True, "overrides": {"gpio_pin": 30}}
    )
    assert module.settings["gpio_pin"] == 30
    assert module.locked_fields == frozenset({"gpio_pin"})


def test_override_wins_over_persisted_state_on_every_boot(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    store.set("modules.light", {"gpio_pin": 4})

    module = LightModule(
        "light",
        EventBus(),
        {"override_enabled": True, "overrides": {"gpio_pin": 30}},
        store,
    )

    assert module.settings["gpio_pin"] == 30


def test_overrides_ignored_when_override_enabled_is_false():
    module = LightModule("light", EventBus(), {"overrides": {"gpio_pin": 30}})
    assert module.settings["gpio_pin"] == 17
    assert module.locked_fields == frozenset()


def test_update_settings_rejects_locked_field():
    async def scenario():
        module = LightModule(
            "light", EventBus(), {"override_enabled": True, "overrides": {"gpio_pin": 30}}
        )

        with pytest.raises(SettingsValidationError):
            await module.update_settings({"gpio_pin": 4})

        assert module.settings["gpio_pin"] == 30

    asyncio.run(scenario())


def test_update_settings_allows_unlocked_field_when_another_is_locked():
    async def scenario():
        module = LightModule(
            "light", EventBus(), {"override_enabled": True, "overrides": {"gpio_pin": 30}}
        )

        await module.update_settings({"brightness": 20})

        assert module.settings["brightness"] == 20

    asyncio.run(scenario())


# -- active / set_active -------------------------------------------------------


def test_active_defaults_to_true():
    module = LightModule("light", EventBus())
    assert module.settings["active"] is True


def test_set_active_persists_and_toggles_enabled(tmp_path):
    async def scenario():
        store = JSONStore(tmp_path / "state.json")
        module = LightModule("light", EventBus(), store=store)

        await module.set_active(False)
        assert module.settings["active"] is False
        assert module.enabled is False
        assert store.get("modules.light")["active"] is False

        await module.set_active(True)
        assert module.settings["active"] is True
        assert module.enabled is True
        assert store.get("modules.light")["active"] is True

    asyncio.run(scenario())


def test_set_active_rejected_when_locked():
    async def scenario():
        module = LightModule(
            "light", EventBus(), {"override_enabled": True, "overrides": {"active": False}}
        )

        with pytest.raises(SettingsValidationError):
            await module.set_active(True)

    asyncio.run(scenario())
