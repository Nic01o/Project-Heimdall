"""Tests for the server-rendered HTML control panel (Jinja2 templates,
classic Post/Redirect/Get forms - no JS) attached to the webui module.
"""

import datetime
from typing import Any

from fastapi.testclient import TestClient

from alarmclock.core.alarm import Weekday
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


def test_ui_index_lists_groups_and_modules():
    client, scheduler, _webui, _dummy = make_client()
    scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))

    response = client.get("/ui/")
    assert response.status_code == 200
    assert "Monday" in response.text
    assert "07:00" in response.text
    assert "Dummy" in response.text
    assert "Settings" in response.text  # dummy has a non-empty schema


def test_ui_create_group_redirects_and_persists():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/ui/plan/groups", data={"time": "07:00", "days": ["0", "2"]})
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/"

    groups = scheduler.get_plan().groups
    assert len(groups) == 1
    assert sorted(int(day) for day in groups[0].days) == [0, 2]


def test_ui_create_group_without_days_redirects_with_error():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/ui/plan/groups", data={"time": "07:00"})
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/?error=")
    assert scheduler.get_plan().groups == []


def test_ui_change_group_permanent_vs_next():
    client, scheduler, _webui, _dummy = make_client()
    group = scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))

    response = client.post(
        "/ui/plan/change",
        data={"group_id": group.id, "time": "07:00", "scope": "permanent"},
    )
    assert response.status_code == 303
    assert scheduler.get_plan().groups[0].time == datetime.time(7, 0)

    response = client.post(
        "/ui/plan/change",
        data={"group_id": group.id, "time": "09:00", "scope": "next"},
    )
    assert response.status_code == 303
    assert scheduler.get_plan().groups[0].time == datetime.time(7, 0)
    assert len(scheduler.get_plan().overrides) == 1


def test_ui_change_unknown_group_redirects_with_error():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post(
        "/ui/plan/change",
        data={"group_id": "does-not-exist", "time": "07:00", "scope": "permanent"},
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/?error=")


def test_ui_delete_group():
    client, scheduler, _webui, _dummy = make_client()
    group = scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))

    response = client.post(f"/ui/plan/groups/{group.id}/delete")
    assert response.status_code == 303
    assert scheduler.get_plan().groups == []


def test_ui_delete_unknown_group_redirects_with_error():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post("/ui/plan/groups/does-not-exist/delete")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/?error=")


def test_ui_set_free_day():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post(
        "/ui/plan/days/monday", data={"time": "07:00", "scope": "permanent"}
    )
    assert response.status_code == 303
    groups = scheduler.get_plan().groups
    assert len(groups) == 1


def test_ui_set_already_assigned_day_redirects_with_error():
    client, scheduler, _webui, _dummy = make_client()
    client.post("/ui/plan/days/monday", data={"time": "07:00", "scope": "permanent"})
    response = client.post(
        "/ui/plan/days/monday", data={"time": "08:00", "scope": "next"}
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/?error=")


def test_ui_disable_and_reactivate_via_edit():
    client, scheduler, _webui, _dummy = make_client()
    group = scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))

    response = client.post("/ui/plan/disable")
    assert response.status_code == 303
    assert scheduler.get_plan().enabled is False

    response = client.post(
        "/ui/plan/change",
        data={"group_id": group.id, "time": "07:30", "scope": "permanent"},
    )
    assert response.status_code == 303
    assert scheduler.get_plan().enabled is True


def test_ui_stop_and_snooze():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/ui/plan/stop")
    assert response.status_code == 303

    response = client.post("/ui/plan/snooze", data={"minutes": "5"})
    assert response.status_code == 303
    assert scheduler.get_plan().snooze_until is not None


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
