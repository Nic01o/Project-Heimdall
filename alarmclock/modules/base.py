"""Plugin interface for alarm clock modules."""

from __future__ import annotations

import abc
import logging
from typing import Any

from alarmclock.modules.settings_types import validate_against_schema

_RESERVED_CONFIG_KEYS = ("enabled", "class")


class Module(abc.ABC):
    """Base class for all modules (plugins).

    Modules only communicate through the event bus - never directly with
    each other. Subclasses implement the lifecycle hooks below and, in
    ``init()``, subscribe to the events they care about.
    """

    display_name: str | None = None
    icon: str | None = None

    def __init__(
        self,
        name: str,
        bus: Any,
        config: dict[str, Any] | None = None,
        store: Any = None,
    ) -> None:
        self.name = name
        self.bus = bus
        self.config = config or {}
        self.store = store
        self.enabled = False
        self.logger = logging.getLogger(f"alarmclock.modules.{name}")
        self.settings: dict[str, Any] = {
            key: value for key, value in self.config.items() if key not in _RESERVED_CONFIG_KEYS
        }
        if self.store is not None:
            persisted = self.store.get(f"modules.{name}")
            if persisted is not None:
                self.settings.update(persisted)

    @abc.abstractmethod
    async def init(self) -> None:
        """Called once at startup, before enable(). Subscribe to bus events here."""
        raise NotImplementedError

    @abc.abstractmethod
    async def enable(self) -> None:
        """Enable the module."""
        raise NotImplementedError

    @abc.abstractmethod
    async def disable(self) -> None:
        """Disable the module."""
        raise NotImplementedError

    @abc.abstractmethod
    async def on_event(self, event: str, payload: Any = None) -> None:
        """Generic handler for events this module is subscribed to."""
        raise NotImplementedError

    # -- settings pattern ----------------------------------------------------
    #
    # Modules own their settings schema; the core and UI stay generic. A
    # future web UI (or CLI) can render a settings form for *any* module
    # without knowing anything about its internals.

    async def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        """Describe this module's settings (used by UI/CLI to render forms).

        Empty by default. Override for modules with configurable settings;
        each field's `type` must come from `settings_types.FIELD_TYPES`.
        """
        return {}

    async def get_settings(self) -> dict[str, Any]:
        """Return current settings values."""
        return self.settings

    async def update_settings(self, values: dict[str, Any]) -> None:
        """Validate and store new settings. No side effects here—the module
        reacts to its own `<name>.settings_changed` event (or its next
        regular cycle) to actually apply the change.

        Generic based on the field-type vocabulary; only override for a real
        special case.
        """
        schema = await self.get_settings_schema()
        validated = validate_against_schema(values, schema)
        self.settings = {**self.settings, **validated}
        if self.store is not None:
            self.store.set(f"modules.{self.name}", self.settings)
        await self.bus.emit(f"{self.name}.settings_changed", self.settings)