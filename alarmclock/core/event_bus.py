"""Async pub/sub event bus. Delivery is abstracted behind a Transport so a
Unix-socket transport can later replace the in-process one without touching
module code. Events are plain, JSON-serializable dicts.
"""

from __future__ import annotations

import abc
import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable
from alarmclock.core.logger_wrapper import logger

Handler = Callable[[dict[str, Any]], Awaitable[None]]


class Transport(abc.ABC):
    """Delivers events from publishers to subscribed handlers."""

    @abc.abstractmethod
    def subscribe(self, event: str, handler: Handler) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def unsubscribe(self, event: str, handler: Handler) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError


class LocalTransport(Transport):
    """
    An in-memory implementation of the Transport interface.

    This transport delivers events to all subscribed handlers within the same
    process using a local dictionary mapping event names to lists of handler
    functions. It supports asynchronous, concurrent execution of handlers
    when an event is published.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        handlers = self._handlers.get(event)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        handlers = list(self._handlers.get(event, ()))

        if not handlers:
            return

        results = await asyncio.gather(
            *(handler(payload) for handler in handlers),
            return_exceptions=True,
        )

        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                logger.exception(
                    "handler %r for event %r raised", handler, event, exc_info=result
                )


class EventBus:
    """Public pub/sub API used by modules. Delivery is delegated to Transport."""

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport or LocalTransport()

    def subscribe(self, event: str, handler: Handler) -> None:
        self._transport.subscribe(event, handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        self._transport.unsubscribe(event, handler)

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        logger.debug("emit %s: %s", event, payload or {})
        await self._transport.publish(event, payload or {})