# Alarm Clock

A modular alarm clock framework with a core scheduler, event-driven architecture, and plugin-based module system that enables extensibility for features like light, audio, microphone input, or remote control while keeping the codebase maintainable and testable.

## Philosophy

Rather than monolithic spaghetti or over-engineered microservices, this project follows an "OS-like" design: a single core process (Python + asyncio) orchestrates a scheduler and an event bus, while all features (alarms, lights, sound, UI) are implemented as loosely-coupled plugins. Plugins communicate only through events—they never know about each other directly. This keeps the codebase clean, testable, and easy to extend.

The architecture supports gradual optimization: modules remain in-process for simplicity and speed, but the plugin transport layer is abstracted so individual modules can be moved into their own process later if performance demands it.

## Quick Start

```bash
git clone git@github.com:Nic01o/Project-Heimdall.git
cd alarm-clock


pip3 freeze > requirements.txt
pip3 install -r requirements.txt

# Run tests
python3 -m pytest

# Start the daemon
python3 -m alarmclock.daemon
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
│  • Config Manager        │  • Web API   │
│  • Persistence           │  • (others)  │
└─────────────────────────────────────────┘
↕ systemd watchdog   ↕ (optional remote adapters)           
```

**Core**: Manages alarm scheduling, fires events, persists state. Knows nothing about hardware.

**Modules**: Attach via plugin interface, listen for events, emit their own events. Each module has a mock (testable) and real (hardware-aware) implementation.

**Event Bus**: Pub/Sub within the process. Transport layer is pluggable—initially in-memory, later Unix-socket for remote modules.

**Watchdog**: systemd integration to auto-restart the daemon if it hangs.

## Project Structure

```
alarm-clock/
├── alarmclock/
│   ├── core/
│   │   ├── scheduler.py       # Alarm scheduling logic
│   │   ├── event_bus.py       # Event pub/sub
│   │   ├── config.py          # YAML config loading
│   │   └── persistence.py     # SQLite storage
│   ├── modules/
│   │   ├── base.py            # Plugin interface (abc.ABC)
│   │   ├── sound/
│   │   │   ├── __init__.py
│   │   │   ├── mock.py        # Test implementation
│   │   │   └── real.py        # Hardware implementation
│   │   ├── light/
│   │   ├── webui/             # frontend 
│   │   └── ...
│   ├── daemon.py              # Main entry point
│   └── exceptions.py          # Custom errors
├── tests/
│   ├── test_scheduler.py
│   ├── test_event_bus.py
│   └── ...
├── config/
│   └── config.yaml           # Default configuration
├── systemd/
│   └── alarmclock.service    # systemd unit file
├── requirements.txt
├── README.md
└── todo.TODO                 # well, todos
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

### Module Settings Pattern
 
Modules own their settings schema; the core and UI stay generic. This lets a
future web UI (or CLI) render a settings form for *any* module without
knowing anything about its internals:
 
```python
class LightModule(Module):
    async def get_settings_schema(self):
        return {
            "gpio_pin": {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"},
            "brightness": {"type": "int", "min": 0, "max": 100, "label": "Brightness"},
        }
 
    async def get_settings(self):
        return self.settings
 
    async def update_settings(self, values):
        validated = self._validate(values)
        self.settings = {**self.settings, **validated}
        await self.config.persist("modules.light", self.settings)
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
        await self.config.persist(f"modules.{self.name}", self.settings)
        await self.bus.emit(f'{self.name}.settings_changed', self.settings)
```
 
Modules only override this when they have a real special case.
 
To label the settings tab itself, modules can expose an optional class
attribute rather than smuggling it into the schema (keeps it from colliding
with real settings fields):
 
```python
class LightModule(Module):
    display_name = "Light"
    icon = "lightbulb"
```
 
### `webui` Widget Library
 
On the frontend, `webui` ships a small widget library that maps 1:1 onto
`FIELD_TYPES`, so adding a settings tab for a new module needs zero
module-specific frontend code:
 
```javascript
const WIDGETS = {
  number:      NumberField,
  slider:      SliderField,
  toggle:      ToggleField,
  text:        TextField,
  password:    PasswordField,
  dropdown:    SelectField,
  checkboxes:  MultiSelectField,
  colorpicker: ColorField,
};
 
function renderField(key, field, value, onChange) {
  const Widget = WIDGETS[field.widget ?? DEFAULT_WIDGET[field.type]];
  return <Widget label={field.label} value={value} onChange={v => onChange(key, v)} {...field} />;
}
```
 
`webui` discovers modules and builds one settings tab per module purely from
their schema:
 
```javascript
const modules = await fetch('/modules').then(r => r.json());
 
for (const mod of modules) {
  const schema = await fetch(`/modules/${mod.name}/settings/schema`).then(r => r.json());
  if (Object.keys(schema).length > 0) {
    renderSettingsTab(mod.display_name ?? mod.name, schema, mod.name);
  }
}
```


Register it in `config/default.yaml`:

```yaml
modules:
  mymodule:
    enabled: true
    class: 'alarmclock.modules.mymodule:MyModule'
```

## Configuration

All runtime settings live in a single YAML file. Modules are listed with their enabled status and parameters:

```yaml
scheduler:
  timezone: 'Europe/Berlin'

modules:
  sound:
    enabled: true
    device: '/dev/audio'
  
  light:
    enabled: true
    gpio_pin: 17
    brightness: 100

webapi:
  enabled: true
  host: '0.0.0.0'
  port: 5000
```

## Testing

Modules come with mock implementations so you can test the entire system without hardware:

```bash
# Run all tests
python -m pytest

# Run tests for a specific module
python -m pytest tests/test_sound.py

# Run with coverage
python -m pytest --cov=alarmclock
```

## Running as a systemd Service

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
