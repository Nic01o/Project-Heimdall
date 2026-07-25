# Alarm Clock

A modular alarm clock framework with a core scheduler, event-driven architecture, and plugin-based module system that enables extensibility for features like light, audio, microphone input, or remote control while keeping the codebase maintainable and testable.

## Philosophy

Rather than monolithic spaghetti or over-engineered microservices, this project follows an "OS-like" design: a single core process (Python + asyncio) orchestrates a scheduler and an event bus, while all features (alarms, lights, sound, UI) are implemented as loosely-coupled plugins. Plugins communicate only through events—they never know about each other directly. This keeps the codebase clean, testable, and easy to extend.

The architecture supports gradual optimization: modules remain in-process for simplicity and speed, but the plugin transport layer is abstracted so individual modules can be moved into their own process later if performance demands it.

## Quick Start

```bash
git clone git@github.com:Nic01o/Project-Heimdall.git
cd Project-Heimdall

# Debian/Raspberry Pi OS won't let pip install system-wide (PEP 668) - use a venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests
python -m pytest

# Start the daemon (from the repo root, not from inside alarmclock/)
python -m alarmclock.daemon
```

## Architecture

```
┌─────────────────────────────────────────┐
│         Alarm Clock Daemon              │
│         (Python, asyncio)               │
├─────────────────────────────────────────┤
│  Core                    │  Modules     │
│  • Scheduler             │  • Sound     │
│  • Event Bus             │  • Light     │
│  • Config Manager        │  • Web UI    │
│  • Persistence           │  • (others)  │
└─────────────────────────────────────────┘
↕ systemd watchdog   ↕ (optional remote adapters)           
```

**Core**: Manages alarm scheduling, fires events, persists state (JSON key/value store; SQLite is a possible later upgrade). Knows nothing about hardware.

**Modules**: Attach via plugin interface, listen for events, emit their own events. Each module has a mock (testable) and real (hardware-aware) implementation.

**Event Bus**: Pub/Sub within the process. Transport layer is pluggable—initially in-memory, later Unix-socket for remote modules.

**Watchdog**: systemd integration to auto-restart the daemon if it hangs.

## Project Structure

```
Project-Heimdall/
├── alarmclock/
│   ├── core/
│   │   ├── alarm.py            # Sleep plan data model (SleepPlan, SleepPlanGroup, Weekday)
│   │   ├── scheduler.py        # Sleep plan scheduling logic
│   │   ├── event_bus.py        # Event pub/sub
│   │   ├── config.py           # YAML config loading
│   │   └── persistence.py      # JSON key/value store (JSONStore)
│   ├── modules/
│   │   ├── base.py             # Plugin interface (abc.ABC) + settings pattern
│   │   ├── settings_types.py   # Shared field-type vocabulary + validation
│   │   ├── mymodule/
│   │   │   └── mymodule.py     # Minimal example/mock module
│   │   ├── webui/
│   │   │   ├── webui.py        # REST API + server-rendered control panel
│   │   │   ├── templates/      # Jinja2 templates (index, settings form, widgets)
│   │   │   └── static/         # CSS
│   │   ├── sound/              # planned
│   │   └── light/              # planned
│   └── daemon.py               # Main entry point
├── tests/
│   ├── test_scheduler.py
│   ├── test_settings.py
│   ├── test_persistence.py
│   ├── test_webui.py           # REST API
│   ├── test_webui_pages.py     # server-rendered pages
│   └── test_integration.py     # Core -> Bus -> Module round trip
├── config/
│   └── config.yaml            # Default configuration
├── requirements.txt
├── README.md
└── todo.TODO                  # well, todos
```

## Plugin System

Extend the clock by creating a new module. Implement the base interface:

```python
from alarmclock.modules.base import Module
 
class MyModule(Module):
    async def init(self):
        """Called once at startup."""
        self.bus.subscribe('alarm.triggered', self.on_alarm)
    
    async def on_alarm(self, event):
        """Event handler."""
        # Do something with the alarm
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
        """Validate and persist new settings. No side effects here—
        the module itself decides what to do with changed values via
        its own event handlers (e.g. reacting to `<module>.settings_changed`)."""
        pass
```

The three settings methods above already have working generic implementations on
`Module` itself (empty schema, `self.settings`, and validate+merge+persist+emit) - a
module only needs to override them for a real special case, most just override
`get_settings_schema()`.

### Module Settings Pattern
 
Modules own their settings schema; the core and UI stay generic. This lets the
web UI (or CLI) render a settings form for *any* module without knowing
anything about its internals. A module only needs to override
`get_settings_schema()` - the generic `update_settings()` on `Module` already
validates, merges, persists, and emits the change event:
 
```python
class LightModule(Module):
    async def get_settings_schema(self):
        return {
            "gpio_pin": {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"},
            "brightness": {"type": "int", "min": 0, "max": 100, "label": "Brightness"},
        }

    # get_settings() and update_settings() are inherited from Module - override
    # only for a real special case, e.g.:
    async def update_settings(self, values):
        validated = self._validate(values)
        self.settings = {**self.settings, **validated}
        if self.store is not None:
            self.store.set(f"modules.{self.name}", self.settings)
        await self.bus.emit('light.settings_changed', self.settings)
```
 
`update_settings()` only validates, stores, and persists—no hardware side
effects. The module reacts to its own `*.settings_changed` event (or on its
next regular cycle) to actually apply the change. This keeps "change config"
cleanly separated from "act on config".
 
> **Note:** `update_settings()` is a method on the module itself, callable
> internally (e.g. from the CLI) via the event bus. Only the **`webui`
> module** is allowed to expose it over HTTP. Other modules are free to run
> their own online APIs for their own purposes (e.g. a weather module
> calling out to a forecast API), but none of them—and not the core
> either—get an HTTP endpoint for changing settings. That path exists
> exclusively through the web UI.
 
### Shared Field-Type Vocabulary
 
For `webui` to render a settings tab for *any* module without knowing that
module, schema fields declare a `type` from a small, fixed vocabulary that
both backend (validation) and frontend (widget choice) understand:
 
```python
# modules/settings_types.py
"""Shared vocabulary for settings fields.
Each type has one fixed meaning for validation AND rendering."""
 
FIELD_TYPES = {
    "int":         {"widget": "number"},
    "float":       {"widget": "number"},
    "bool":        {"widget": "toggle"},
    "string":      {"widget": "text"},
    "password":    {"widget": "password"},   # masked in the UI, e.g. WiFi key
    "select":      {"widget": "dropdown"},   # needs "options"
    "multiselect": {"widget": "checkboxes"}, # needs "options"
    "color":       {"widget": "colorpicker"},
}
```
 
`widget` is optional per field—every `type` has a sensible default widget,
but a field can override it (e.g. an `int` rendered as a `slider` instead of
a plain number box):
 
```python
class LightModule(Module):
    async def get_settings_schema(self):
        return {
            "gpio_pin":   {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"},
            "brightness": {"type": "int", "min": 0, "max": 100, "label": "Brightness", "widget": "slider"},
            "color":      {"type": "color", "label": "Light color"},
            "mode":       {"type": "select", "options": ["fade", "instant"], "label": "Transition"},
        }
```
 
Because the vocabulary is fixed, validation itself can move into the
`Module` base class instead of being reimplemented per module:
 
```python
class Module(abc.ABC):
    async def update_settings(self, values: dict) -> None:
        schema = await self.get_settings_schema()
        validated = validate_against_schema(values, schema)  # generic, from settings_types.py
        self.settings = {**self.settings, **validated}
        if self.store is not None:
            self.store.set(f"modules.{self.name}", self.settings)  # JSONStore, see core/persistence.py
        await self.bus.emit(f'{self.name}.settings_changed', self.settings)
```

`self.store` is an optional `JSONStore` the daemon passes to every module at
construction time (`core/persistence.py`). It's also what restores settings on
the next boot: `Module.__init__` overlays any previously persisted
`modules.<name>` value on top of the YAML defaults from `config.yaml`.
 
Modules only override this when they have a real special case.
 
To label the settings tab itself, modules can expose an optional class
attribute rather than smuggling it into the schema (keeps it from colliding
with real settings fields):
 
```python
class LightModule(Module):
    display_name = "Light"
    icon = "lightbulb"
```
 
### `webui` Widget Rendering

`webui` is server-rendered (FastAPI + Jinja2, classic forms, no JS/build
step required) rather than a JS single-page app, but the idea is the same:
one generic template maps `field.widget` onto markup, so adding a settings
tab for a new module needs zero module-specific frontend code.

`webui.py` resolves each field's widget from `FIELD_TYPES` before rendering
(`_resolve_widgets()`), then the `_widgets.html` Jinja2 macro switches on
`field.widget` - never on `field.type` directly:

```jinja
{% macro render_field(name, field, value) %}
  {% if field.widget == "number" %}
    <input type="number" name="{{ name }}" value="{{ value }}">
  {% elif field.widget == "toggle" %}
    <input type="checkbox" name="{{ name }}" {% if value %}checked{% endif %}>
  {% elif field.widget == "dropdown" %}
    <select name="{{ name }}">...</select>
  {# ... one branch per FIELD_TYPES widget ... #}
  {% endif %}
{% endmacro %}
```

The settings page (`GET /modules/<name>/settings`) loops over
`get_settings_schema()` and renders one field per entry; submitting the form
(`POST /modules/<name>/settings`) parses the form data back into typed
values and calls `module.update_settings(...)` - a classic Post/Redirect/Get,
no JavaScript involved. `webui` discovers modules generically via the module
registry it's given at startup (see "Web UI & REST API" below) and only
shows a *Settings* link for modules whose `get_settings_schema()` is
non-empty.

Register a module in `config/config.yaml`:

```yaml
modules:
  mymodule:
    enabled: true
    class: 'alarmclock.modules.mymodule.mymodule:MyModule'
```

## Configuration

All runtime settings live in a single YAML file (`config/config.yaml`). Each
enabled module needs a `class` (dotted module path + `:ClassName`) that the
daemon dynamically imports and instantiates - everything else in a module's
block is passed through as its initial `config`/`settings`:

```yaml
scheduler:
  timezone: 'Europe/Berlin'

persistence:
  path: 'data/state.json'    # JSON store for the sleep plan + module settings; defaults to this if omitted

modules:
  mymodule:
    enabled: false
    class: 'alarmclock.modules.mymodule.mymodule:MyModule'

  sound:
    enabled: false
    device: '/dev/audio'

  light:
    enabled: false
    gpio_pin: 17
    brightness: 100

  webui:
    enabled: true
    class: 'alarmclock.modules.webui.webui:WebUIModule'
    host: '0.0.0.0'
    port: 5000
```

Persisted module settings (written via `update_settings()`) take priority
over these YAML defaults on the next boot - the YAML value is only the
*initial* value the first time a module runs.

## Web UI & REST API

When `webui` is enabled, it's both a JSON REST API (under `/api`) and a
server-rendered browser control panel on the same port - `GET /` serves the
control panel's home page directly.

Unlike other modules, `webui` gets direct references to the `Scheduler` and
the full module registry (`attach_context()`, called by the daemon once every
module has been `init()`'d) - a deliberate, singular exception to "modules
only talk through the bus", since being the one cross-module control plane is
its entire purpose.

There is exactly **one** sleep plan (no arbitrary list of alarms): 1-7
disjoint weekday groups, each with its own wake time (a weekday not in any
group simply has no alarm), plus one-time exceptions and a pause switch.
Editing a group's time always says whether the change is for the next
wake-up only or permanent - see `SleepPlan`/`SleepPlanGroup` in
`alarmclock/core/alarm.py` and the scheduling logic in
`alarmclock/core/scheduler.py`.

REST API (JSON):

```bash
# Read the whole plan
curl http://localhost:5000/api/plan

# Create a group covering Monday+Tuesday, or a single still-free day
curl -X POST http://localhost:5000/api/plan/groups \
  -H 'Content-Type: application/json' \
  -d '{"days": [0, 1], "time": "07:00"}'
curl -X POST http://localhost:5000/api/plan/days/wednesday \
  -d '{"time": "07:30", "permanent": true}'

# Change a group's time - permanently, or just for its next occurrence
curl -X POST http://localhost:5000/api/plan/groups/<id> -d '{"time": "07:15", "permanent": true}'
curl -X POST http://localhost:5000/api/plan/groups/<id> -d '{"time": "09:00", "permanent": false}'

# Delete a group (frees its days again)
curl -X DELETE http://localhost:5000/api/plan/groups/<id>

# Pause the whole plan (editing any group/day reactivates it)
curl -X POST http://localhost:5000/api/plan/disable

# Stop or snooze a ringing alarm
curl -X POST http://localhost:5000/api/plan/stop
curl -X POST http://localhost:5000/api/plan/snooze -d '{"minutes": 9}'

# Modules: list, enable/disable, settings
curl http://localhost:5000/api/modules
curl -X POST http://localhost:5000/api/modules/light/enable
curl http://localhost:5000/api/modules/light/settings/schema
curl http://localhost:5000/api/modules/light/settings
curl -X POST http://localhost:5000/api/modules/light/settings -d '{"brightness": 80}'
```

Browser UI (`/...`, classic HTML forms, no JS): `/` shows the sleep
plan's groups and any still-free weekdays, each with a small form to set a
new time (next-only or permanent) and a group delete button, a form to bundle
free days into a new group, and global stop/snooze/deactivate controls;
`/modules/<name>/settings` renders that module's settings form generically
from its schema (see "`webui` Widget Rendering" above). These are separate
routes from the JSON API above (which lives under `/api`) even though they
call the same `Scheduler`/`Module` methods under the hood - it keeps the
REST API a pure JSON contract.

## Testing

Modules come with mock implementations so you can test the entire system without hardware:

```bash
# Run all tests
python -m pytest

# Run tests for a specific area
python -m pytest tests/test_scheduler.py
python -m pytest tests/test_webui.py tests/test_webui_pages.py

# Run with coverage
python -m pytest --cov=alarmclock
```

## Running as a systemd Service

> **Not implemented yet** - there's no `systemd/` directory or unit file in
> the repo yet (see `todo.TODO`). This is the intended usage once it exists.

Copy the service file and enable it:

```bash
sudo cp systemd/alarmclock.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable alarmclock
sudo systemctl start alarmclock
```

Check logs:

```bash
sudo systemctl status alarmclock
journalctl -u alarmclock -f
```




## Module Ideas

Brainstorm of possible future modules (unsorted, non-binding):

- Light
- LCD Display
- Local GUI
- Button Input
- Speaker
- Microphone
- Alarm sound library (multiple sounds/playlists instead of just one sound)
- Sunrise simulation (gradually brighten light before the alarm)
- Snooze via motion/presence sensor (e.g. PIR sensor)
- Voice control (set/snooze alarm via voice command, builds on microphone)
- Calendar sync (adjust alarms to the first appointment of the day)
- Bluetooth speaker output (alternative/addition to speaker)
- NFC/RFID tag to turn off (alarm can only be stopped via tag scan)
- Push notification (status to phone: alarm triggered, module error)
- Home Assistant/MQTT bridge (integration into existing smart home systems)

MIT
