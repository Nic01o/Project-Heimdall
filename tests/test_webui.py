"""Tests for the webui REST-API module: the sleep plan, module registry, and
the settings pattern exposed over HTTP.
"""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.base import Module
from alarmclock.modules.webui.webui import WebUIModule

ACTIVE_SCHEMA_FIELD = {"active": {"type": "bool", "label": "Aktiv", "default": True}}

DUMMY_SCHEMA = {
    "brightness": {
        "type": "int",
        "min": 0,
        "max": 100,
        "label": "Brightness",
        "requires_restart": True,
        "default": 50,
    },
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

    def get_settings_schema(self) -> dict:
        return {**super().get_settings_schema(), **DUMMY_SCHEMA}


def make_client() -> tuple[TestClient, Scheduler, WebUIModule, DummyModule]:
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    dummy = DummyModule("dummy", bus)
    webui.attach_context(scheduler, [webui, dummy])
    return TestClient(webui.app), scheduler, webui, dummy


def test_get_plan_empty():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/api/plan")
    assert response.status_code == 200
    assert response.json() == {
        "groups": [],
        "overrides": {},
        "enabled": True,
        "snooze_until": None,
    }


def test_create_group_and_list_it():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/api/plan/groups", json={"days": [0, 1], "time": "06:30"})
    assert response.status_code == 200
    body = response.json()
    assert body["days"] == [0, 1]
    assert body["time"] == "06:30:00"

    listed = client.get("/api/plan").json()
    assert listed["groups"] == [body]


def test_create_group_rejects_already_assigned_day():
    client, _scheduler, _webui, _dummy = make_client()
    client.post("/api/plan/groups", json={"days": [0], "time": "06:30"})
    response = client.post("/api/plan/groups", json={"days": [0, 1], "time": "07:00"})
    assert response.status_code == 400


def test_update_group_permanent_and_next_only():
    client, scheduler, _webui, _dummy = make_client()
    group = client.post("/api/plan/groups", json={"days": [0], "time": "06:30"}).json()

    response = client.post(
        f"/api/plan/groups/{group['id']}", json={"time": "07:00", "permanent": True}
    )
    assert response.status_code == 200
    assert response.json()["groups"][0]["time"] == "07:00:00"
    assert response.json()["overrides"] == {}

    response = client.post(
        f"/api/plan/groups/{group['id']}", json={"time": "09:00", "permanent": False}
    )
    assert response.status_code == 200
    assert response.json()["groups"][0]["time"] == "07:00:00"
    assert len(response.json()["overrides"]) == 1


def test_update_unknown_group_returns_400():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post("/api/plan/groups/does-not-exist", json={"time": "07:00"})
    assert response.status_code == 400


def test_delete_group_frees_its_days():
    client, scheduler, _webui, _dummy = make_client()
    group = client.post("/api/plan/groups", json={"days": [0], "time": "06:30"}).json()

    response = client.delete(f"/api/plan/groups/{group['id']}")
    assert response.status_code == 200
    assert client.get("/api/plan").json()["groups"] == []


def test_delete_unknown_group_returns_404():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.delete("/api/plan/groups/does-not-exist")
    assert response.status_code == 404


def test_set_day_permanent_creates_solo_group():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/api/plan/days/monday", json={"time": "07:00", "permanent": True})
    assert response.status_code == 200
    groups = response.json()["groups"]
    assert len(groups) == 1
    assert groups[0]["days"] == [0]
    assert groups[0]["time"] == "07:00:00"


def test_set_day_next_only_does_not_create_a_group():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/api/plan/days/monday", json={"time": "08:00", "permanent": False})
    assert response.status_code == 200
    assert response.json()["groups"] == []
    assert len(response.json()["overrides"]) == 1


def test_set_day_already_assigned_returns_409():
    client, _scheduler, _webui, _dummy = make_client()
    client.post("/api/plan/days/monday", json={"time": "07:00", "permanent": True})
    response = client.post("/api/plan/days/monday", json={"time": "08:00", "permanent": False})
    assert response.status_code == 409


def test_set_day_unknown_weekday_returns_404():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post("/api/plan/days/someday", json={"time": "07:00"})
    assert response.status_code == 404


def test_disable_plan():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/api/plan/disable")
    assert response.status_code == 200
    assert scheduler.get_plan().enabled is False


def test_stop_plan():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post("/api/plan/stop")
    assert response.status_code == 200


def test_snooze_plan():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/api/plan/snooze", json={"minutes": 5})
    assert response.status_code == 200
    assert response.json()["snooze_until"] == scheduler.get_plan().snooze_until.isoformat()


def test_list_modules_includes_webui_and_dummy():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/api/modules")
    assert response.status_code == 200
    names = {module["name"] for module in response.json()}
    assert names == {"webui", "dummy"}


def test_enable_and_disable_module():
    client, _scheduler, _webui, dummy = make_client()
    assert dummy.enabled is False

    assert client.post("/api/modules/dummy/enable").status_code == 200
    assert dummy.enabled is True

    assert client.post("/api/modules/dummy/disable").status_code == 200
    assert dummy.enabled is False


def test_restart_module_endpoint_disables_then_enables():
    client, _scheduler, _webui, dummy = make_client()
    dummy.needs_restart = True

    response = client.post("/api/modules/dummy/restart")

    assert response.status_code == 200
    assert dummy.enabled is True


def test_list_modules_exposes_needs_restart():
    client, _scheduler, _webui, dummy = make_client()
    dummy.needs_restart = True

    response = client.get("/api/modules")

    by_name = {module["name"]: module for module in response.json()}
    assert by_name["dummy"]["needs_restart"] is True
    assert by_name["webui"]["needs_restart"] is False


def test_update_settings_flags_needs_restart_only_for_flagged_field_change():
    client, _scheduler, _webui, dummy = make_client()

    response = client.post("/api/modules/dummy/settings", json={"brightness": 90})

    assert response.status_code == 200
    assert dummy.needs_restart is True


def test_unknown_module_returns_404():
    client, _scheduler, _webui, _dummy = make_client()
    assert client.post("/api/modules/nonexistent/enable").status_code == 404
    assert client.get("/api/modules/nonexistent/settings").status_code == 404
    assert client.get("/api/modules/nonexistent/settings/schema").status_code == 404


def test_get_settings_schema_for_module():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/api/modules/dummy/settings/schema")
    assert response.status_code == 200
    assert response.json() == {**ACTIVE_SCHEMA_FIELD, **DUMMY_SCHEMA}


def test_get_settings_for_module():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/api/modules/dummy/settings")
    assert response.status_code == 200
    assert response.json() == {"active": True, "brightness": 50}


def test_update_settings_for_module_validates_and_persists():
    client, _scheduler, _webui, dummy = make_client()
    response = client.post("/api/modules/dummy/settings", json={"brightness": 75})
    assert response.status_code == 200
    assert response.json() == {"active": True, "brightness": 75}
    assert dummy.settings == {"active": True, "brightness": 75}


def test_update_settings_rejects_invalid_value():
    client, _scheduler, _webui, dummy = make_client()
    response = client.post("/api/modules/dummy/settings", json={"brightness": 999})
    assert response.status_code == 400
    assert dummy.settings == {"active": True, "brightness": 50}


def test_webui_enable_and_disable_lifecycle_starts_and_stops_server_task():
    async def scenario():
        bus = EventBus()
        webui = WebUIModule("webui", bus, {})
        webui.settings["host"] = "127.0.0.1"
        webui.settings["port"] = 0
        await webui.init()

        await webui.enable()
        assert webui.enabled is True
        assert webui._server_task is not None
        task = webui._server_task

        await webui.disable()
        assert webui.enabled is False
        assert task.done()

    asyncio.run(scenario())


def test_webui_enable_clears_needs_restart():
    async def scenario():
        bus = EventBus()
        webui = WebUIModule("webui", bus, {})
        webui.settings["host"] = "127.0.0.1"
        webui.settings["port"] = 0
        await webui.init()
        webui.needs_restart = True

        await webui.enable()
        assert webui.needs_restart is False

        await webui.disable()

    asyncio.run(scenario())


def test_webui_update_settings_flags_needs_restart_for_host_and_port_only():
    async def scenario():
        bus = EventBus()
        webui = WebUIModule("webui", bus, {})
        await webui.init()

        await webui.update_settings({"port": 5001})
        assert webui.needs_restart is True

        webui.needs_restart = False
        await webui.update_settings({"color_profile": "ocean"})
        assert webui.needs_restart is False

    asyncio.run(scenario())


def test_no_password_set_allows_requests_without_credentials():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/api/plan")
    assert response.status_code == 200


def test_password_set_rejects_api_requests_without_login():
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    webui.settings["password"] = "secret"
    webui.attach_context(scheduler, [webui])
    client = TestClient(webui.app)

    response = client.get("/api/plan")
    assert response.status_code == 401


def test_password_set_redirects_ui_requests_without_login_to_login_page():
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    webui.settings["password"] = "secret"
    webui.attach_context(scheduler, [webui])
    client = TestClient(webui.app, follow_redirects=False)

    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/"


def test_login_page_is_reachable_without_a_session():
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    webui.settings["password"] = "secret"
    webui.attach_context(scheduler, [webui])
    client = TestClient(webui.app)

    response = client.get("/login")
    assert response.status_code == 200
    assert 'name="password"' in response.text
    assert 'name="username"' not in response.text


def test_login_with_wrong_password_does_not_grant_access():
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    webui.settings["password"] = "secret"
    webui.attach_context(scheduler, [webui])
    client = TestClient(webui.app)

    client.post("/login", data={"password": "wrong", "next": "/"})
    response = client.get("/api/plan")
    assert response.status_code == 401


def test_login_with_correct_password_grants_session_access():
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    webui.settings["password"] = "secret"
    webui.attach_context(scheduler, [webui])
    client = TestClient(webui.app)

    login = client.post(
        "/login", data={"password": "secret", "next": "/"}, follow_redirects=False
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/"

    response = client.get("/api/plan")
    assert response.status_code == 200


def test_login_redirects_back_to_the_originally_requested_page():
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    webui.settings["password"] = "secret"
    webui.attach_context(scheduler, [webui])
    client = TestClient(webui.app, follow_redirects=False)

    denied = client.get("/modules/webui/settings")
    assert denied.status_code == 303
    next_target = denied.headers["location"].removeprefix("/login?next=")

    login = client.post("/login", data={"password": "secret", "next": next_target})
    assert login.headers["location"] == next_target


def test_changing_password_invalidates_existing_sessions():
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    webui.settings["password"] = "secret"
    webui.attach_context(scheduler, [webui])
    client = TestClient(webui.app)

    client.post("/login", data={"password": "secret", "next": "/"})
    assert client.get("/api/plan").status_code == 200

    asyncio.run(webui.update_settings({"password": "new-secret"}))

    assert client.get("/api/plan").status_code == 401


def test_webui_enable_raises_and_stays_disabled_when_port_is_taken():
    async def scenario():
        bus = EventBus()
        first = WebUIModule("webui", bus, {})
        first.settings["host"] = "127.0.0.1"
        first.settings["port"] = 0
        await first.init()
        await first.enable()
        port = first._server.servers[0].sockets[0].getsockname()[1]

        second = WebUIModule("webui2", bus, {})
        second.settings["host"] = "127.0.0.1"
        second.settings["port"] = port
        await second.init()
        try:
            with pytest.raises(RuntimeError):
                await second.enable()
            assert second.enabled is False
            assert second._server_task is None
        finally:
            await first.disable()

    asyncio.run(scenario())
