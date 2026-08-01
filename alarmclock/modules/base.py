"""Plugin interface for alarm clock modules."""

from __future__ import annotations

import abc
import asyncio
from typing import Any
from alarmclock.core.logger_wrapper import logger as logger_wrapper

from alarmclock.modules.settings_types import SettingsValidationError, validate_against_schema


class Module(abc.ABC):
    """Base class for all modules (plugins).

    Modules only communicate through the event bus - never directly with
    each other. Subclasses implement the lifecycle hooks below and, in
    ``init()``, subscribe to the events they care about.

    Config vs. state: `config` only ever carries backend/wiring concerns
    (`class` for import wiring, `enabled` as the load-time gate, `driver` for
    mock/real hardware selection, and optionally `override_enabled`/
    `overrides` to lock specific settings to a fixed value - see below).
    Everything a user actually tunes (pin, port, intervals, flags, `active`,
    ...) lives in `self.settings`, seeded from each field's `"default"` in
    `get_settings_schema()` and overlaid with whatever's persisted in
    `store`. A module never reads settings out of `config` directly.
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
        # Set by update_settings() when a schema field flagged
        # `requires_restart: True` actually changes value. A subclass's
        # enable() is responsible for clearing it once it has re-applied
        # whatever settings triggered it - only enable() knows which of its
        # own fields it just consumed. Modules with no requires_restart
        # fields never need to touch this.
        self.needs_restart = False
        self.logger = logger_wrapper

        schema = self.get_settings_schema()
        defaults = {key: field["default"] for key, field in schema.items() if "default" in field}

        persisted = self.store.get(f"modules.{name}") if self.store is not None else None
        known_persisted = {key: value for key, value in (persisted or {}).items() if key in schema}
        self.settings: dict[str, Any] = {**defaults, **known_persisted}

        # `override_enabled` + `overrides` let a deployment's config.yaml
        # hard-lock specific settings (e.g. "this Pi's LED really is on pin
        # 22") - the config value wins on every boot, and the field becomes
        # read-only in the settings UI (see `locked_fields`, enforced in
        # update_settings()/set_active()).
        override_enabled = bool(self.config.get("override_enabled"))
        overrides = self.config.get("overrides") or {}
        if override_enabled and overrides:
            validate_against_schema(overrides, schema)
            self.settings.update(overrides)
        self.locked_fields: frozenset[str] = frozenset(overrides) if override_enabled else frozenset()

        if self.store is not None:
            self.store.set(f"modules.{name}", self.settings)

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

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        """Describe this module's settings (used by UI/CLI to render forms,
        and by __init__ to seed `self.settings` from each field's
        `"default"`).

        Every module gets `active` (whether it should be enabled at
        startup/after a settings change) for free. Override for modules with
        further configurable settings; each field's `type` must come from
        `settings_types.FIELD_TYPES`.
        """
        return {"active": {"type": "bool", "label": "Aktiv", "default": True}}

    async def get_settings(self) -> dict[str, Any]:
        """Return current settings values."""
        return self.settings

    async def update_settings(self, values: dict[str, Any]) -> None:
        """Validate and store new settings. No side effects here—the module
        reacts to its own `<name>.settings_changed` event (or its next
        regular cycle) to actually apply the change.

        Generic based on the field-type vocabulary; only override for a real
        special case.

        A field can opt into `"requires_restart": True` in its schema entry;
        if such a field's value actually changes, `needs_restart` is set so
        the UI can prompt for a manual restart() instead of silently doing
        nothing until the process is restarted.

        Fields locked via config's `override_enabled`/`overrides` (see
        `locked_fields`) can never be changed here - config always wins.
        """
        locked = self.locked_fields.intersection(values)
        if locked:
            raise SettingsValidationError(f"locked by config: {sorted(locked)!r}")

        schema = self.get_settings_schema()
        validated = validate_against_schema(values, schema)
        for key, new_value in validated.items():
            if schema[key].get("requires_restart") and self.settings.get(key) != new_value:
                self.needs_restart = True
        self.settings = {**self.settings, **validated}
        if self.store is not None:
            self.store.set(f"modules.{self.name}", self.settings)
        await self.bus.emit(f"{self.name}.settings_changed", self.settings)

    async def set_active(self, active: bool) -> None:
        """Persist the user's enable/disable intent as the `active` setting,
        then actually apply it via enable()/disable(). This is what the
        web UI's enable/disable controls call, so the choice survives a
        daemon restart (see daemon.py's `_load_modules`, which only calls
        enable() for modules whose persisted `active` is true)."""
        if "active" in self.locked_fields:
            raise SettingsValidationError("'active' is locked by config")
        self.settings["active"] = active
        if self.store is not None:
            self.store.set(f"modules.{self.name}", self.settings)
        if active:
            await self.enable()
        else:
            await self.disable()

    async def restart(self) -> None:
        """Disable then re-enable, so a subclass's enable() can pick up
        changed requires_restart settings. Does not clear `needs_restart`
        itself - that's enable()'s job (see `needs_restart` above)."""
        await self.disable()
        await self.enable()


class OutputModule(Module):
    """Base for modules that drive a single GPIO pin as a simple on/off
    output (a buzzer/speaker relay, a light relay, ...). Adds a shared `pin`
    setting and a `set_output()`/`_write()` seam so mock vs. real hardware
    only differs in how the write happens - `init()`/`on_event()` (what
    triggers the output) stay module-specific and still need implementing.
    """

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(super().get_settings_schema())
        schema.setdefault("pin", {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"})
        return schema

    @property
    def pin(self) -> int:
        return self.settings["pin"]

    # Last commanded state, tracked generically here (not read back from the
    # driver) so subclasses reacting with e.g. a "toggle" can flip relative
    # to what they last asked for, without either driver needing a read-back
    # capability (RealLEDDriver's GPIO.output is write-only).
    is_on: bool = False

    async def set_output(self, on: bool) -> None:
        await self._write(on)
        self.is_on = on
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

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(super().get_settings_schema())
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
