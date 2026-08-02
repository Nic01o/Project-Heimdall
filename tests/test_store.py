"""Tests for alarmclock.core.settings.SettingsStore."""

from __future__ import annotations

import tomllib

import pytest

from alarmclock.core.settings import SettingsStore


@pytest.fixture
def store(tmp_path):
    """A SettingsStore backed by a temporary settings.toml."""
    return SettingsStore(tmp_path / "settings.toml")


def test_init_creates_parent_directory(tmp_path):
    nested = tmp_path / "nested" / "dir" / "settings.toml"
    SettingsStore(nested)
    assert nested.parent.exists()


def test_get_missing_file_returns_empty_dict(store):
    assert store.get("led_front") == {}


def test_get_missing_instance_returns_empty_dict(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[led_front]\npin = 22\n')
    store = SettingsStore(path)
    assert store.get("led_back") == {}


def test_set_persists_to_disk(store, tmp_path):
    store.set("led_front", {"pin": 22})
    persisted = tomllib.loads((tmp_path / "settings.toml").read_text())
    assert persisted == {"led_front": {"pin": 22}}


def test_get_returns_what_was_set(store):
    store.set("led_front", {"pin": 22, "blink_interval_seconds": 0.5})
    assert store.get("led_front") == {"pin": 22, "blink_interval_seconds": 0.5}


def test_set_merges_into_existing_overrides(store):
    store.set("led_front", {"pin": 22})
    store.set("led_front", {"blink_interval_seconds": 0.5})
    assert store.get("led_front") == {"pin": 22, "blink_interval_seconds": 0.5}


def test_set_overwrites_only_the_given_keys(store):
    store.set("led_front", {"pin": 22, "active": True})
    store.set("led_front", {"pin": 23})
    assert store.get("led_front") == {"pin": 23, "active": True}


def test_set_does_not_touch_other_instances(store):
    store.set("led_front", {"pin": 22})
    store.set("led_back", {"pin": 27})
    assert store.get("led_front") == {"pin": 22}
    assert store.get("led_back") == {"pin": 27}


def test_set_preserves_hand_edited_content(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[led_front]\npin = 22\n')
    store = SettingsStore(path)
    store.set("led_back", {"active": False})
    assert store.get("led_front") == {"pin": 22}
    assert store.get("led_back") == {"active": False}


def test_write_is_atomic_no_leftover_tmp_file(store, tmp_path):
    store.set("led_front", {"pin": 22})
    assert not (tmp_path / "settings.toml.tmp").exists()
