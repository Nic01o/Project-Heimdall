"""Tests für alarmclock.core.persistence.Store."""

from pathlib import Path
import pytest
import yaml

from alarmclock.core.persistence import Store


@pytest.fixture
def store(tmp_path):
    """Create a Store backed by a temporary directory."""
    return Store(tmp_path / "test.yaml")


@pytest.fixture
def store_with_data(tmp_path):
    """Store pre-populated with sample data."""
    path = tmp_path / "data.yaml"
    test_data = {
        "modules": {"button": {"enabled": True, "name": "Physical"}},
        "core": {"timezone": "UTC", "language": "de"},
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(test_data, f)
    return Store(path)


def test_store_init(store):
    """Test that Store successfully converts a string path to a Path object during initialization."""
    assert isinstance(store._path, Path)


def test_store_read_existing_full_file(tmp_path):
    """Test that Store.read() returns the complete file content as dict."""
    test_file_path = tmp_path / "read_test.yaml"
    test_data = {"modules": {"button": {"enabled": True}}, "core": {"timezone": "UTC"}}
    with open(test_file_path, "w", encoding="utf-8") as f:
        yaml.dump(test_data, f)

    store = Store(test_file_path)
    content = store.read()
    assert isinstance(content, dict)
    assert content["modules"]["button"]["enabled"] is True
    assert content["core"]["timezone"] == "UTC"


def test_store_read_missing_file(tmp_path):
    """Test dass read() bei fehlender Datei leeres dict zurueckgibt."""
    store = Store(tmp_path / "never_created.yaml")
    content = store.read()
    assert isinstance(content, dict)
    assert len(content) == 0


def test_store_get_setting_nested(store_with_data):
    """Test Store.get_setting() mit dot-path (z.B. 'modules.button.enabled')."""
    assert store_with_data.get_setting("modules.button.enabled") is True
    assert store_with_data.get_setting("modules.button.name") == "Physical"
    assert store_with_data.get_setting("nonexistent.key") is None


def test_store_get_with_default(tmp_path):
    """Test Store.get(key, default) mit Default-Wert."""
    path = tmp_path / "get_default.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump({"existing": "value"}, f)

    store = Store(path)
    assert store.get("nonexistent") is None
    assert store.get("also_missing", "custom_default") == "custom_default"
    assert store.get("existing") == "value"


def test_store_set_with_nested_value(tmp_path):
    """Test Store.set() ersetzt ein Setting im Baum."""
    store = Store(tmp_path / "set_test.yaml")

    new_setting = {"enabled": False, "name": "LED", "brightness": 80}
    store.set("modules.button", new_setting)

    with open(tmp_path / "set_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content["modules"]["button"]["enabled"] is False
    assert content["modules"]["button"]["name"] == "LED"


def test_store_update_setting_nested(tmp_path):
    """Test Store.update_setting() aktualisiert ein einzelnes Feld."""
    path = tmp_path / "update_test.yaml"
    initial_data = {"alarm": {"wake_time": "07:30", "volume": 10}}
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(initial_data, f)

    store = Store(path)
    store.update_setting("alarm.volume", 50)

    with open(path, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content["alarm"]["wake_time"] == "07:30"
    assert content["alarm"]["volume"] == 50


def test_store_write_full_replacement(tmp_path):
    """Test Store.write() ersetzt die komplette Datei."""
    store = Store(tmp_path / "write_test.yaml")
    store.set("key", "old_value")

    new_content = {"completely": "new"}
    store.write(new_content)

    with open(tmp_path / "write_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content == {"completely": "new"}


def test_store_write_with_dict(tmp_path):
    """Test Store.write() mit Dict."""
    store = Store(tmp_path / "write_dict_test.yaml")
    new_data = {
        "modules": {
            "button": {"enabled": True},
            "led": {"enabled": False},
        }
    }
    store.write(new_data)

    with open(tmp_path / "write_dict_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content == new_data


def test_store_write_empty_file(tmp_path):
    """Test Store.write() mit leeren dict."""
    store = Store(tmp_path / "empty_test.yaml")
    store.write({})

    with open(tmp_path / "empty_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content == {}


def test_store_write_list(tmp_path):
    """Test Store.write() kann auch Lists speichern."""
    path = tmp_path / "list_test.yaml"
    new_data = [1, 2, {"nested": True}]
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(new_data, f)

    store = Store(path)
    content = store.read()
    assert isinstance(content, list)


def test_store_remove_setting(tmp_path):
    """Test Store.remove_setting() entfernt ein Setting aus der Datei."""
    path = tmp_path / "remove_test.yaml"
    initial_data = {
        "modules": {
            "button": {"enabled": True},
            "led": {"enabled": False},
        }
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(initial_data, f)

    store = Store(path)
    result = store.remove_setting("modules.button.enabled")
    assert result is True

    with open(path, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert "enabled" not in content["modules"]["button"]


def test_store_remove_nonexistent(tmp_path):
    """Test Store.remove_setting() mit nicht-existent Setting."""
    store = Store(tmp_path / "remove_nonexistent.yaml")
    result = store.remove_setting("nonexistent.key")
    assert result is False


def test_store_merge_settings(tmp_path):
    """Test Store.merge_settings() deep merge neue Einstellungen."""
    path = tmp_path / "merge_test.yaml"
    initial_data = {
        "modules": {
            "button": {"enabled": True, "name": "Physical"},
            "led": {"enabled": False},
        },
        "core": {"timezone": "UTC", "language": "de"},
    }

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(initial_data, f)

    store = Store(path)
    new_settings = {
        "modules": {
            "led": {"brightness": 75},
            "sound": {"enabled": True},
        },
        "core": {"volume": 80},
    }
    store.merge_settings(new_settings)

    with open(path, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content["modules"]["button"] == {"enabled": True, "name": "Physical"}
    assert content["modules"]["led"]["enabled"] is False
    assert content["modules"]["led"]["brightness"] == 75
    assert content["modules"]["sound"]["enabled"] is True
    assert content["core"]["timezone"] == "UTC"
    assert content["core"]["language"] == "de"
    assert content["core"]["volume"] == 80


def test_store_init_creates_parent_dir(tmp_path):
    """Test dass Store.__init__() uebergeordnete Verzeichnisse erstellt."""
    nested = tmp_path / "deeply" / "nested" / "subdir" / "store.yaml"
    store = Store(nested)
    assert store._path.parent.exists()
