"""
Settings class for managing configuration with Singleton pattern.
This class completely replaces the functionality of persistence.py.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import yaml

class _Store:
    """Private class to store settings in YAML file. Only accessible through Settings."""

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
        """Gets a single setting from the file.

        Args:
            key: The path to the setting (e.g. "modules.button.enabled")
            default: Default value if not found

        Returns:
            The value or the default.
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
        """Sets a setting.

        Args:
            key: The path to the setting
            value: The new value (replaces the entire setting in the tree)
        """
        data = self._read() or {}
        parts = key.split('.')

        # Navigate to parent dict or create it
        parent_data = data
        for i in range(len(parts) - 1):
            part = parts[i]
            if isinstance(parent_data, dict) and part not in parent_data:
                parent_data[part] = {}
            elif not isinstance(parent_data.get(part), (dict, list)):
                # If parent dict exists but is not a dict/list, replace it
                parent_data[part] = {} if i == len(parts) - 2 else {}
                parent_data = parent_data[part]
            parent_data = parent_data.get(part, {})

        # Set the leaf at the end
        last_part = parts[-1]
        if isinstance(value, (dict, list)):
            parent_data[last_part] = value
        elif last_part in parent_data and not isinstance(parent_data[last_part], (dict, list)):
            # Only if the key exists and we're setting a new scalar dict
            parent_data[last_part] = {last_part: value}
        else:
            parent_data[last_part] = value

        self._write(data)

    def read(self) -> dict[str, Any] | None:
        """Reads the entire YAML file as an object.

        Returns the full parsed YAML content (dict, list, or scalar).
        Returns None if file doesn't exist.
        """
        return self._read()

    def get_setting(self, key: str) -> Any:
        """Gets a single setting from the file.

        Args:
            key: The path to the setting (e.g. "modules.button.enabled")

        Returns:
            The value or None if not found.
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
        """Updates a single setting in the file.

        Args:
            key: The path to the setting (e.g. "modules.button.enabled")
            new_value: The new value
        """
        data = self._read() or {}

        # Split the key into parent parts and leaf
        parts = key.split('.')
        if len(parts) < 2:
            return

        last_part = parts[-1]
        parent_parts = parts[:-1]

        # Navigate to parent dict, create parent container if needed
        parent_data = data
        for i, part in enumerate(parent_parts):
            if isinstance(parent_data, dict) and part in parent_data:
                child = parent_data[part]
                if isinstance(child, (dict, list)):
                    parent_data = child
                else:
                    # Parent dict exists but is not a container - replace it with a dict
                    parent_data[part] = {}
                    parent_data = parent_data[part]
            elif isinstance(parent_data, dict) and part not in parent_data:
                # Parent container must be created
                if i == len(parent_parts) - 1:
                    # This is the last parent node - replace it directly with the new value
                    parent_data[part] = {part: new_value}
                    self._write(data)
                    return
                else:
                    parent_data[part] = {}
                    parent_data = parent_data[part]
            else:
                return  # Parent dict doesn't exist

        # Set the leaf value in the parent dict
        if last_part in parent_data and isinstance(parent_data[last_part], (dict, list)):
            # The leaf is already a container - replace the whole parent element
            parent_data[last_part] = {last_part: new_value}
        elif last_part in parent_data:
            # The key exists and is not a container - set the new value directly
            parent_data[last_part] = new_value
        else:
            # Leaf container must be created - but only if the new value is a dict or scalar to be made into a dict
            if isinstance(new_value, (dict, list)):
                parent_data[last_part] = {last_part: new_value}
            else:
                # For scalars create a container with the key as the value itself? No!
                # If we update "alarm.volume" and alarm.volume doesn't exist yet,
                # it should simply become {"volume": 50}
                parent_data[last_part] = new_value

        self._write(data)

    def write(self, data: Any) -> None:
        """Writes a complete YAML file (replaces everything).

        Args:
            data: The entire content as dict, list or scalar.
        """
        self._write(data) if isinstance(data, (dict, list)) else self._write({})

    def remove_setting(self, key: str) -> bool:
        """Removes a setting from the file.

        Args:
            key: The path to removal (e.g. "modules.dht11.enabled")

        Returns:
            True if successfully removed, False if not found.
        """
        data = self._read() or {}
        parts = key.split('.')

        if len(parts) == 1:
            # Removes top-level key
            if parts[0] in data and isinstance(data, dict):
                del data[parts[0]]
                self._write(data)
                return True

        if len(parts) > 1:
            parent_key = '.'.join(parts[:-1])
            leaf_key = parts[-1]

            # Navigate to parent dict
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
        """Merges new settings into the file (deep update).

        Args:
            additional: New settings to add or overwrite.
        """
        existing = self._read() or {}

        def deep_update(base: dict, update: dict) -> None:
            for key, value in update.items():
                if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                    # Recursively merge
                    deep_update(base[key], value)
                else:
                    base[key] = value

        deep_update(existing, additional)
        self._write(existing)


class Settings:
    """Singleton class for managing configuration.

    This class completely replaces the functionality of persistence.py.
    It encapsulates the Store class and integrates the logic from _load_settings().
    """

    _instance = None
    _store = None
    _configuration = None

    def __new__(cls, *args: Any, **kwargs: Any) -> Settings:
        """Creates a new instance only if none exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls, *args: Any, **kwargs: Any) -> Settings:
        """Returns the singleton instance."""
        return cls(*args, **kwargs)

    def __init__(
        self,
        hardware_config_path: str | Path | None = None,
        settings_path: str | Path | None = None,
    ) -> None:
        """Initializes the Settings class.

        Args:
            hardware_config_path: Override for hardware_config.yaml's location.
                Defaults to alongside this file if not given.
            settings_path: Override for settings.yaml's location. Defaults to
                alongside this file if not given.
        """
        # Prevents re-initialization
        if not hasattr(self, '_initialized'):
            self._initialized = True
            # Sets the paths for settings storage and hardware config
            self._settings_path = Path(settings_path) if settings_path is not None else Path(__file__).parent / "settings.yaml"
            self._hardware_config_path = (
                Path(hardware_config_path)
                if hardware_config_path is not None
                else Path(__file__).parent.parent.parent / "config" / "hardware_config.yaml"
            )
            self._store = _Store(self._settings_path)

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Gets a single setting from the configuration.

        Args:
            key: The path to the setting (e.g. "modules.button.enabled")
            default: Default value if not found

        Returns:
            The value or the default.
        """
        return self._store.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        """Sets a single setting.

        Args:
            key: The path to the setting (e.g. "modules.button.enabled")
            value: The new value
        """
        self._store.set(key, value)

    def get(self, key: str, default: Any = None) -> Any:
        """Alias for get_setting(), so Settings itself satisfies the store
        interface modules expect (`store.get`/`store.set`) without handing
        out the underlying _Store.
        """
        return self.get_setting(key, default)

    def set(self, key: str, value: Any) -> None:
        """Alias for set_setting(), so Settings itself satisfies the store
        interface modules expect (`store.get`/`store.set`) without handing
        out the underlying _Store.
        """
        self.set_setting(key, value)

    def update_settings(self, updates: dict[str, Any]) -> None:
        """Updates multiple settings at once.

        Args:
            updates: Dictionary with the settings to be updated
        """
        self._store.merge_settings(updates)

    def remove_setting(self, key: str) -> bool:
        """Removes a single setting.

        Args:
            key: The path to the setting (e.g. "modules.button.enabled")

        Returns:
            True if successfully removed, otherwise False
        """
        return self._store.remove_setting(key)

    def load_configuration(self) -> dict[str, Any]:
        """Loads the complete configuration from all sources.

        Returns:
            The complete configuration as a Dictionary.
        """
        # storage layers
        # 1. hardware_config: activates the hardware from software
        # 2. default_settings: take default settings
        # 3. state/store: Dynamic user preferences and runtime values, overwriting default settings

        # 1. load hardware configuration
        with open(self._hardware_config_path, "r", encoding="utf-8") as f:
            hw_config = yaml.safe_load(f) or {}

        # get active modules
        active_modules = []
        module_defs = hw_config.get("modules", {})
        for name, info in module_defs.items():
            if info.get("enabled"):
                active_modules.append((name, info))

        # merge and collect default settings of modules and core modules
        # with existing ones in settings_path
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
        user_settings = self._store.read()
        if user_settings:
            final_settings.update(user_settings)

        self.save_configuration(final_settings)
        self._configuration = final_settings
        return final_settings

    def get_configuration(self) -> dict[str, Any]:
        """Returns the loaded configuration (caching).

        Returns:
            The loaded configuration.
        """
        if self._configuration is None:
            return self.load_configuration()
        return self._configuration

    def save_configuration(self, config: dict[str, Any]) -> None:
        """Saves a complete configuration to persistent storage.

        Args:
            config: The configuration to be saved as Dictionary.
        """
        self._store.write(config)

    def validate_configuration(self, config: dict[str, Any]) -> bool:
        """Validates the configuration against a schema (if implemented).

        Args:
            config: The configuration to be validated

        Returns:
            True if valid, otherwise False.
        """
        # Placeholder for validation - can be extended later
        return True

    def validate_setting(self, key: str, value: Any) -> bool:
        """Checks if a value is valid for a specific setting.

        Args:
            key: The path to the setting (e.g. "modules.button.enabled")
            value: The value to check

        Returns:
            True if valid, otherwise False.
        """
        # Placeholder for validation - can be extended later
        return True

    def reset_to_defaults(self) -> None:
        """Resets all settings to their default values."""
        self._configuration = None  # Clears the cache
        # Deletes the settings file so defaults are reloaded
        if self._settings_path.exists():
            self._settings_path.unlink()

    def backup_configuration(self) -> str:
        """Creates a backup of the current configuration and returns the file path.

        Returns:
            The path to the backup file.
        """
        import shutil
        import datetime

        if not self._settings_path.exists():
            return ""

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self._settings_path.with_name(f"settings_backup_{timestamp}.yaml")

        shutil.copy2(self._settings_path, backup_path)
        return str(backup_path)