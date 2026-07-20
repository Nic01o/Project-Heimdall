"""Main entry point: wires config -> event bus -> scheduler -> modules and runs the daemon loop."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import importlib
import logging
import signal
from pathlib import Path
from typing import Any

from alarmclock.core.alarm import Alarm
from alarmclock.core.config import load_config
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.base import Module

logger = logging.getLogger("alarmclock.daemon")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"

WATCHED_EVENTS = ("alarm.triggered", "alarm.stopped", "alarm.snoozed", "mymodule.action_done")


def _load_module(name: str, mod_cfg: dict[str, Any], bus: EventBus) -> Module:
    dotted_path, class_name = mod_cfg["class"].split(":")
    mod = importlib.import_module(dotted_path)
    cls = getattr(mod, class_name)
    return cls(name, bus, mod_cfg)


async def _load_modules(cfg: dict[str, Any], bus: EventBus, scheduler: Scheduler) -> list[Module]:
    """Instantiate and init() every enabled module, then attach cross-module
    context (currently only used by webui) before enable()ing any of them.

    This ordering matters: a module's attach_context() (e.g. webui) needs the
    full registry, which only exists once every module has been init()'d -
    regardless of where it sits in the YAML config.
    """
    modules: list[Module] = []
    for name, mod_cfg in cfg.get("modules", {}).items():
        if not mod_cfg.get("enabled"):
            continue
        instance = _load_module(name, mod_cfg, bus)
        await instance.init()
        modules.append(instance)
        logger.info("module %r initialized", name)

    for instance in modules:
        attach_context = getattr(instance, "attach_context", None)
        if attach_context is not None:
            attach_context(scheduler, modules)

    for instance in modules:
        await instance.enable()
        logger.info("module %r enabled", instance.name)

    return modules


def _log_events(bus: EventBus) -> None:
    """Subscribe a simple logger to the events worth watching live."""

    async def log(event: str, payload: dict[str, Any]) -> None:
        logger.info("event %s: %s", event, payload)

    for event in WATCHED_EVENTS:
        bus.subscribe(event, lambda payload, event=event: log(event, payload))


async def run(cfg_path: Path, demo_alarm_seconds: int | None) -> None:
    cfg = load_config(cfg_path)
    bus = EventBus()
    _log_events(bus)

    scheduler = Scheduler(bus, timezone=cfg.get("scheduler", {}).get("timezone", "UTC"))
    modules = await _load_modules(cfg, bus, scheduler)

    if demo_alarm_seconds is not None:
        now = datetime.datetime.now(scheduler.tz)
        alarm = scheduler.add_alarm(
            Alarm(at=now + datetime.timedelta(seconds=demo_alarm_seconds), label="demo")
        )
        logger.info("demo alarm %s scheduled for %s", alarm.id, alarm.at)

    await scheduler.start()
    logger.info("daemon started, waiting for alarms (Ctrl+C to stop)")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    logger.info("shutting down")
    await scheduler.stop()
    for module in modules:
        await module.disable()


def main() -> None:
    parser = argparse.ArgumentParser(description="Alarm clock daemon")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--demo-alarm-seconds",
        type=int,
        default=None,
        help="Schedule a one-shot demo alarm this many seconds after startup",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run(args.config, args.demo_alarm_seconds))


if __name__ == "__main__":
    main()
