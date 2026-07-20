"""Integration test: verifies the full Core -> Bus -> Module round trip.

Wires a real Scheduler, EventBus, and Module (the mock example module) together
exactly as the daemon does, fires an alarm, and confirms the module reacts to
`alarm.triggered` and reports back through the bus.
"""

import asyncio
import datetime

from alarmclock.core.alarm import Alarm
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.mymodule.mymodule import MyModule


def test_core_to_bus_to_module_round_trip():
    async def scenario():
        bus = EventBus()
        scheduler = Scheduler(bus, timezone="UTC")

        module = MyModule("mymodule", bus, {"enabled": True})
        await module.init()
        await module.enable()

        action_done_events = []

        async def on_action_done(payload):
            action_done_events.append(payload)

        bus.subscribe("mymodule.action_done", on_action_done)

        alarm = scheduler.add_alarm(
            Alarm(at=datetime.datetime.now(scheduler.tz) + datetime.timedelta(milliseconds=50))
        )

        await scheduler.start()
        await asyncio.sleep(0.2)
        await scheduler.stop()

        assert module.enabled is True
        assert len(action_done_events) == 1
        assert action_done_events[0] == {"status": "ok"}
        assert scheduler.get_alarm(alarm.id) is None  # one-shot removed after firing

    asyncio.run(scenario())
