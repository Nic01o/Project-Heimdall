"""Tests for the server-rendered HTML control panel (Jinja2 templates,
classic Post/Redirect/Get forms - no JS) attached to the webui module.
"""

import datetime
from typing import Any

from fastapi.testclient import TestClient

from alarmclock.core.alarm import Alarm
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.base import Module
from alarmclock.modules.webui.webui import WebUIModule

DUMMY_SCHEMA = {
    "brightness": {"type": "int", "min": 0, "max": 100, "label": "Brightness"},
    "mode": {"type": "select", "options": ["fade", "instant"], "label": "Mode"},
    "active": {"type": "bool", "label": "Active"},
}


class DummyModule(Module):
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
    dummy = DummyModule("dummy", bus, {"brightness": 50, "mode": "fade", "active": True})
    webui.attach_context(scheduler, [webui, dummy])
    client = TestClient(webui.app, follow_redirects=False)
    return client, scheduler, webui, dummy


def test_root_redirects_to_ui_index():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/"


def test_ui_index_lists_alarms_and_modules():
    client, scheduler, _webui, _dummy = make_client()
    scheduler.add_alarm(Alarm(time=datetime.time(7, 0), label="Wake up"))

    response = client.get("/ui/")
    assert response.status_code == 200
    assert "Wake up" in response.text
    assert "Dummy" in response.text
    assert "Settings" in response.text  # dummy has a non-empty schema


def test_ui_create_recurring_alarm_redirects_and_persists():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post(
        "/ui/alarms", data={"time": "07:00", "label": "Wake up", "repeat": ["0", "2"]}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/"

    alarms = scheduler.list_alarms()
    assert len(alarms) == 1
    assert alarms[0].label == "Wake up"
    assert sorted(int(day) for day in alarms[0].repeat) == [0, 2]


def test_ui_create_alarm_with_conflicting_fields_redirects_with_error():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post(
        "/ui/alarms", data={"time": "07:00", "at": "2026-07-20T07:00", "label": "bad"}
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/?error=")
    assert scheduler.list_alarms() == []


def test_ui_delete_alarm():
    client, scheduler, _webui, _dummy = make_client()
    alarm = scheduler.add_alarm(Alarm(time=datetime.time(7, 0)))

    response = client.post(f"/ui/alarms/{alarm.id}/delete")
    assert response.status_code == 303
    assert scheduler.get_alarm(alarm.id) is None


def test_ui_delete_unknown_alarm_returns_404():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post("/ui/alarms/does-not-exist/delete")
    assert response.status_code == 404


def test_ui_enable_and_disable_module():
    client, _scheduler, _webui, dummy = make_client()
    assert dummy.enabled is False

    response = client.post("/ui/modules/dummy/enable")
    assert response.status_code == 303
    assert dummy.enabled is True

    response = client.post("/ui/modules/dummy/disable")
    assert response.status_code == 303
    assert dummy.enabled is False


def test_ui_module_settings_page_renders_current_values():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/ui/modules/dummy/settings")
    assert response.status_code == 200
    assert 'value="50"' in response.text
    assert "Brightness" in response.text


def test_ui_update_module_settings_persists_and_redirects():
    client, _scheduler, _webui, dummy = make_client()
    response = client.post(
        "/ui/modules/dummy/settings",
        data={"brightness": "75", "mode": "instant", "active": "on"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/modules/dummy/settings"
    assert dummy.settings == {"brightness": 75, "mode": "instant", "active": True}


def test_ui_update_module_settings_unchecked_bool_becomes_false():
    client, _scheduler, _webui, dummy = make_client()
    response = client.post(
        "/ui/modules/dummy/settings",
        data={"brightness": "50", "mode": "fade"},  # "active" checkbox omitted -> unchecked
    )
    assert response.status_code == 303
    assert dummy.settings["active"] is False


def test_ui_update_module_settings_rejects_invalid_value_with_error_redirect():
    client, _scheduler, _webui, dummy = make_client()
    response = client.post(
        "/ui/modules/dummy/settings",
        data={"brightness": "999", "mode": "fade", "active": "on"},
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/modules/dummy/settings?error=")
    assert dummy.settings == {"brightness": 50, "mode": "fade", "active": True}


def test_ui_settings_page_for_unknown_module_returns_404():
    client, _scheduler, _webui, _dummy = make_client()
    assert client.get("/ui/modules/nonexistent/settings").status_code == 404
