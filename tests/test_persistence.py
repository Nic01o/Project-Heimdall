"""Tests für alarmclock.core.persistence.Store."""

from pathlib import Path
import pytest
import yaml

from alarmclock.core.persistence import Store


def test_store_init():
    """Test that Store successfully converts a string path to a Path object during initialization."""
    store = Store("test.yaml")
    assert store._path == Path("test.yaml")


def test_store_read_existing_full_file():
    """Test that Store.read() returns the complete file content as dict."""
    test_file_path = "test.yaml"
    test_data = {"modules": {"button": {"enabled": True}}, "core": {"timezone": "UTC"}}
    with open(test_file_path, "w", encoding="utf-8") as f:
        yaml.dump(test_data, f)

    store = Store(test_file_path)
    content = store.read()  # Neue Methode: liest ganze Datei
    assert isinstance(content, dict)
    assert content["modules"]["button"]["enabled"] is True
    assert content["core"]["timezone"] == "UTC"


def test_store_get_setting_nested():
    """Test Store.get_setting() mit dot-path (z.B. 'modules.button.enabled')."""
    test_file_path = "test.yaml"
    test_data = {"modules": {"button": {"enabled": True, "name": "Physical"}}}
    with open(test_file_path, "w", encoding="utf-8") as f:
        yaml.dump(test_data, f)

    store = Store(test_file_path)

    # Testen mit dot-path - neue Methode!
    assert store.get_setting("modules.button.enabled") is True
    assert store.get_setting("modules.button.name") == "Physical"
    assert store.get_setting("nonexistent.key") is None  # Nicht-existent: return None


def test_store_set_with_nested_value():
    """Test Store.set() ersetzt ein Setting im Baum (nicht die ganze Datei)."""
    store = Store("set_test.yaml")

    # Setzt ein komplettes nested dict als Wert
    new_setting = {"enabled": False, "name": "LED", "brightness": 80}
    store.set("modules.button", new_setting)

    with open("set_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    # Die anderen Settings sollten erhalten bleiben
    assert content["modules"]["button"]["enabled"] is False
    assert content["modules"]["button"]["name"] == "LED"


def test_store_update_setting_nested():
    """Test Store.update_setting() aktualisiert ein einzelnes Feld."""
    store = Store("update_test.yaml")

    # Erstelle initialen Baum
    initial_data = {"alarm": {"wake_time": "07:30", "volume": 10}}
    with open("update_test.yaml", "w", encoding="utf-8") as f:
        yaml.dump(initial_data, f)

    # Aktualisiert nur das alarm.volume Feld - nicht die ganze Datei!
    store.update_setting("alarm.volume", 50)

    with open("update_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content["alarm"]["wake_time"] == "07:30"  # Unverändert
    assert content["alarm"]["volume"] == 50  # Neuer Wert


def test_store_write_full_replacement():
    """Test Store.write() ersetzt die komplette Datei."""
    store = Store("write_test.yaml")

    # Zuerst etwas setzen (damit Datei existiert)
    store.set("key", "old_value")

    # Jetzt write() - überschreibt ALLES!
    new_content = {"completely": "new"}
    store.write(new_content)

    with open("write_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content == {"completely": "new"}  # Alt ist weg!


def test_store_write_with_dict():
    """Test Store.write() mit Dict."""
    store = Store("write_dict_test.yaml")
    store.set("dummy", "")  # Datei erstelln

    new_data = {
        "modules": {
            "button": {"enabled": True},
            "led": {"enabled": False}
        }
    }
    store.write(new_data)

    with open("write_dict_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content == new_data


def test_store_remove_setting():
    """Test Store.remove_setting() entfernt ein Setting aus der Datei."""
    store = Store("remove_test.yaml")

    # Erstelle Baum mit mehreren Settings
    initial_data = {
        "modules": {
            "button": {"enabled": True},
            "led": {"enabled": False}
        }
    }
    with open("remove_test.yaml", "w", encoding="utf-8") as f:
        yaml.dump(initial_data, f)

    store = Store("remove_test.yaml")

    # Entferne modules.button
    result = store.remove_setting("modules.button.enabled")
    assert result is True  # Erfolgreich

    with open("remove_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert "enabled" not in content["modules"]["button"]


def test_store_remove_nonexistent():
    """Test Store.remove_setting() mit nicht-existent Setting."""
    store = Store("remove_nonexistent.yaml")

    result = store.remove_setting("nonexistent.key")
    assert result is False  # Nicht gefunden


def test_store_merge_settings():
    """Test Store.merge_settings() deep merge neue Einstellungen."""
    store = Store("merge_test.yaml")

    # Erstelle Initialdaten
    initial_data = {
        "modules": {
            "button": {"enabled": True, "name": "Physical"},
            "led": {"enabled": False}
        },
        "core": {
            "timezone": "UTC",
            "language": "de"
        }
    }

    with open("merge_test.yaml", "w", encoding="utf-8") as f:
        yaml.dump(initial_data, f)

    store = Store("merge_test.yaml")

    # Neue Einstellungen hinzufügen (deep merge!)
    new_settings = {
        "modules": {
            "led": {"brightness": 75},  # Nur led wird erweitert
            "sound": {"enabled": True}  # Neues Modul hinzugefügt
        },
        "core": {
            "volume": 80  # Neuer core.Einstellung
        }
    }

    store.merge_settings(new_settings)

    with open("merge_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    # button.should unverändert bleiben (nicht in new_settings)
    assert content["modules"]["button"] == {"enabled": True, "name": "Physical"}

    # led wurde erweitert (brightness hinzugefügt)
    assert content["modules"]["led"]["enabled"] is False
    assert content["modules"]["led"]["brightness"] == 75

    # sound neu erstellt
    assert content["modules"]["sound"]["enabled"] is True

    # core.volume wurde hinzugefügt, timezone/language unverändert
    assert content["core"]["timezone"] == "UTC"
    assert content["core"]["language"] == "de"
    assert content["core"]["volume"] == 80


def test_store_write_empty_file():
    """Test Store.write() mit leeren dict (erstellt leere Datei)."""
    store = Store("empty_test.yaml")
    store.set("dummy", "")  # Erstellt die Datei

    # Write das ganze mit leerem dict
    store.write({})

    with open("empty_test.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)

    assert content == {}



def test_store_get_with_default():
    """Test Store.get(key, default) mit Default-Wert."""
    store = Store("get_default.yaml")

    with open("get_default.yaml", "w", encoding="utf-8") as f:
        yaml.dump({"existing": "value"}, f)

    # Nicht-existent key sollte default zurückgeben
    assert store.get("nonexistent") is None  # Default ist None (default Parameter)
    assert store.get("also_missing", "custom_default") == "custom_default"


def test_store_write_list():
    """Test Store.write() kann auch Lists speichern."""
    store = Store("list_test.yaml")

    new_data = [1, 2, {"nested": True}]
    with open("list_test.yaml", "w", encoding="utf-8") as f:
        yaml.dump(new_data, f)

    # read() sollte die List auch korrekt zurückgeben
    content = store.read()
    assert isinstance(content, list)


def test_store_init_creates_parent_dir():
    """Test dass Store.__init__() übergeordnete Verzeichnisse erstellt."""
    import os

    # Ein nicht-existentes verzeichnis erstellen
    subpath = "tests/creates_subdir/nested.yaml"

    store = Store(subpath)
    assert Path(subpath).parent.exists()  # Das Elternverzeichnis existiert jetzt


def test_store_read_missing_file():
    """Test dass read() bei fehlender Datei leeres dict zurückgibt."""
    store = Store("never_created.yaml")
    content = store.read()
    assert isinstance(content, dict)
    assert len(content) == 0


def cleanup():
    """Reinigt alle Test-Dateien."""
    files = [
        "test.yaml",
        "set_test.yaml",
        "update_test.yaml",
        "write_test.yaml",
        "write_dict_test.yaml",
        "remove_test.yaml",
        "remove_nonexistent.yaml",
        "merge_test.yaml",
        "empty_test.yaml",
    ]

    for file in files:
        if Path(file).exists():
            Path(file).unlink()
