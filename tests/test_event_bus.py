"""Tests for the core EventBus: pub/sub delivery, unsubscribe, handler
exception isolation, and the generic per-emit debug logging that lets any
new event ("flag") written by a module get traced without extra code.
"""

import asyncio
import logging

from alarmclock.core.event_bus import EventBus


def test_subscribed_handler_receives_emitted_payload():
    async def scenario():
        bus = EventBus()
        received = []

        async def handler(payload):
            received.append(payload)

        bus.subscribe("thing.happened", handler)
        await bus.emit("thing.happened", {"value": 42})

        assert received == [{"value": 42}]

    asyncio.run(scenario())


def test_emit_without_payload_delivers_empty_dict():
    async def scenario():
        bus = EventBus()
        received = []

        async def handler(payload):
            received.append(payload)

        bus.subscribe("thing.happened", handler)
        await bus.emit("thing.happened")

        assert received == [{}]

    asyncio.run(scenario())


def test_unsubscribe_stops_delivery():
    async def scenario():
        bus = EventBus()
        received = []

        async def handler(payload):
            received.append(payload)

        bus.subscribe("thing.happened", handler)
        bus.unsubscribe("thing.happened", handler)
        await bus.emit("thing.happened", {"value": 1})

        assert received == []

    asyncio.run(scenario())


def test_emit_with_no_subscribers_does_not_raise():
    asyncio.run(EventBus().emit("nobody.listening", {"value": 1}))


def test_one_handler_raising_does_not_block_others():
    async def scenario():
        bus = EventBus()
        received = []

        async def bad_handler(payload):
            raise ValueError("boom")

        async def good_handler(payload):
            received.append(payload)

        bus.subscribe("thing.happened", bad_handler)
        bus.subscribe("thing.happened", good_handler)
        await bus.emit("thing.happened", {"value": 1})

        assert received == [{"value": 1}]

    asyncio.run(scenario())


def test_emit_logs_event_and_payload_at_debug_level(caplog):
    async def scenario():
        bus = EventBus()
        with caplog.at_level(logging.DEBUG, logger="alarmclock.core.event_bus"):
            await bus.emit("modulename.newflag", {"name": "modulename", "count": 3})

        assert "modulename.newflag" in caplog.text
        assert "'count': 3" in caplog.text

    asyncio.run(scenario())


def test_emit_debug_log_is_silent_below_debug_level(caplog):
    async def scenario():
        bus = EventBus()
        with caplog.at_level(logging.INFO, logger="alarmclock.core.event_bus"):
            await bus.emit("modulename.newflag", {"count": 3})

        assert caplog.records == []

    asyncio.run(scenario())
