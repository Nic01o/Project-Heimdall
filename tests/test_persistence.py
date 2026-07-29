from alarmclock.core.persistence import Store

from pathlib import Path
import pytest
import yaml


def test_store_init():
    """Test that Store successfully converts a string path to a Path object during initialization."""
    store = Store("test.yaml")
    assert store._path == Path("test.yaml")


def test_store_read_existing():
    """Test that Store read correctly returns the value associated with a key in an existing YAML file."""
    test_file_path = "test.yaml"
    with open(test_file_path, "w", encoding="utf-8") as f:
        yaml.dump({"key": "value"}, f)

    store = Store(test_file_path)
    assert store.get("key") == "value"


def test_store_write_new():
    """Test that Store correctly writes a new key-value pair to an available file."""
    store = Store("new_test.yaml")
    store.set("new_key", "new_value")
    with open("new_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    assert content["new_key"] == "new_value"


def test_store_write_update():
    """Test that Store updates an existing key with a new value in the YAML file."""
    store = Store("update_test.yaml")
    store.set("key_to_update", "original_value")
    store.set("key_to_update", "updated_value")
    with open("update_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    assert content["key_to_update"] == "updated_value"


def test_store_read_missing():
    """Test that Store handles a missing file safely by returning None for unknown keys."""
    store = Store("missing.yaml")
    assert store.get("nonexistent_key") is None
