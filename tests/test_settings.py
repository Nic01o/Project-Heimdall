"""Tests for alarmclock.core.settings.HardwareRegistry."""

from __future__ import annotations

import pytest

from alarmclock.core.settings import HardwareRegistry


@pytest.fixture
def registry(tmp_path):
    """A HardwareRegistry backed by a temporary hardware.toml."""
    return HardwareRegistry(tmp_path / "hardware.toml")


def test_instances_missing_file_returns_empty_list(registry):
    assert registry.instances() == []


def test_get_instance_missing_file_returns_none(registry):
    assert registry.get_instance("led_front") is None


def test_instances_parses_array_of_tables(tmp_path):
    path = tmp_path / "hardware.toml"
    path.write_text(
        '[[instances]]\n'
        'id = "led_front"\n'
        'module = "led"\n'
        'driver = "mock"\n'
        '\n'
        '[[instances]]\n'
        'id = "led_back"\n'
        'module = "led"\n'
        'driver = "real"\n'
    )
    registry = HardwareRegistry(path)
    instances = registry.instances()
    assert len(instances) == 2
    assert instances[0] == {"id": "led_front", "module": "led", "driver": "mock"}
    assert instances[1] == {"id": "led_back", "module": "led", "driver": "real"}


def test_get_instance_returns_matching_entry(tmp_path):
    path = tmp_path / "hardware.toml"
    path.write_text(
        '[[instances]]\n'
        'id = "led_front"\n'
        'module = "led"\n'
        'driver = "real"\n'
        '[instances.overrides]\n'
        'pin = 22\n'
    )
    registry = HardwareRegistry(path)
    instance = registry.get_instance("led_front")
    assert instance["module"] == "led"
    assert instance["driver"] == "real"
    assert instance["overrides"] == {"pin": 22}


def test_get_instance_unknown_id_returns_none(tmp_path):
    path = tmp_path / "hardware.toml"
    path.write_text('[[instances]]\nid = "led_front"\nmodule = "led"\n')
    registry = HardwareRegistry(path)
    assert registry.get_instance("led_back") is None


def test_instances_file_without_instances_key_returns_empty_list(tmp_path):
    path = tmp_path / "hardware.toml"
    path.write_text('# no instances registered yet\n')
    registry = HardwareRegistry(path)
    assert registry.instances() == []
