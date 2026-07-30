"""Flat JSON-file key/value store used to persist alarms and module settings
across daemon restarts. Synchronous - this is plain local file I/O on tiny
payloads, same as core/config.py's load_config."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import yaml

def _load_settings(hardware_config_path: Path, settings_path: Path) -> dict[str, Any]:
    # storage layers
    # 1. hardware_config: activates the hardware from software
    # 2. default_settings: take default settings
    # 3. state/store: Dynamic user preferences and runtime values, overwriting default settings

    # 1. load hardware configuration
    with open(hardware_config_path, "r", encoding="utf-8") as f:
        hw_config = yaml.safe_load(f) or {}

    # get active modules
    active_modules = []
    module_defs = hw_config.get("modules", {})
    for name, info in module_defs.items():
        if info.get("enabled"):
            active_modules.append((name, info))

    # merge and collect default settings of modules and core modules
    # with existing ones in settings_path
    store = Store(settings_path)
    final_settings = {}
    
    # 1. Load Core Defaults (base layer)
    core_defaults_path = Path(__file__).parent / "default_settings.yaml"
    if core_defaults_path.exists():
        with open(core_defaults_path, "r", encoding="utf-8") as f:
            core_defaults = yaml.safe_load(f) or {}
            final_settings.update(core_defaults)

    # 2. Merge Module Defaults and Overwrites
    for name, _ in active_modules:
        module_default_path = Path(__file__).parent / ".." / "modules" / name / "default_settings.yaml"
        if module_default_path.exists():
            with open(module_default_path, "r", encoding="utf-8") as f:
                m_defaults = yaml.safe_load(f) or {}
                final_settings.update(m_defaults)

    # 3. Overlay User Settings from Store using public API
    user_settings = store.read()
    if user_settings:
        final_settings.update(user_settings)

    return final_settings


class Store:
    """Public class to store settings in YAML file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        """Reads settings from YAML file."""
        if not self._path.exists():
            return {}
        with open(self._path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _write(self, data: dict[str, Any]) -> None:
        """Writes settings to YAML file."""
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
        os.replace(tmp_path, self._path)

    def get(self, key: str, default: Any = None) -> Any:
        """Liest eine einzelne Einstellung aus der Datei (veraltet).

        Args:
            key: Der Pfad zur Einstellung (z.B. "modules.button.enabled")
            default: Standardwert wenn nicht existierend

        Returns:
            Den Wert oder den default.
        """
        data = self._read() or {}
        parts = key.split('.')
        value = data
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def set(self, key: str, value: Any) -> None:
        """Setzt eine Einstellung (veraltet - ersetzt ganze Datei).

        Args:
            key: Der Pfad zur Einstellung
            value: Der neue Wert (ersetzt das gesamte Setting im Baum)
        """
        data = self._read() or {}
        parts = key.split('.')

        # Navigiere zum elternden dict oder erstelle es
        parent_data = data
        for i in range(len(parts) - 1):
            part = parts[i]
            if isinstance(parent_data, dict) and part not in parent_data:
                parent_data[part] = {}
            elif not isinstance(parent_data.get(part), (dict, list)):
                # Wenn elterndict existiert aber kein dict/list ist, ersetze es
                parent_data[part] = {} if i == len(parts) - 2 else {}
                parent_data = parent_data[part]
            parent_data = parent_data.get(part, {})

        # Setzen des leaves am Ende
        last_part = parts[-1]
        if isinstance(value, (dict, list)):
            parent_data[last_part] = value
        elif last_part in parent_data and not isinstance(parent_data[last_part], (dict, list)):
            # Nur wenn das key existiert ist und wir einen neuen scalar dict setzen
            parent_data[last_part] = {last_part: value}
        else:
            parent_data[last_part] = value

        self._write(data)

    def read(self) -> dict[str, Any] | None:
        """Liest die gesamte YAML-Datei als Objekt zurück.

        Returns the full parsed YAML content (dict, list, or scalar).
        Returns None if file doesn't exist.
        """
        return self._read()

    def get_setting(self, key: str) -> Any:
        """Liest eine einzelne Einstellung aus der Datei.

        Args:
            key: Der Pfad zur Einstellung (z.B. "modules.button.enabled")

        Returns:
            Den Wert oder None wenn nicht existierend.
        """
        data = self._read() or {}
        parts = key.split('.')
        value = data
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return None
        return value

    def update_setting(self, key: str, new_value: Any) -> None:
        """Aktualisiert eine einzelne Einstellung in der Datei.

        Args:
            key: Der Pfad zur Einstellung (z.B. "modules.button.enabled")
            new_value: Der neue Wert
        """
        data = self._read() or {}

        # Teile den key in parent-part und leaf
        parts = key.split('.')
        if len(parts) < 2:
            return

        last_part = parts[-1]
        parent_parts = parts[:-1]

        # Navigiere zum elternden dict, falls notwendig erstelle parent-container
        parent_data = data
        for i, part in enumerate(parent_parts):
            if isinstance(parent_data, dict) and part in parent_data:
                child = parent_data[part]
                if isinstance(child, (dict, list)):
                    parent_data = child
                else:
                    # Elterndict existiert aber kein container - ersetze es mit einem dict
                    parent_data[part] = {}
                    parent_data = parent_data[part]
            elif isinstance(parent_data, dict) and part not in parent_data:
                # Parent-Container muss erstellt werden
                if i == len(parent_parts) - 1:
                    # Dies ist der letzte Parent-Knoten - ersetze ihn direkt mit dem neuen Wert
                    parent_data[part] = {part: new_value}
                    self._write(data)
                    return
                else:
                    parent_data[part] = {}
                    parent_data = parent_data[part]
            else:
                return  # Elterndict existiert nicht

        # Setze den Leaf-Wert im Parent-Dict
        if last_part in parent_data and isinstance(parent_data[last_part], (dict, list)):
            # Der Leaf ist bereits ein Container - ersetze das ganze Parent-Element
            parent_data[last_part] = {last_part: new_value}
        elif last_part in parent_data:
            # Der Key existiert und ist kein Container - setze direkt den neuen Wert
            parent_data[last_part] = new_value
        else:
            # Leaf-Container muss erstellt werden - aber nur, wenn der neue Wert ein dict ist oder ein scalar zu einem dict gemacht wird
            if isinstance(new_value, (dict, list)):
                parent_data[last_part] = {last_part: new_value}
            else:
                # Für scalars erstelle einen container mit dem key als value selbst? Nein!
                # Wenn wir "alarm.volume" updaten und alarm.volume noch nicht existiert,
                # sollte es einfach zu {"volume": 50} werden
                parent_data[last_part] = new_value

        self._write(data)

    def write(self, data: Any) -> None:
        """Schreibt eine komplette YAML-Datei (ersetzt alles).

        Args:
            data: Der gesamte Inhalt als dict, list oder scalar.
        """
        self._write(data) if isinstance(data, (dict, list)) else self._write({})

    def remove_setting(self, key: str) -> bool:
        """Entfernt eine Einstellung aus der Datei.

        Args:
            key: Der Pfad zur Entfernung (z.B. "modules.dht11.enabled")

        Returns:
            True wenn erfolgreich entfernt, False wenn nicht gefunden.
        """
        data = self._read() or {}
        parts = key.split('.')

        if len(parts) == 1:
            # Entfemt Top-Level-Key
            if parts[0] in data and isinstance(data, dict):
                del data[parts[0]]
                self._write(data)
                return True

        if len(parts) > 1:
            parent_key = '.'.join(parts[:-1])
            leaf_key = parts[-1]

            # Navigiere zum elternden dict
            value = data
            for part in parts[:-1]:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return False

            if isinstance(value, dict) and leaf_key in value:
                del value[leaf_key]
                self._write(data)
                return True

        return False

    def merge_settings(self, additional: dict[str, Any]) -> None:
        """Mergst neue Einstellungen in die Datei (deep update).

        Args:
            additional: Neue Einstellungen die hinzugefügt oder überschrieben werden.
        """
        existing = self._read() or {}

        def deep_update(base: dict, update: dict) -> None:
            for key, value in update.items():
                if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                    # Recursiv recursiv recursiv mergen
                    deep_update(base[key], value)
                else:
                    base[key] = value

        deep_update(existing, additional)
        self._write(existing)