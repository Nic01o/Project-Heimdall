"""Tests for alarmclock.modules.base.Configurable."""

import pytest

from alarmclock.modules.base import (
    Configurable,
    Module,
    available_module_types,
    write_registry_entry,
)


class ConfigurableModule(Configurable):
    def get_settings_schema(self):
        return {
            "pin": {"type": "int", "default": 17},
            "driver": {"type": "string", "default": "mock"},
            "blink_rate": {"type": "string", "default": "slow"},
            "active": {"type": "bool", "default": True},
        }


@pytest.fixture
def settings_path(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        "[registry]\n"
        'led_test = { module = "led", pin = 17, driver = "mock" }\n'
        "\n"
        "[settings.led_test]\n"
        'blink_rate = "fast"\n'
        "active = true\n"
    )
    return path


@pytest.mark.asyncio
async def test_load_config_merges_registry_and_overrides(settings_path):
    module = ConfigurableModule("test_module", None, settings_path=settings_path)
    await module.load_config("led_test")

    config = module.get_instance_config()
    assert config["pin"] == 17
    assert config["driver"] == "mock"
    assert config["blink_rate"] == "fast"


@pytest.mark.asyncio
async def test_save_config_persists_updates(settings_path):
    module = ConfigurableModule("test_module", None, settings_path=settings_path)
    await module.load_config("led_test")

    await module.save_config("led_test", {"blink_rate": "medium"})

    reloaded = ConfigurableModule("test_module", None, settings_path=settings_path)
    await reloaded.load_config("led_test")
    assert reloaded.get_instance_config()["blink_rate"] == "medium"


@pytest.mark.asyncio
async def test_locked_fields_come_from_registry_wiring(settings_path):
    """pin/driver are set via [registry.led_test] (hardware wiring) and must
    end up locked; blink_rate/active only ever live in [settings.led_test]
    and must stay editable."""
    module = ConfigurableModule("test_module", None, settings_path=settings_path)
    await module.load_config("led_test")

    assert module.locked_fields == frozenset({"pin", "driver"})


@pytest.mark.asyncio
async def test_locked_fields_empty_for_instance_without_wiring(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[registry]\nplain = { module = "led" }\n')

    module = ConfigurableModule("test_module", None, settings_path=path)
    await module.load_config("plain")

    assert module.locked_fields == frozenset()


def test_available_module_types_finds_listed_types(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        "[module_types]\n"
        'led = "alarmclock.modules.led.led"\n'
        'mymodule = "alarmclock.modules.mymodule.mymodule"\n'
    )

    types = available_module_types(path)

    assert set(types) == {"led", "mymodule"}
    assert types["led"].__name__ == "LEDModule"


def test_available_module_types_skips_unimportable_entry(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        "[module_types]\n"
        'led = "alarmclock.modules.led.led"\n'
        'button = "alarmclock.modules.button.button"\n'
    )

    types = available_module_types(path)

    assert set(types) == {"led"}


def test_available_module_types_empty_without_section(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("[registry]\n")

    assert available_module_types(path) == {}


def test_write_registry_entry_adds_instance(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("[registry]\n")

    write_registry_entry(path, "alarm_led", "led")

    module = ConfigurableModule("test_module", None, settings_path=path)
    import asyncio
    asyncio.run(module.load_config("alarm_led"))
    assert module.get_instance_config()["driver"] == "mock"  # schema default, untouched


def test_write_registry_entry_rejects_duplicate(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[registry]\nalarm_led = { module = "led" }\n')

    with pytest.raises(ValueError):
        write_registry_entry(path, "alarm_led", "led")


def test_write_registry_entry_preserves_other_sections(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        "[module_types]\n"
        'led = "alarmclock.modules.led.led"\n'
        "\n"
        "[registry]\n"
        'existing = { module = "led", pin = 5 }\n'
        "\n"
        "[settings.scheduler]\n"
        "enabled = true\n"
    )

    write_registry_entry(path, "new_one", "led")

    import tomllib
    with open(path, "rb") as f:
        data = tomllib.load(f)
    assert data["module_types"]["led"] == "alarmclock.modules.led.led"
    assert data["registry"]["existing"] == {"module": "led", "pin": 5}
    assert data["registry"]["new_one"] == {"module": "led"}
    assert data["settings"]["scheduler"]["enabled"] is True


class _NoopBus:
    async def emit(self, event, payload):
        pass


class ConcreteModule(Module):
    def get_settings_schema(self):
        schema = dict(super().get_settings_schema())
        schema["pin"] = {"type": "int", "default": 5}
        return schema

    async def init(self):
        pass

    async def enable(self):
        self.enabled = True

    async def disable(self):
        self.enabled = False

    async def on_event(self, event, payload=None):
        pass


@pytest.mark.asyncio
async def test_module_update_settings_persists(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[registry]\nm = { module = "concrete" }\n')
    module = ConcreteModule("m", _NoopBus(), settings_path=path)
    await module.load_config("m")

    await module.update_settings({"pin": 7})

    reloaded = ConcreteModule("m", _NoopBus(), settings_path=path)
    await reloaded.load_config("m")
    assert reloaded.settings["pin"] == 7


@pytest.mark.asyncio
async def test_module_set_active_persists(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[registry]\nm = { module = "concrete" }\n')
    module = ConcreteModule("m", _NoopBus(), settings_path=path)
    await module.load_config("m")

    await module.set_active(False)

    reloaded = ConcreteModule("m", _NoopBus(), settings_path=path)
    await reloaded.load_config("m")
    assert reloaded.settings["active"] is False
