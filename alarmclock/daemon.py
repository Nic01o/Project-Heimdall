"""Main entry point: wires config -> event bus -> scheduler -> modules and runs the daemon loop."""

from __future__ import annotations
import argparse
import asyncio
import tomllib
from pathlib import Path
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.core.webui_controller import WebUIController
from alarmclock.modules.base import Module, available_module_types
from alarmclock.modules.settings_types import detect_system_timezone
from alarmclock.core.logger_wrapper import logger

CONFIG_DIR: Path = Path(__file__).resolve().parent.parent / "config"
SETTINGS_PATH: Path = CONFIG_DIR / "settings.toml"

WATCHED_EVENTS = ("alarm.triggered", "alarm.stopped", "alarm.snoozed")


async def run(demo_alarm_seconds: int | None) -> None:
    try:
        with open(SETTINGS_PATH, "rb") as f:
            raw_settings = tomllib.load(f)
    except FileNotFoundError:
        raw_settings = {}

    registry = raw_settings.get("registry", {})
    # The webui's "Zeitzone" setting is marked requires_restart, so it's fine
    # to read it straight from disk here rather than through WebUIController
    # (which isn't constructed yet) - this is the timezone the scheduler
    # computes every wake-up trigger in. Falls back to the host's own system
    # timezone (not hardcoded UTC) so a fresh install without any saved
    # settings.toml entry still fires alarms at the right wall-clock time.
    timezone = raw_settings.get("settings", {}).get("webui", {}).get("timezone") or detect_system_timezone()

    logger.info(f"Loaded {len(registry)} hardware instance(s)", module_name="daemon")

    module_types = available_module_types(SETTINGS_PATH)

    bus = EventBus()
    webui_controller = WebUIController(name="webui", bus=bus, settings_path=SETTINGS_PATH)
    scheduler = Scheduler(bus=bus, name="scheduler", settings_path=SETTINGS_PATH, timezone=timezone)

    await scheduler.load_config(scheduler.name)
    await webui_controller.load_config(webui_controller.name)

    # Setup modules (including core webui controller)
    modules: list[Module] = []
    for instance_id, entry in registry.items():
        module_type = entry["module"]
        wiring = {k: v for k, v in entry.items() if k != "module"}

        module_cls = module_types.get(module_type)
        if module_cls is None:
            logger.warning(
                f"instance {instance_id!r} wants module type {module_type!r}, "
                "which isn't listed in [module_types]",
                module_name="daemon",
            )
            continue

        module = module_cls(name=instance_id, bus=bus, config=wiring, settings_path=SETTINGS_PATH)
        await module.load_config(instance_id)
        modules.append(module)

    webui_controller.attach_context(scheduler, modules)

    # Initialize all modules
    await asyncio.gather(
        *[module.init() for module in modules],
        webui_controller.init()
    )

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
