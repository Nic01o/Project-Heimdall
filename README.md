# Alarm Clock

A modular alarm clock framework with a core scheduler, event-driven architecture, and plugin-based module system that enables extensibility for features like light, audio, microphone input, or remote control while keeping the codebase maintainable and testable.

## Philosophy

Rather than monolithic spaghetti or over-engineered microservices, this project follows an "OS-like" design: a single core process (Python + asyncio) orchestrates a scheduler and an event bus, while all features (alarms, lights, sound, UI) are implemented as loosely-coupled plugins. Plugins communicate only through events—they never know about each other directly. This keeps the codebase clean, testable, and easy to extend.

The architecture supports gradual optimization: modules remain in-process for simplicity and speed, but the plugin transport layer is abstracted so individual modules can be moved into their own process later if performance demands it.

## Quick Start

```bash
git clone <repo>
cd alarm-clock
pip install -r requirements.txt

# Run tests
python -m pytest

# Start the daemon
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
│   │   ├── webapi/
│   │   └── ...
│   ├── daemon.py              # Main entry point
│   └── exceptions.py          # Custom errors
├── tests/
│   ├── test_scheduler.py
│   ├── test_event_bus.py
│   └── ...
├── config/
│   └── default.yaml           # Default configuration
├── systemd/
│   └── alarmclock.service     # systemd unit file
├── requirements.txt
└── README.md
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

## REST API

Once the webapi module is enabled, interact with the daemon via HTTP:

```bash
# Get all alarms
curl http://localhost:5000/alarms

# Create a new alarm
curl -X POST http://localhost:5000/alarms \
  -H 'Content-Type: application/json' \
  -d '{"time": "07:00", "label": "Wake up", "enabled": true}'

# Toggle a module
curl -X POST http://localhost:5000/modules/light/enable
```

## Development Roadmap

**Phase 1 (Foundation)**
- [x] Core architecture
- [ ] Scheduler + persistence
- [ ] Event bus
- [ ] Plugin interface
- [ ] Sound module (mock)
- [ ] Unit tests & logging
- [ ] 

**Phase 2 (Features)**
- [ ] systemd integration
- [ ] REST API
- [ ] Light module
- [ ] Basic CLI

**Phase 3 (Polish)**
- [ ] Web UI
- [ ] Microphone module
- [ ] Text panel integration
- [ ] Remote adapter (optional)

## License

MIT
