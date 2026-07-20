"""Example module demonstrating the plugin interface (see README)."""

from __future__ import annotations

from typing import Any

from alarmclock.modules.base import Module


class MyModule(Module):
    """Minimal example module: reacts to alarms and reports back."""

    async def init(self) -> None:
        """Called once at startup."""
        self.bus.subscribe('alarm.triggered', self.on_alarm)

    async def on_alarm(self, event: Any) -> None:
        """Event handler."""
        await self.bus.emit('mymodule.action_done', {'status': 'ok'})

    async def enable(self) -> None:
        """Enable the module."""
        self.enabled = True

    async def disable(self) -> None:
        """Disable the module."""
        self.enabled = False

    async def on_event(self, event: str, payload: Any = None) -> None:
        """Generic dispatch entry point, in case something calls it directly."""
        if event == 'alarm.triggered':
            await self.on_alarm(payload)