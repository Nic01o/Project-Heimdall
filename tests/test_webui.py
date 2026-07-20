"""Tests for the webui REST-API module: alarms, module registry, and the
settings pattern exposed over HTTP.
"""

import asyncio
import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alarmclock.core.alarm import Alarm
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.base import Module
from alarmclock.modules.webui.webui import WebUIModule

DUMMY_SCHEMA = {
    "brightness": {"type": "int", "min": 0, "max": 100, "label": "Brightness"},
}


class DummyModule(Module):
    """Minimal module double with a settings schema, used to exercise the
    generic module/settings routes without depending on a real hardware
    module."""

    display_name = "Dummy"
    icon = "dummy"

    async def init(self) -> None:
        pass

    async def enable(self) -> None:
        self.enabled = True

    async def disable(self) -> None:
        self.enabled = False

    async def on_event(self, event: str, payload: Any = None) -> None:
        pass

    async def get_settings_schema(self) -> dict:
        return DUMMY_SCHEMA


def make_client() -> tuple[TestClient, Scheduler, WebUIModule, DummyModule]:
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    dummy = DummyModule("dummy", bus, {"brightness": 50})
    webui.attach_context(scheduler, [webui, dummy])
    return TestClient(webui.app), scheduler, webui, dummy


def test_list_alarms_empty():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/alarms")
    assert response.status_code == 200
    assert response.json() == []


def test_create_recurring_alarm_and_list_it():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/alarms", json={"time": "07:00", "label": "Wake up"})
    assert response.status_code == 200
    body = response.json()
    assert body["time"] == "07:00:00"
    assert body["label"] == "Wake up"
    assert len(scheduler.list_alarms()) == 1

    listed = client.get("/alarms").json()
    assert listed == [body]


def test_create_one_shot_alarm():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post("/alarms", json={"at": "2026-07-20T07:00:00+00:00"})
    assert response.status_code == 200
    assert response.json()["at"] is not None


def test_create_alarm_with_both_time_and_at_is_rejected():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post(
        "/alarms", json={"time": "07:00", "at": "2026-07-20T07:00:00+00:00"}
    )
    assert response.status_code == 400


def test_create_alarm_with_neither_time_nor_at_is_rejected():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post("/alarms", json={"label": "nothing"})
    assert response.status_code == 400


def test_delete_alarm():
    client, scheduler, _webui, _dummy = make_client()
    alarm = scheduler.add_alarm(Alarm(time=datetime.time(7, 0)))

    response = client.delete(f"/alarms/{alarm.id}")
    assert response.status_code == 200
    assert scheduler.get_alarm(alarm.id) is None


def test_delete_unknown_alarm_returns_404():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.delete("/alarms/does-not-exist")
    assert response.status_code == 404


def test_snooze_alarm():
    client, scheduler, _webui, _dummy = make_client()
    alarm = scheduler.add_alarm(Alarm(time=datetime.time(7, 0)))

    response = client.post(f"/alarms/{alarm.id}/snooze", json={"minutes": 5})
    assert response.status_code == 200
    assert response.json()["label"] == alarm.label


def test_list_modules_includes_webui_and_dummy():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/modules")
    assert response.status_code == 200
    names = {module["name"] for module in response.json()}
    assert names == {"webui", "dummy"}


def test_enable_and_disable_module():
    client, _scheduler, _webui, dummy = make_client()
    assert dummy.enabled is False

    assert client.post("/modules/dummy/enable").status_code == 200
    assert dummy.enabled is True

    assert client.post("/modules/dummy/disable").status_code == 200
    assert dummy.enabled is False


def test_unknown_module_returns_404():
    client, _scheduler, _webui, _dummy = make_client()
    assert client.post("/modules/nonexistent/enable").status_code == 404
    assert client.get("/modules/nonexistent/settings").status_code == 404
    assert client.get("/modules/nonexistent/settings/schema").status_code == 404


def test_get_settings_schema_for_module():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/modules/dummy/settings/schema")
    assert response.status_code == 200
    assert response.json() == DUMMY_SCHEMA


def test_get_settings_for_module():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/modules/dummy/settings")
    assert response.status_code == 200
    assert response.json() == {"brightness": 50}


def test_update_settings_for_module_validates_and_persists():
    client, _scheduler, _webui, dummy = make_client()
    response = client.post("/modules/dummy/settings", json={"brightness": 75})
    assert response.status_code == 200
    assert response.json() == {"brightness": 75}
    assert dummy.settings == {"brightness": 75}


def test_update_settings_rejects_invalid_value():
    client, _scheduler, _webui, dummy = make_client()
    response = client.post("/modules/dummy/settings", json={"brightness": 999})
    assert response.status_code == 400
    assert dummy.settings == {"brightness": 50}


def test_webui_enable_and_disable_lifecycle_starts_and_stops_server_task():
    async def scenario():
        bus = EventBus()
        webui = WebUIModule("webui", bus, {"host": "127.0.0.1", "port": 0})
        await webui.init()

        await webui.enable()
        assert webui.enabled is True
        assert webui._server_task is not None
        task = webui._server_task

        await webui.disable()
        assert webui.enabled is False
        assert task.done()

    asyncio.run(scenario())


def test_webui_enable_raises_and_stays_disabled_when_port_is_taken():
    async def scenario():
        bus = EventBus()
        first = WebUIModule("webui", bus, {"host": "127.0.0.1", "port": 0})
        await first.init()
        await first.enable()
        port = first._server.servers[0].sockets[0].getsockname()[1]

        second = WebUIModule("webui2", bus, {"host": "127.0.0.1", "port": port})
        await second.init()
        try:
            with pytest.raises(RuntimeError):
                await second.enable()
            assert second.enabled is False
            assert second._server_task is None
        finally:
            await first.disable()

    asyncio.run(scenario())
