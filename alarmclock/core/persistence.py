"""Flat JSON-file key/value store used to persist alarms and module settings
across daemon restarts. Synchronous - this is plain local file I/O on tiny
payloads, same as core/config.py's load_config."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import yaml

# storage layers
# 1. hardware_config: loading activated / connected hardware
# 2. default_settings: take default settings
# 3. state/store: Dynamic user preferences and runtime values, overwriting default settings


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
        return self._read().get(key, default)

    def set(self, key: str, value: Any) -> None:
        data = self._read()
        data[key] = value
        self._write(data)