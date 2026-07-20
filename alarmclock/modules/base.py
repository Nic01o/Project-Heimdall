"""Plugin interface for alarm clock modules."""

from __future__ import annotations

import abc
import logging
from typing import Any


class Module(abc.ABC):
    """Base class for all modules (plugins).

    Modules only communicate through the event bus - never directly with
    each other. Subclasses implement the lifecycle hooks below and, in
    ``init()``, subscribe to the events they care about.
    """

    def __init__(self, name: str, bus: Any, config: dict[str, Any] | None = None) -> None:
        self.name = name
        self.bus = bus
        self.config = config or {}
        self.enabled = False
        self.logger = logging.getLogger(f"alarmclock.modules.{name}")

    @abc.abstractmethod
    async def init(self) -> None:
        """Called once at startup, before enable(). Subscribe to bus events here."""
        raise NotImplementedError

    @abc.abstractmethod
    async def enable(self) -> None:
        """Enable the module."""
        raise NotImplementedError

    @abc.abstractmethod
    async def disable(self) -> None:
        """Disable the module."""
        raise NotImplementedError

    @abc.abstractmethod
    async def on_event(self, event: str, payload: Any = None) -> None:
        """Generic handler for events this module is subscribed to."""
        raise NotImplementedError