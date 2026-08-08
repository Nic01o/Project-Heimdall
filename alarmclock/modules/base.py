"""Plugin interface for alarm clock modules."""

from __future__ import annotations

import abc
import asyncio
import functools
import importlib
import tomllib
import tomli_w
from pathlib import Path
from typing import Any
from alarmclock.core.logger_wrapper import logger as logger_wrapper
from alarmclock.modules.flags import FLAG_SOURCES, resolve_flag
from alarmclock.modules.settings_types import SettingsValidationError, validate_against_schema


class Configurable:
    """Base class for all configurable modules.

    This class handles loading configuration from the unified settings.toml file,
    which contains both registry (hardware configs) and settings (overrides).
    It manages merging, type validation and legacy key cleanup.
    """

    def __init__(self, name: str, bus, config: dict[str, Any] | None = None, settings_path: Path | None = None) -> None:
        self.name = name
        self.bus = bus
        self.config = config or {}
        self._settings_path = settings_path or Path("config/settings.toml")
        # For now, keep a copy of the current configuration
        self._instance_config: dict[str, Any] = {}
        # Initialize with default settings (including schema defaults) to ensure tests still work
        self.settings: dict[str, Any] = self._get_default_settings()
        # Fields whose value comes from [registry.<name>] (hardware wiring) -
        # populated by load_config(); read-only in the settings UI, enforced
        # in Module.update_settings()/set_active().
        self.locked_fields: frozenset[str] = frozenset()

    def _get_default_settings(self) -> dict[str, Any]:
        """Get default settings with schema values populated."""
        schema = self.get_settings_schema()
        defaults = {}
        for key, value in schema.items():
            if "default" in value:
                defaults[key] = value["default"]
        return defaults

    async def load_config(self, instance_name: str) -> None:
        """Load configuration from settings.toml and merge with schema defaults.

        This method reads the registry and settings sections for the given instance,
        merges them, validates types against the module's schema, and performs
        cleanup of legacy keys (removing outdated ones from the file).

        Args:
            instance_name: The name of the instance to load configuration for.
        """
        # Read the entire settings.toml
        try:
            with open(self._settings_path, "rb") as f:
                settings_data = tomllib.load(f)
        except FileNotFoundError:
            # If no settings file exists, start fresh
            settings_data = {}

        # Ensure sections exist
        if "registry" not in settings_data:
            settings_data["registry"] = {}
        if "settings" not in settings_data:
            settings_data["settings"] = {}

        # Get registry data for this instance (hardware info)
        registry_instance = settings_data["registry"].get(instance_name, {})

        # Get override values for this instance
        overrides = settings_data["settings"].get(instance_name, {})

        # Hardware wiring from the registry always wins over the settings UI -
        # "module" itself isn't a settings field, so it's excluded here.
        self.locked_fields = frozenset(k for k in registry_instance if k != "module")

        # Merge defaults (schema) with registry data and overrides
        merged_config = self._merge_schema_with_data(registry_instance, overrides)

        # Update instance config
        self._instance_config = merged_config

        # Apply the merged config to self.config for compatibility with base class
        self.config = merged_config

        # Update self.settings as expected by legacy code (e.g. in test)
        # This must be done AFTER config is updated so settings are consistent
        self.settings = merged_config.copy()

        # Bring the settings file in line with the current schema: drop keys
        # the schema no longer defines, then backfill schema defaults that
        # aren't yet covered by either a registry wiring value or a
        # persisted override - so a fresh install or a newly added schema
        # field shows up in settings.toml instead of only existing
        # implicitly in memory. Registry-sourced keys are deliberately
        # excluded here: settings.<name> overrides win over registry on the
        # next load (see _merge_schema_with_data), so mirroring a wiring
        # value into settings would let it silently shadow future registry
        # edits.
        schema = self.get_settings_schema()
        if schema:
            self._cleanup_legacy_keys(instance_name, set(schema.keys()))
            missing_defaults = {
                key: field["default"]
                for key, field in schema.items()
                if "default" in field
                and key not in registry_instance
                and key not in overrides
                and not (key.startswith("reaction_") and field["default"] == "ignore")
            }
            if missing_defaults:
                await self.save_config(instance_name, missing_defaults)

    async def save_config(self, instance_name: str, updates: dict[str, Any]) -> None:
        """Save configuration changes to settings.toml.

        Only the [settings.<instance_name>] section is modified. The [registry]
        section is left untouched.

        Args:
            instance_name: The name of the instance to update.
            updates: Dictionary containing the configuration changes to save.
        """
        # Read the entire settings.toml
        try:
            with open(self._settings_path, "rb") as f:
                settings_data = tomllib.load(f)
        except FileNotFoundError:
            # If no settings file exists, start fresh
            settings_data = {"registry": {}, "settings": {}}

        # Ensure sections exist
        if "registry" not in settings_data:
            settings_data["registry"] = {}
        if "settings" not in settings_data:
            settings_data["settings"] = {}

        # Update the settings section for this instance
        if instance_name not in settings_data["settings"]:
            settings_data["settings"][instance_name] = {}

        # Apply updates
        for key, value in updates.items():
            settings_data["settings"][instance_name][key] = value

        # Write back to file atomically
        tmp_path = self._settings_path.with_suffix(self._settings_path.suffix + ".tmp")
        with open(tmp_path, "wb") as f:
            tomli_w.dump(settings_data, f)
        tmp_path.replace(self._settings_path)

    def _merge_schema_with_data(self, registry_data: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        """Merge schema defaults with registry data and override values.

        Args:
            registry_data: Hardware configuration from [registry][instance_name].
            overrides: Module-specific overrides from [settings][instance_name].

        Returns:
            Merged configuration dictionary.
        """
        schema = self.get_settings_schema()
        if not schema:
            # If no schema is defined, fall back to a simple merge
            result = registry_data.copy()
            result.update(overrides)
            return result

        # Start with defaults from schema
        result = {}
        for key, value in schema.items():
            default_value = value.get("default")
            if default_value is not None:
                result[key] = default_value

        # Update with registry data (hardware wiring)
        result.update(registry_data)

        # Update with overrides (user settings)
        result.update(overrides)

        return result

    def _cleanup_legacy_keys(self, instance_name: str, valid_schema_keys: set[str]) -> None:
        """Remove outdated keys from the settings file that are no longer in the schema.

        This ensures the configuration file doesn't accumulate cruft over time.

        Args:
            instance_name: The name of the instance to clean up.
            valid_schema_keys: Set of currently valid setting keys based on schema.
        """
        try:
            with open(self._settings_path, "rb") as f:
                settings_data = tomllib.load(f)
        except FileNotFoundError:
            # No file means no legacy keys to clean
            return

        # Ensure settings section exists
        if "settings" not in settings_data:
            return

        if instance_name not in settings_data["settings"]:
            return

        # Get current config for this instance
        current_config = settings_data["settings"][instance_name]

        # Identify keys that are no longer valid
        legacy_keys = set(current_config.keys()) - valid_schema_keys

        # If there are legacy keys, remove them from the file
        if legacy_keys:
            for key in legacy_keys:
                del current_config[key]

            # Write back to file atomically (only if changes were made)
            tmp_path = self._settings_path.with_suffix(self._settings_path.suffix + ".tmp")
            with open(tmp_path, "wb") as f:
                tomli_w.dump(settings_data, f)
            tmp_path.replace(self._settings_path)

    def get_instance_config(self) -> dict[str, Any]:
        """Get the instance configuration (merged registry + overrides).

        Returns:
            The merged configuration dictionary.
        """
        return self._instance_config.copy()


def _read_toml(settings_path: Path) -> dict[str, Any]:
    try:
        with open(settings_path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def available_module_types(settings_path: Path) -> dict[str, type["Module"]]:
    """Import and return every module type listed in `[module_types]` of
    settings.toml, e.g. `led = "alarmclock.modules.led.led"`. A module type
    only becomes selectable (in the daemon's registry loading and the web
    UI's "add module" form) once it has a line here - having source files
    under alarmclock/modules/ is not enough by itself, so a module can be
    developed and merged without being exposed yet. A listed path that
    fails to import, or doesn't define exactly one Module subclass, is
    skipped with a warning rather than raising - one broken/half-finished
    module type shouldn't take the whole list down."""
    settings_data = _read_toml(settings_path)
    types: dict[str, type[Module]] = {}
    for type_name, dotted_path in settings_data.get("module_types", {}).items():
        try:
            module_file = importlib.import_module(dotted_path)
        except ImportError as exc:
            logger_wrapper.warning(
                f"module type {type_name!r} ({dotted_path}) failed to import: {exc}",
                module_name="module_types",
            )
            continue
        for obj in vars(module_file).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, Module)
                and obj is not Module
                and obj.__module__ == module_file.__name__
            ):
                types[type_name] = obj
                break
    return types


def write_registry_entry(settings_path: Path, instance_id: str, module_type: str) -> None:
    """Add a new `[registry.<instance_id>]` entry wiring `instance_id` to
    `module_type`. Used by the web UI's "add module" flow - hand-editing
    stays the fallback, this is just the same file written programmatically.
    Raises ValueError if `instance_id` is already registered.

    Read-modify-write against the whole file, same as Configurable.save_config()
    - not safe against a concurrent writer, see todo.TODO's "settings.toml
    race condition" entry."""
    settings_data = _read_toml(settings_path)
    settings_data.setdefault("registry", {})
    settings_data.setdefault("settings", {})

    if instance_id in settings_data["registry"]:
        raise ValueError(f"instance {instance_id!r} already exists")

    settings_data["registry"][instance_id] = {"module": module_type}

    tmp_path = settings_path.with_suffix(settings_path.suffix + ".tmp")
    with open(tmp_path, "wb") as f:
        tomli_w.dump(settings_data, f)
    tmp_path.replace(settings_path)


class Module(Configurable):
    """Base class for all modules (plugins).

    Modules only communicate through the event bus - never directly with
    each other. Subclasses implement the lifecycle hooks below and, in
    ``init()``, subscribe to the events they care about.

    Config vs. state: `config` only ever carries backend/wiring concerns
    (`class` for import wiring, `enabled` as the load-time gate, `driver` for
    mock/real hardware selection, and optionally `overrides` to lock specific
    settings to a fixed value - see below). Everything a user actually tunes
    (pin, port, intervals, flags, `active`,
    ...) lives in `self.settings`, seeded from each field's `"default"` in
    `get_settings_schema()` and overlaid with whatever's persisted in
    `store`. A module never reads settings out of `config` directly.

    Note: This class is now based on Configurable, which loads configuration
    from a unified settings.toml. The 'store' parameter is still accepted for
    backwards compatibility but will be ignored as configuration is now handled
    by the Configurable base class.
    """

    display_name: str | None = None
    icon: str | None = None

    def __init__(
        self,
        name: str,
        bus: Any,
        config: dict[str, Any] | None = None,
        settings_path: Path | None = None,
    ) -> None:
        # Call parent __init__ with all necessary parameters
        super().__init__(name, bus, config, settings_path)

        self.name = name
        self.bus = bus
        self.config = config or {}
        self.enabled = False
        # Set by update_settings() when a schema field flagged
        # `requires_restart: True` actually changes value. A subclass's
        # enable() is responsible for clearing it once it has re-applied
        # whatever settings triggered it - only enable() knows which of its
        # own fields it just consumed. Modules with no requires_restart
        # fields never need to touch this.
        self.needs_restart = False
        self.logger = logger_wrapper

        # The configuration loading now happens through Configurable.load_config()
        # This method is called from the daemon (or module-specific code) after initialization

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

        # A reaction_<flag> submitted as "ignore" - its own schema default -
        # carries no information; skip persisting it so settings.toml only
        # lists the flags this instance actually reacts to.
        to_persist = {
            key: value
            for key, value in validated.items()
            if not (key.startswith("reaction_") and value == "ignore" and schema[key].get("default") == "ignore")
        }
        if to_persist:
            await self.save_config(self.name, to_persist)

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
        await self.save_config(self.name, {"active": active})
        await self.bus.emit(f"{self.name}.settings_changed", self.settings)
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
    setting, a `set_output()`/`_write()` seam so mock vs. real hardware only
    differs in how the write happens, and a generic `reaction_<flag>`
    mechanism: every flag in `alarmclock.modules.flags.FLAG_SOURCES` gets a
    configurable reaction (see `reactions`) - `init()` (what actually drives
    the output) stays module-specific and still needs implementing, but
    "which flags does this module react to, and how" is handled here for
    every subclass.

    Subclasses that want different out-of-the-box behavior than "ignore"
    for a given flag (e.g. the LED blinking on `alarm_triggered` by default)
    override the `default_reactions` class attribute. Subclasses whose
    hardware supports more than a plain on/off (e.g. the LED's flash
    patterns) extend the `reactions` class attribute with their own values -
    the base set here is deliberately the lowest common denominator so it
    stays meaningful for any binary output (a speaker, a relay, ...).
    """

    reactions: list[str] = ["ignore", "on", "off", "toggle"]
    default_reactions: dict[str, str] = {}

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(super().get_settings_schema())
        schema.setdefault("pin", {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"})
        for flag in FLAG_SOURCES:
            schema[f"reaction_{flag}"] = {
                "type": "select",
                "options": self.reactions,
                "default": self.default_reactions.get(flag, "ignore"),
                "label": f"Reaction: {flag}",
            }
        return schema

    @property
    def pin(self) -> int:
        return self.settings["pin"]

    # Last commanded state, tracked generically here (not read back from the
    # driver) so subclasses reacting with e.g. a "toggle" can flip relative
    # to what they last asked for, without either driver needing a read-back
    # capability (RealLEDDriver's GPIO.output is write-only).
    is_on: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Pass kwargs through to Configurable.__init__
        super().__init__(*args, **kwargs)
        # Task driving whatever ongoing pattern reaction is currently
        # active (see `_apply_pattern_reaction`), if any.
        self._blink_task: asyncio.Task[None] | None = None
        # Set by `_apply_pattern_reaction()` while its pattern should block
        # ordinary flags from interrupting it (see
        # `FlagSource.suppress_while_blinking`) - e.g. a continuous blink
        # sets this, a bounded flash doesn't.
        self._is_blinking: bool = False

    async def set_output(self, on: bool) -> None:
        await self._write(on)
        self.is_on = on
        self.logger.debug("pin %s set to %s", self.pin, "on" if on else "off", module_name=self.name)

    async def _write(self, on: bool) -> None:
        """Actually drive the pin. Implemented by the concrete module
        (typically by delegating to a mock or real driver, see modules/sound)."""
        raise NotImplementedError

    async def enable(self) -> None:
        self.enabled = True

    async def disable(self) -> None:
        await self._stop_pattern()
        await self.set_output(False)
        self.enabled = False

    # -- reaction dispatch ----------------------------------------------------
    #
    # Subclasses call `subscribe_flags()` once from their `init()` to wire
    # every flag in `alarmclock.modules.flags.FLAG_SOURCES` up to this
    # module's own `reaction_<flag>` setting (see `get_settings_schema()`).

    async def subscribe_flags(self) -> None:
        """Subscribe to every bus event any flag could arrive on. Several
        flags can share one event (e.g. all button gestures arrive as
        `button.flag`), so this subscribes once per distinct event name and
        lets `_on_flag_event()` resolve which flag actually fired."""
        seen_events: set[str] = set()
        for source in FLAG_SOURCES.values():
            if source.event in seen_events:
                continue
            seen_events.add(source.event)
            self.bus.subscribe(source.event, functools.partial(self._on_flag_event, source.event))

    async def _on_flag_event(self, event: str, payload: dict[str, Any]) -> None:
        flag = resolve_flag(event, payload)
        if flag is None:
            return
        reaction = self.settings.get(f"reaction_{flag}", "ignore")
        if reaction == "ignore":
            return
        if FLAG_SOURCES[flag].suppress_while_blinking and self._is_blinking:
            return
        await self._stop_pattern()
        await self._apply_reaction(reaction)

    async def _apply_reaction(self, reaction: str) -> None:
        """Execute a reaction value from this module's `reactions`
        vocabulary. Handles the hardware-agnostic on/off/toggle cases
        directly via `set_output()`; anything else is delegated to
        `_apply_pattern_reaction()`. Whatever pattern was previously running
        has already been stopped by the caller (`_on_flag_event()`)."""
        if reaction == "on":
            await self.set_output(True)
        elif reaction == "off":
            await self.set_output(False)
        elif reaction == "toggle":
            await self.set_output(not self.is_on)
        else:
            await self._apply_pattern_reaction(reaction)

    async def _apply_pattern_reaction(self, reaction: str) -> None:
        """Start whatever ongoing pattern `reaction` names, beyond plain
        on/off/toggle (e.g. an LED's "blink"/"flash_N"). Implemented by a
        concrete module for its own hardware - list such reactions in the
        module's own `reactions` (see `LEDModule`). The implementation is
        responsible for setting `self._blink_task` to the task driving the
        pattern (and `self._is_blinking` while it should block ordinary
        flags, see `FlagSource.suppress_while_blinking`) so `_stop_pattern()`
        can cancel it generically."""
        raise NotImplementedError(f"{type(self).__name__} has no pattern reaction {reaction!r}")

    async def _stop_pattern(self) -> None:
        """Cancel whatever pattern task (see `_apply_pattern_reaction`) is
        currently driving the output, if any."""
        self._is_blinking = False
        if self._blink_task is not None:
            self._blink_task.cancel()
            try:
                await self._blink_task
            except asyncio.CancelledError:
                pass
            self._blink_task = None

class InputModule(Module):
    """Base for modules that read a single GPIO pin as input (a button, a
    PIR sensor, ...). Polls `_read()` in the background and calls
    `_on_activated()` on every inactive -> active transition (e.g. a button
    press) - `init()`/`on_event()`/what `_on_activated()` does stay
    module-specific and still need implementing.
    """

    poll_interval: float = 0.05

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        schema = dict(super().get_settings_schema())
        schema.setdefault("pin", {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"})
        return schema

    @property
    def pin(self) -> int:
        return self.settings["pin"]

    def __init__(
        self,
        name: str,
        bus: Any,
        config: dict[str, Any] | None = None,
        settings_path: Path | None = None,
    ) -> None:
        super().__init__(name, bus, config, settings_path)

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
