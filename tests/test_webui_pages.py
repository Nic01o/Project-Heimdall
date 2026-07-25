"""Tests for the server-rendered HTML control panel (Jinja2 templates,
classic Post/Redirect/Get forms - no JS) attached to the webui module.
"""

import datetime
from typing import Any

from fastapi.testclient import TestClient

from alarmclock.core.alarm import EVERY_DAY, Weekday
from alarmclock.core.event_bus import EventBus
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.base import Module
from alarmclock.modules.webui.webui import WebUIModule

DUMMY_SCHEMA = {
    "brightness": {
        "type": "int",
        "min": 0,
        "max": 100,
        "label": "Brightness",
        "requires_restart": True,
        "default": 50,
    },
    "mode": {
        "type": "select",
        "options": ["fade", "instant"],
        "label": "Mode",
        "default": "fade",
    },
    "flag": {"type": "bool", "label": "Flag", "default": True},
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

    def get_settings_schema(self) -> dict:
        return {**super().get_settings_schema(), **DUMMY_SCHEMA}


def make_client() -> tuple[TestClient, Scheduler, WebUIModule, DummyModule]:
    bus = EventBus()
    scheduler = Scheduler(bus, timezone="UTC")
    webui = WebUIModule("webui", bus, {})
    dummy = DummyModule("dummy", bus)
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
    assert "Mo" in response.text
    assert "07:00" in response.text
    assert "Dummy" in response.text
    assert "/ui/modules/dummy/settings" in response.text  # dummy has a non-empty schema


def test_ui_index_shows_hint_when_no_plan_exists():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "Noch kein Schlafplan" in response.text


def test_ui_index_shows_no_alarm_planned_when_nothing_scheduled():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "Kein Wecker geplant" in response.text


def test_ui_index_shows_klingelt_heute_for_group_later_today():
    client, scheduler, _webui, _dummy = make_client()
    now = datetime.datetime.now(datetime.timezone.utc)
    scheduler.create_group(EVERY_DAY, (now + datetime.timedelta(minutes=5)).time())

    response = client.get("/ui/")

    assert response.status_code == 200
    assert "Klingelt heute um" in response.text


def test_ui_index_shows_klingelt_morgen_for_group_only_on_tomorrows_weekday():
    client, scheduler, _webui, _dummy = make_client()
    now = datetime.datetime.now(datetime.timezone.utc)
    tomorrow_weekday = Weekday((now.weekday() + 1) % 7)
    scheduler.create_group(frozenset({tomorrow_weekday}), datetime.time(6, 0))

    response = client.get("/ui/")

    assert response.status_code == 200
    assert "Klingelt morgen um 06:00 Uhr" in response.text


def test_ui_create_group_redirects_and_persists():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/ui/plan/groups", data={"time": "07:00", "days": ["0", "2"]})
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/"

    groups = scheduler.get_plan().groups
    assert len(groups) == 1
    assert sorted(int(day) for day in groups[0].days) == [0, 2]
    assert groups[0].enabled is True


def test_ui_create_group_without_days_is_a_silent_noop():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/ui/plan/groups", data={"time": "07:00"})
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/"
    assert scheduler.get_plan().groups == []


def test_ui_update_group_changes_time_and_days():
    client, scheduler, _webui, _dummy = make_client()
    group = scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))

    response = client.post(
        f"/ui/plan/groups/{group.id}/update",
        data={"time": "07:00", "days": ["1"]},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/"

    updated = scheduler.get_plan().groups[0]
    assert updated.time == datetime.time(7, 0)
    assert updated.days == frozenset({Weekday.TUESDAY})


def test_ui_update_group_without_days_is_a_silent_noop_and_stays_in_edit():
    client, scheduler, _webui, _dummy = make_client()
    group = scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(6, 30))

    response = client.post(f"/ui/plan/groups/{group.id}/update", data={"time": "07:00"})
    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/?edit={group.id}"
    assert scheduler.get_plan().groups[0].time == datetime.time(6, 30)


def test_ui_update_unknown_group_redirects_with_error():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post(
        "/ui/plan/groups/does-not-exist/update", data={"time": "07:00", "days": ["0"]}
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/?error=")


def test_ui_toggle_group_enabled():
    client, scheduler, _webui, _dummy = make_client()
    group = scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))
    assert scheduler.get_plan().groups[0].enabled is True

    response = client.post(f"/ui/plan/groups/{group.id}/toggle")
    assert response.status_code == 303
    assert scheduler.get_plan().groups[0].enabled is False

    response = client.post(f"/ui/plan/groups/{group.id}/toggle")
    assert response.status_code == 303
    assert scheduler.get_plan().groups[0].enabled is True


def test_ui_toggle_unknown_group_redirects_with_error():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.post("/ui/plan/groups/does-not-exist/toggle")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/?error=")


def test_ui_index_frees_days_of_a_disabled_group_for_the_create_form():
    client, scheduler, _webui, _dummy = make_client()
    group = scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))
    scheduler.set_group_enabled(group.id, False)

    response = client.get("/ui/")
    assert response.status_code == 200
    # Monday's checkbox in the "new group" create form must be enabled
    # (not `disabled`) now that its only owning group is paused.
    assert '<input type="checkbox" name="days" value="0"\n        \n        >' in response.text


def test_ui_index_disables_reenable_switch_when_a_day_is_now_taken():
    client, scheduler, _webui, _dummy = make_client()
    group = scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))
    scheduler.set_group_enabled(group.id, False)
    scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(8, 0))

    response = client.get("/ui/")
    assert response.status_code == 200
    # The paused group's own toggle switch must be rendered `disabled` so
    # the conflict is blocked at interaction time, not after a submit.
    assert f'action="/ui/plan/groups/{group.id}/toggle"' in response.text
    toggle_form = response.text.split(f'action="/ui/plan/groups/{group.id}/toggle"')[1]
    button_markup = toggle_form.split("</form>")[0]
    assert "disabled" in button_markup


def test_ui_toggle_group_enabled_rejects_conflict_as_a_server_side_safety_net():
    # The switch is disabled client-side (see above); this covers a direct
    # POST that bypasses the disabled attribute (curl, stale page, etc).
    client, scheduler, _webui, _dummy = make_client()
    group = scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(7, 0))
    scheduler.set_group_enabled(group.id, False)
    scheduler.create_group(frozenset({Weekday.MONDAY}), datetime.time(8, 0))

    response = client.post(f"/ui/plan/groups/{group.id}/toggle")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/?error=")
    assert scheduler.get_plan().groups[0].enabled is False


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


def test_ui_skip_next_alarm_and_unskip():
    # a group covering every weekday guarantees there's always a next
    # occurrence to act on, regardless of which real weekday the test runs on
    client, scheduler, _webui, _dummy = make_client()
    scheduler.create_group(EVERY_DAY, datetime.time(7, 0))

    response = client.post("/ui/plan/master/skip")
    assert response.status_code == 303
    assert scheduler.is_next_alarm_skipped() is True
    assert len(scheduler.get_plan().overrides) == 1

    response = client.post("/ui/plan/override/clear")
    assert response.status_code == 303
    assert scheduler.is_next_alarm_skipped() is False
    assert scheduler.get_plan().overrides == {}


def test_ui_set_next_alarm_override_clears_skip():
    client, scheduler, _webui, _dummy = make_client()
    scheduler.create_group(EVERY_DAY, datetime.time(7, 0))
    scheduler.skip_next_alarm()
    assert scheduler.is_next_alarm_skipped() is True

    response = client.post("/ui/plan/override", data={"time": "07:30"})
    assert response.status_code == 303
    assert scheduler.is_next_alarm_skipped() is False

    overrides = scheduler.get_plan().overrides
    assert list(overrides.values()) == [datetime.time(7, 30)]


def test_ui_set_next_alarm_override_without_any_plan():
    client, scheduler, _webui, _dummy = make_client()
    response = client.post("/ui/plan/override", data={"time": "07:30"})
    assert response.status_code == 303
    assert list(scheduler.get_plan().overrides.values()) == [datetime.time(7, 30)]


def test_ui_enable_and_disable_module():
    client, _scheduler, _webui, dummy = make_client()
    assert dummy.enabled is False

    response = client.post("/ui/modules/dummy/enable")
    assert response.status_code == 303
    assert dummy.enabled is True

    response = client.post("/ui/modules/dummy/disable")
    assert response.status_code == 303
    assert dummy.enabled is False


def test_ui_index_status_dot_reflects_disabled_module():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "status-dot status-off" in response.text
    assert 'title="Aus"' in response.text


def test_ui_index_status_dot_reflects_enabled_module():
    client, _scheduler, _webui, dummy = make_client()
    dummy.enabled = True
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "status-dot status-on" in response.text
    assert 'title="Läuft"' in response.text


def test_ui_index_status_dot_reflects_needs_restart_over_enabled():
    client, _scheduler, _webui, dummy = make_client()
    dummy.enabled = True
    dummy.needs_restart = True
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "status-dot status-restart" in response.text
    assert 'title="Neustart nötig"' in response.text


def test_ui_restart_module_redirects_to_next():
    client, _scheduler, _webui, dummy = make_client()
    dummy.needs_restart = True

    response = client.post(
        "/ui/modules/dummy/restart", data={"next": "/ui/modules/dummy/settings"}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/modules/dummy/settings"
    assert dummy.enabled is True


def test_ui_restart_module_rejects_unsafe_next():
    client, _scheduler, _webui, dummy = make_client()
    dummy.needs_restart = True

    response = client.post(
        "/ui/modules/dummy/restart", data={"next": "//evil.example"}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/"


def test_ui_index_shows_no_restart_hint_by_default():
    client, _scheduler, _webui, _dummy = make_client()
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "Neustart" not in response.text


def test_ui_index_shows_restart_hint_when_needs_restart():
    client, _scheduler, _webui, dummy = make_client()
    dummy.needs_restart = True

    response = client.get("/ui/")

    assert response.status_code == 200
    assert "Neustart" in response.text


def test_ui_module_settings_shows_restart_hint_when_needs_restart():
    client, _scheduler, _webui, dummy = make_client()
    dummy.needs_restart = True

    response = client.get("/ui/modules/dummy/settings")

    assert response.status_code == 200
    assert "Neustart" in response.text


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
        data={"brightness": "75", "mode": "instant", "flag": "on"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/modules/dummy/settings"
    assert dummy.settings == {"active": True, "brightness": 75, "mode": "instant", "flag": True}


def test_ui_update_module_settings_unchecked_bool_becomes_false():
    client, _scheduler, _webui, dummy = make_client()
    response = client.post(
        "/ui/modules/dummy/settings",
        data={"brightness": "50", "mode": "fade"},  # "flag" checkbox omitted -> unchecked
    )
    assert response.status_code == 303
    assert dummy.settings["flag"] is False


def test_ui_update_module_settings_does_not_touch_active():
    # `active` is excluded from the generic settings form (see
    # webui.py:_editable_schema) - saving other settings must never flip it,
    # even though it's absent from the submitted form data just like an
    # unchecked checkbox would be.
    client, _scheduler, _webui, dummy = make_client()
    response = client.post(
        "/ui/modules/dummy/settings",
        data={"brightness": "50", "mode": "fade", "flag": "on"},
    )
    assert response.status_code == 303
    assert dummy.settings["active"] is True


def test_ui_update_module_settings_rejects_invalid_value_with_error_redirect():
    client, _scheduler, _webui, dummy = make_client()
    response = client.post(
        "/ui/modules/dummy/settings",
        data={"brightness": "999", "mode": "fade", "flag": "on"},
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/modules/dummy/settings?error=")
    assert dummy.settings == {"active": True, "brightness": 50, "mode": "fade", "flag": True}


def test_ui_settings_page_for_unknown_module_returns_404():
    client, _scheduler, _webui, _dummy = make_client()
    assert client.get("/ui/modules/nonexistent/settings").status_code == 404
