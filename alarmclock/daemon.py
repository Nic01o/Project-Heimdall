"""Main entry point: wires config -> event bus -> scheduler -> modules and runs the daemon loop."""

from __future__ import annotations
import argparse
import asyncio
import importlib
import logging
import os
from pathlib import Path
from typing import Any
from alarmclock.core.persistence import Store, _load_settings
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.core.webui_controller import WebUIController
from alarmclock.modules.base import Module

logger = logging.getLogger("alarmclock.daemon")

# Konfigurationspfade als absolute Paths (bereits ausgewertet)
HARDWARE_CONFIG_PATH: Path = __import__("pathlib").Path(__file__).resolve().parent.parent / "config" / "hardware_config.yaml"
SETTINGS_PATH: Path = __import__("pathlib").Path(__file__).resolve().parent.parent / "config" / "settings.yaml"

WATCHED_EVENTS = ("alarm.triggered", "alarm.stopped", "alarm.snoozed")


async def run(demo_alarm_seconds: int | None) -> None:
    # load settings from persistent storage (sync function now)
    settings_data = _load_settings(HARDWARE_CONFIG_PATH, SETTINGS_PATH)

    logger.info(f"Loaded configuration with {len(settings_data)} setting groups")

    # Setup event bus
    bus = EventBus()

    # Setup scheduler
    scheduler = Scheduler(bus=bus)

    # Setup modules (including core webui controller)
    modules: list[Module] = []

    # Create a store for persistence
    store = Store()

    # Initialize WebUI Controller directly as a core component
    # This is the special case - webui gets direct access to scheduler and modules
    webui_controller = WebUIController(
        name="webui",
        bus=bus,
        config=settings_data.get("webui", {}),  # Get webui settings from config
        store=store
    )

    # Load regular modules from config
    module_configs = settings_data.get("modules", {})
    for module_name, module_config in module_configs.items():
        if module_name == "webui":
            # Skip - we already created the core webui controller above
            continue

        # Create module instance (this is simplified - you'd need to dynamically load modules)
        try:
            module_class = importlib.import_module(f"alarmclock.modules.{module_name}")
            if hasattr(module_class, module_name.capitalize()):
                module_instance = getattr(module_class, module_name.capitalize())(
                    name=module_name,
                    bus=bus,
                    config=module_config,
                    store=store
                )
                modules.append(module_instance)
        except (ImportError, AttributeError) as e:
            logger.warning(f"Could not load module {module_name}: {e}")

    # Attach context to webui controller (this gives it access to scheduler and all modules)
    webui_controller.attach_context(scheduler, modules)

    # Initialize all modules
    await asyncio.gather(
        *[module.init() for module in modules],
        webui_controller.init()
    )

    # Enable modules that should be enabled
    # Check if modules were previously enabled (from persisted settings) or use config
    for module in modules:
        # Load previous state from store or check config
        if module.settings.get("active", True):  # Default to enabled if not specified
            await module.enable()

    # Enable webui controller (it's a core component, so it should be enabled by default)
    if settings_data.get("webui", {}).get("enabled", True):
        await webui_controller.enable()
    else:
        await webui_controller.disable()

    # Start scheduler
    await scheduler.start()

    # If demo alarm requested, schedule it
    if demo_alarm_seconds is not None:
        logger.info(f"Scheduling demo alarm in {demo_alarm_seconds} seconds")
        # Implementation for demo alarm would go here

    # Main loop - keep the daemon running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        # Cleanup tasks (this is simplified)
        await scheduler.stop()
        for module in modules:
            await module.disable()
        await webui_controller.disable()

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

    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    asyncio.run(run(args.demo_alarm_seconds))


if __name__ == "__main__":
    main()
