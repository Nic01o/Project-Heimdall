"""Plugin interface for alarm clock modules."""

from __future__ import annotations

import abc
import asyncio
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


class OutputModule(Module):
    """Base for modules that drive a single GPIO pin as a simple on/off
    output (a buzzer/speaker relay, a light relay, ...). Adds a shared `pin`
    setting and a `set_output()`/`_write()` seam so mock vs. real hardware
    only differs in how the write happens - `init()`/`on_event()` (what
    triggers the output) stay module-specific and still need implementing.
    """

    async def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(await super().get_settings_schema())
        schema.setdefault("pin", {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"})
        return schema

    @property
    def pin(self) -> int:
        return self.settings["pin"]

    async def set_output(self, on: bool) -> None:
        await self._write(on)
        self.logger.debug("pin %s set to %s", self.pin, "on" if on else "off")

    async def _write(self, on: bool) -> None:
        """Actually drive the pin. Implemented by the concrete module
        (typically by delegating to a mock or real driver, see modules/sound)."""
        raise NotImplementedError

    async def enable(self) -> None:
        self.enabled = True

    async def disable(self) -> None:
        await self.set_output(False)
        self.enabled = False


class InputModule(Module):
    """Base for modules that read a single GPIO pin as input (a button, a
    PIR sensor, ...). Polls `_read()` in the background and calls
    `_on_activated()` on every inactive -> active transition (e.g. a button
    press) - `init()`/`on_event()`/what `_on_activated()` does stay
    module-specific and still need implementing.
    """

    poll_interval: float = 0.05

    def __init__(
        self,
        name: str,
        bus: Any,
        config: dict[str, Any] | None = None,
        store: Any = None,
    ) -> None:
        super().__init__(name, bus, config, store)
        self._poll_task: asyncio.Task[None] | None = None
        self._last_state = False

    async def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(await super().get_settings_schema())
        schema.setdefault("pin", {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"})
        return schema

    @property
    def pin(self) -> int:
        return self.settings["pin"]

    async def _read(self) -> bool:
        """Read the current pin state. Implemented by the concrete module
        (typically by delegating to a mock or real driver, see modules/button)."""
        raise NotImplementedError

    async def _on_activated(self) -> None:
        """Called on every inactive -> active transition. No-op by default;
        override to react (e.g. emit a bus event)."""

    async def _on_deactivated(self) -> None:
        """Called on every active -> inactive transition. No-op by default;
        override to react (e.g. emit a bus event)."""

    async def enable(self) -> None:
        self.enabled = True
        self._last_state = False
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def disable(self) -> None:
        self.enabled = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def _poll_loop(self) -> None:
        while True:
            state = await self._read()
            if state and not self._last_state:
                await self._on_activated()
            elif not state and self._last_state:
                await self._on_deactivated()
            self._last_state = state
            await asyncio.sleep(self.poll_interval)