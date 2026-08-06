# Alarm Clock - Development Documentation

This document provides technical details about the architecture, configuration, and extensibility of the Alarm Clock framework.

## Architecture

The project follows an "OS-driven" design where a central core manages scheduling and events.

```
┌─────────┐   ┌─────────────────────────────────────────┐   ┌─────────┐
│   LED   │◀──│         Alarm Clock Daemon              │──▶│ Button  │
│ (output)│   │         (Python, asyncio)               │   │ (input) │
└─────────┘   ├─────────────────────────────────────────┤   └─────────┘
              │  Core                                   │
              │  • Scheduler                            │
              │  • Event Bus                            │
              │  • Alarm                                │
              │  • WebUI Controller                     │
              │  • Settings                             │
              └─────────────────────────────────────────┘
```

**Core**: Manages alarm scheduling, fires events, persists state via `Configurable` (TOML-backed, see `alarmclock/modules/base.py`). It is hardware-agnostic.

**Modules**: Implemented as plugins that attach to the core via a standard interface. They listen for events and emit their own events. Each module provides both a mock implementation for testing and a real, hardware-aware implementation.

**Event Bus**: A Pub/Sub system within the process. The transport layer is pluggable—initially in-memory, with potential for Unix-socket or other mechanisms for remote modules.

**Watchdog**: Integrated with `systemd` to auto-restart the daemon if it hangs.

## Configuration and Storage

All configuration lives in a single file, `config/settings.toml`, split into three sections with different lifetimes and different "who's allowed to write this" rules.

### `[module_types]` — static, hand-edited only

Maps a module type name to the dotted Python path of the file defining its `Module` subclass:

```toml
[module_types]
led = "alarmclock.modules.led.led"
# mymodule = "alarmclock.modules.mymodule.mymodule"
```

A type is only selectable - by the daemon's registry loading (`daemon.py`) and by the web UI's "Add module" form (`/modules/new`) - once it has a line here. Having source files under `alarmclock/modules/<type>/` is not enough by itself; this is the deliberate step that exposes a module once it's ready, so half-finished modules can sit in the tree unused. Nothing at runtime ever writes to this section (see `available_module_types()` in `alarmclock/modules/base.py`); it only changes with a code change/deploy.

### `[registry]` — hardware wiring, normally hand-edited

One `[registry.<instance_id>]` table per module *instance*:

```toml
[registry.alarm_led]
module = "led"
pin = 17          # wiring - locked in the settings UI, config always wins
```

`module` picks the type (must be a key in `[module_types]`). Any other key is hardware wiring specific to that instance and becomes part of `locked_fields` - read-only on the module's settings page, since `Configurable.load_config()` treats registry values as authoritative over whatever a user might otherwise set.

This section is meant to be hand-edited - the documented fallback for headless/webui-less setups - but the web UI's "Add module" flow (`POST /modules/new`) also writes a plain `{module = "<type>"}` entry here at runtime via `write_registry_entry()`. A module created that way starts out with no locked fields at all, since no extra wiring is added.

### `[settings.<instance_id>]` — fully dynamic

The actual user-tunable state per instance (e.g. `active`, flag reactions, brightness) - only the diff from each field's schema default. Written automatically, never by hand:

- `Configurable.load_config()` backfills any schema default not yet covered by a registry value or an existing override, so a freshly created instance's full settings land on disk instead of only existing in memory.
- `Module.update_settings()` / `set_active()` persist here (via `save_config()`) every time a setting changes through the web UI.

### Summary

| Section | Written by | Hand-edited? |
|---|---|---|
| `[module_types]` | nobody at runtime (code change only) | always |
| `[registry]` | hand-editing, or "Add module" in the web UI | normally (web UI is the one exception) |
| `[settings.*]` | the daemon/web UI, on every settings change | no |

Known limitation: `Configurable.save_config()` and `write_registry_entry()` each do a full read-modify-write of `settings.toml` with no locking, so two writes racing (e.g. a settings save and an "Add module" request at the same time) can silently lose one of them - tracked in `todo.TODO` under "bugreports".

## Project Structure

```
Project-Heimdall/
├── alarmclock/
│   ├── core/
│   │   ├── alarm.py            # Sleep plan data model (SleepPlan, SleepPlanGroup, Weekday)
│   │   ├── scheduler.py        # Sleep plan scheduling logic
│   │   ├── event_bus.py        # Event pub/sub
│   
│   │   ├── logger_wrapper.py   # Logger wrapper for consistent logging
│   │   ├── webui_controller.py # WebUI controller with REST API and server-rendered pages
│   │   ├── templates/          # Jinja2 templates (index, settings form, widgets)
│   │   └── static/             # CSS
│   ├── modules/
│   │   ├── base.py             # Plugin interface (abc.ABC) + settings pattern
│   │   ├── settings_types.py   # Shared field-type vocabulary + validation
│   │   ├── mymodule/
│   │   │   ├── mymodule.py     # Minimal example/mock module
│   │   │   └── default_settings.yaml # Default settings for mymodule
│   │   ├── button/
│   │   │   ├── button.py       # Button module implementation
│   │   │   ├── mock.py         # Mock implementation for testing
│   │   │   └── real.py         # Real hardware implementation
│   │   └── led/
│   │       ├── led.py          # LED module implementation
│   │       ├── mock.py         # Mock implementation for testing
│   │       └── real.py         # Real hardware implementation
├── tests/
├── config/
│   └── settings.toml           # [module_types] static, [registry] normally hand-edited,
│                                # [settings.*] fully dynamic - see "Configuration and Storage"
├── requirements.txt
├── rpi_config_example.txt      # Configuration for Raspberry Pi
├── README.md                   # User guide
└── todo.TODO                   # Ongoing tasks
```

## Plugin System

### Implementation Interface

To extend the clock, create a new module implementing the `Module` base interface:

```python
from alarmclock.modules.base import Module

class MyModule(Module):
    async def init(self):
        """Called once at startup."""
        self.bus.subscribe('alarm.triggered', self.on_alarm)
    
    async def on_alarm(self, event):
        """Event handler."""
        await self.bus.emit('mymodule.action_done', {'status': 'ok'})
    
    async def enable(self):
        """Enable the module."""
        pass
    
    async def disable(self):
        """Disable the module."""
        pass
 
    async def get_settings_schema(self) -> dict:
        """Describe this module's settings (used by UI/CLI to render forms)."""
        return {}
 
    async def get_settings(self) -> dict:
        """Return current settings values."""
        return {}
 
    async def update_settings(self, values: dict) -> None:
        """Validate and persist new settings."""
        pass
```

The `Module` base class provides generic implementations for `get_settings()`, `update_settings()`, and the validation/persistence logic. A module only needs to override `get_settings_schema()` most of the time.

### Module Settings Pattern

Modules define their own settings schema, allowing the Web UI to render forms generically. 

**Shared Field-Type Vocabulary**: The `webui` uses a fixed vocabulary from `alarmclock/modules/settings_types.py` to determine which widget to render (e./g., `int` -> `number`, `bool` -> `toggle`).

```python
# modules/settings_types.py
FIELD_TYPES = {
    "int":         {"widget": "number"},
    "float":       {"widget": "number"},
    "bool":        {"widget": "toggle"},
    "string":      {"widget": "text"},
    "password":    {"widget": "password"},
    "select":      {"widget": "dropdown"},
    "multiselect": {"widget": "checkboxes"},
    "color":       {"widget": "colorpicker"},
}
```

### Web UI Implementation

The `webui` module uses FastAPI and Jinja2 for server-side rendering. It is a classic Post/Redirect/Get pattern with no complex JavaScript frontend required. 

When a user submits a settings form, the backend parses the data and calls `module.update_settings(...)`. The system handles the complexity of validating types and persisting changes to `settings.toml`.
