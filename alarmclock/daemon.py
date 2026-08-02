"""Main entry point: wires config -> event bus -> scheduler -> modules and runs the daemon loop."""

from __future__ import annotations
import argparse
import asyncio
import importlib
from pathlib import Path
from alarmclock.core.settings import HardwareRegistry, SettingsStore
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.core.webui_controller import WebUIController
from alarmclock.modules.base import Module
from alarmclock.core.logger_wrapper import logger

CONFIG_DIR: Path = Path(__file__).resolve().parent.parent / "config"
HARDWARE_PATH: Path = CONFIG_DIR / "hardware.toml"
SETTINGS_PATH: Path = CONFIG_DIR / "settings.toml"

WATCHED_EVENTS = ("alarm.triggered", "alarm.stopped", "alarm.snoozed")


def _load_module_class(module_type: str) -> type[Module] | None:
    """Import alarmclock.modules.<module_type>.<module_type> and return the
    concrete Module subclass it defines, if any."""
    try:
        module_file = importlib.import_module(f"alarmclock.modules.{module_type}.{module_type}")
    except ImportError as e:
        logger.warning(f"Could not import module type {module_type!r}: {e}", module_name="daemon")
        return None
    for obj in vars(module_file).values():
        if (
            isinstance(obj, type)
            and issubclass(obj, Module)
            and obj is not Module
            and obj.__module__ == module_file.__name__
        ):
            return obj
    return None


async def run(demo_alarm_seconds: int | None) -> None:
    hardware = HardwareRegistry(HARDWARE_PATH)
    store = SettingsStore(SETTINGS_PATH)
    instances = hardware.instances()

    logger.info(f"Loaded {len(instances)} hardware instance(s)", module_name="daemon")

    # Setup event bus
    bus = EventBus()

    # Setup scheduler
    scheduler = Scheduler(bus=bus)

    # Setup modules (including core webui controller)
    modules: list[Module] = []

    # Initialize WebUI Controller directly as a core component
    # This is the special case - webui gets direct access to scheduler and modules
    webui_controller = WebUIController(
        name="webui",
        bus=bus,
        store=store,
    )

    # Build regular module instances from the hardware registry - each
    # instance's id becomes its settings-store key (see base.py), and every
    # other field in its hardware.toml entry (driver, overrides, ...) is
    # pure wiring, passed through as `config=` untouched.
    for instance in instances:
        instance_id = instance["id"]
        module_type = instance["module"]
        wiring = {k: v for k, v in instance.items() if k not in ("id", "module")}

        module_cls = _load_module_class(module_type)
        if module_cls is None:
            continue

        modules.append(module_cls(name=instance_id, bus=bus, config=wiring, store=store))

    # Attach context to webui controller (this gives it access to scheduler and all modules)
    webui_controller.attach_context(scheduler, modules)

    # Initialize all modules
    await asyncio.gather(
        *[module.init() for module in modules],
        webui_controller.init()
    )

    # Enable modules whose persisted (or default) `active` setting is true.
    # Calling enable()/disable() directly (not set_active()) since we're
    # only reflecting the already-loaded state, not changing it - no need
    # to re-persist what was just read.
    for module in modules:
        if module.settings.get("active", True):
            await module.enable()
        else:
            await module.disable()

    # WebUI is a core component, enabled by default (see WebUIController's
    # own `enabled = True` in __init__). Disabling it entirely for
    # webui-less devices (D1) isn't wired up yet - tracked separately.
    await webui_controller.enable()

    # Start scheduler
    await scheduler.start()

    # If demo alarm requested, schedule it
    if demo_alarm_seconds is not None:
        logger.info(f"Scheduling demo alarm in {demo_alarm_seconds} seconds", module_name="daemon")
        # Implementation for demo alarm would go here

    # Main loop - keep the daemon running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        # todo (already in todo.todo)
        logger.info("Shutting down...", module_name="daemon")

def main() -> None:
    parser = argparse.ArgumentParser(description="Alarm clock daemon")

    parser.add_argument(
        "--demo-alarm-seconds",
        type=int,
        default=None,
        help="Schedule a one-shot demo alarm this many seconds after startup",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Root log level, e.g. DEBUG, INFO (DEBUG also traces every event bus emit)",
    )

    args = parser.parse_args()

    # Run the daemon with proper KeyboardInterrupt handling
    try:
        asyncio.run(run(args.demo_alarm_seconds))
    except KeyboardInterrupt:
        logger.info("Shutting down...", module_name="daemon")
        # Let the exception propagate - it will be handled by the main event loop
        raise


if __name__ == "__main__":
    main()
