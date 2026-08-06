"""Integration test: verifies the full Core -> Bus -> Module round trip.

Wires a real Scheduler, EventBus, and Module (the mock example module) together
exactly as the daemon does, fires an alarm, and confirms the module reacts to
`alarm.triggered` and reports back through the bus.
"""

import asyncio
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.mymodule.mymodule import MyModule


def test_core_to_bus_to_module_round_trip(tmp_path):
    async def scenario():
        bus = EventBus()
        scheduler = Scheduler(bus, timezone="UTC", name="scheduler", settings_path=tmp_path / "settings.toml")

        module = MyModule("mymodule", bus, {"enabled": True})
        await module.init()

        action_done_events = []

        async def on_action_done(payload):
            action_done_events.append(payload)

        bus.subscribe("mymodule.action_done", on_action_done)

        # "ring" 50ms from now via the snooze mechanism - a one-time trigger
        # independent of any weekly sleep-plan group.
        await scheduler.snooze_alarm(minutes=50 / 60000)

        await scheduler.start()
        await asyncio.sleep(0.2)
        await scheduler.stop()

        assert len(action_done_events) == 1
        assert action_done_events[0] == {"status": "ok"}
        assert (await scheduler.get_plan()).snooze_until is None  # cleared after firing

    asyncio.run(scenario())
