"""Two-file TOML config.

- hardware.toml: the instance registry (wiring). Hand-edited only; read
  through `HardwareRegistry`, never written by the daemon or web UI.
- settings.toml: per-instance schema overrides (the diff from each
  instance's `get_settings_schema()` defaults). Read/written through
  `SettingsStore`, normally by the web UI via `Module.update_settings()`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomli_w


class HardwareRegistry:
    """Read-only view of hardware.toml's instance registry."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def instances(self) -> list[dict[str, Any]]:
        """Return the `[[instances]]` entries as-is (id, module, driver, overrides, ...)."""
        if not self._path.exists():
            return []
        with open(self._path, "rb") as f:
            data = tomllib.load(f)
        return data.get("instances", [])

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        """Return one instance's entry by id, or None if not registered."""
        for instance in self.instances():
            if instance.get("id") == instance_id:
                return instance
        return None


class SettingsStore:
    """Per-instance schema overrides, backed by settings.toml.

    Each top-level table is keyed by instance id and holds only the diff
    from that instance's schema defaults - never the full settings dict.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        with open(self._path, "rb") as f:
            return tomllib.load(f)

    def _write(self, data: dict[str, Any]) -> None:
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(tmp_path, "wb") as f:
            tomli_w.dump(data, f)
        tmp_path.replace(self._path)

    def get(self, instance_id: str) -> dict[str, Any]:
        """Return the persisted overrides for one instance ({} if none)."""
        return self._read().get(instance_id, {})

    def set(self, instance_id: str, values: dict[str, Any]) -> None:
        """Merge `values` into the persisted overrides for one instance."""
        data = self._read()
        data[instance_id] = {**data.get(instance_id, {}), **values}
        self._write(data)
