"""Main entry point: wires config -> event bus -> scheduler -> modules and runs the daemon loop."""

from __future__ import annotations
import argparse
import asyncio
import importlib
import logging
import yaml
from pathlib import Path
from typing import Any
from alarmclock.core.persistence import Store, _load_settings
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
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

    # get activated modules and their settings

    # initiate modules with retrieved settings

    # start main loop (implementation comes later)

    pass

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
