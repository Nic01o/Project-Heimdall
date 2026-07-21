"""REST-API module: HTTP control plane for alarms, modules, and settings.

Unlike other modules, webui needs direct access to the Scheduler and the
full module registry (via attach_context(), called once by the daemon after
every module has been init()'d - see daemon.py). This is a deliberate,
single exception to "modules only communicate through the bus": per the
README's Module Settings Pattern, webui is the only module allowed to expose
settings changes over HTTP, so it necessarily knows about the other modules.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.datastructures import FormData

from alarmclock.core.alarm import Weekday
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.base import Module
from alarmclock.modules.settings_types import FIELD_TYPES, SettingsValidationError

logger = logging.getLogger("alarmclock.modules.webui")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _resolve_widgets(schema: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fill in each field's default widget from FIELD_TYPES so templates only
    ever switch on `field.widget`, never on `field.type` directly."""
    resolved: dict[str, dict[str, Any]] = {}
    for key, field in schema.items():
        widget = field.get("widget", FIELD_TYPES[field["type"]]["widget"])
        resolved[key] = {**field, "widget": widget}
    return resolved


def _form_to_settings(form: FormData, schema: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Coerce submitted form fields to the types their schema declares.
    Fields absent from the form are left untouched (update_settings merges),
    except bools, where "absent" means an unchecked checkbox (False)."""
    values: dict[str, Any] = {}
    for key, field in schema.items():
        field_type = field["type"]
        if field_type == "bool":
            values[key] = key in form
        elif field_type == "multiselect":
            if key in form:
                values[key] = form.getlist(key)
        elif key in form:
            raw = form[key]
            if field_type == "int":
                values[key] = int(raw)
            elif field_type == "float":
                values[key] = float(raw)
            else:
                values[key] = raw
    return values


def _overrides_by_weekday(
    plan: Any,
) -> dict[Weekday, tuple[datetime.date, datetime.time | None]]:
    """Map each weekday to its earliest pending override, if any. Used to
    annotate both group rows and free-day rows in the UI without duplicating
    the plan's date bookkeeping in the template."""
    by_weekday: dict[Weekday, tuple[datetime.date, datetime.time | None]] = {}
    for date, time in plan.overrides.items():
        weekday = Weekday(date.weekday())
        if weekday not in by_weekday or date < by_weekday[weekday][0]:
            by_weekday[weekday] = (date, time)
    return by_weekday


class GroupCreate(BaseModel):
    days: list[int]
    time: str


class GroupTimeUpdate(BaseModel):
    time: str
    permanent: bool = True


class DayTimeUpdate(BaseModel):
    time: str
    permanent: bool = True


class SnoozeRequest(BaseModel):
    minutes: float = 9


class WebUIModule(Module):
    """HTTP control plane. Owns a FastAPI app; routes reach into the
    Scheduler and other modules via attach_context()."""

    display_name = "Web UI"
    icon = "globe"

    def __init__(
        self,
        name: str,
        bus: Any,
        config: dict[str, Any] | None = None,
        store: Any = None,
    ) -> None:
        super().__init__(name, bus, config, store)
        self._scheduler: Scheduler | None = None
        self._modules: dict[str, Module] = {}
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None
        self.app = FastAPI(title="Alarm Clock")
        self.app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
        self._templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        self._register_routes()
        self._register_ui_routes()

    def attach_context(self, scheduler: Scheduler, modules: list[Module]) -> None:
        """Give webui what it needs to act as a control plane. Called once by
        the daemon after every module has been init()'d."""
        self._scheduler = scheduler
        self._modules = {module.name: module for module in modules}

    # -- Module lifecycle -----------------------------------------------------

    async def init(self) -> None:
        self.bus.subscribe(f"{self.name}.settings_changed", self._on_settings_changed)

    async def _on_settings_changed(self, payload: dict[str, Any]) -> None:
        self.logger.info("settings changed (%s) - restart webui to apply host/port", payload)

    _STARTUP_POLL_INTERVAL = 0.01
    _STARTUP_TIMEOUT = 5.0

    async def enable(self) -> None:
        host = self.settings.get("host", "0.0.0.0")
        port = self.settings.get("port", 5000)
        config = uvicorn.Config(self.app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)

        async def serve() -> None:
            try:
                await server.serve()
            except SystemExit:
                # uvicorn's startup() does sys.exit() on OSError (e.g. port
                # already in use). Swallow it here so it doesn't escape this
                # task as an uncaught BaseException - `server.started`
                # staying False is what signals the failure below.
                pass

        task = asyncio.create_task(serve())

        # server.serve() only flips `started` once the socket is bound; wait
        # for whichever happens first so a bind failure surfaces here rather
        # than silently in the background.
        waited = 0.0
        while not server.started and not task.done() and waited < self._STARTUP_TIMEOUT:
            await asyncio.sleep(self._STARTUP_POLL_INTERVAL)
            waited += self._STARTUP_POLL_INTERVAL

        if not server.started:
            task.cancel()
            raise RuntimeError(f"webui failed to bind {host}:{port}")

        self._server = server
        self._server_task = task
        self.enabled = True
        self.logger.info("webui listening on %s:%s", host, port)

    async def disable(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            await self._server_task
        self._server = None
        self._server_task = None
        self.enabled = False

    async def on_event(self, event: str, payload: Any = None) -> None:
        pass

    # -- settings ---------------------------------------------------------------

    async def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "host": {"type": "string", "label": "Host"},
            "port": {"type": "int", "min": 1, "max": 65535, "label": "Port"},
        }

    # -- helpers used by routes ---------------------------------------------

    def _get_scheduler(self) -> Scheduler:
        if self._scheduler is None:
            raise HTTPException(status_code=503, detail="scheduler not attached")
        return self._scheduler

    def _get_module(self, name: str) -> Module:
        module = self._modules.get(name)
        if module is None:
            raise HTTPException(status_code=404, detail=f"unknown module {name!r}")
        return module

    def _get_weekday(self, day: str) -> Weekday:
        try:
            return Weekday[day.upper()]
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown weekday {day!r}") from None

    def _require_free_day(self, scheduler: Scheduler, day: Weekday) -> None:
        if scheduler.is_day_assigned(day):
            raise HTTPException(
                status_code=409,
                detail=f"{day.name.capitalize()} already belongs to a sleep plan group",
            )

    def _register_routes(self) -> None:
        app = self.app

        @app.get("/plan")
        async def get_plan() -> dict[str, Any]:
            return self._get_scheduler().get_plan().to_dict()

        @app.post("/plan/groups")
        async def create_group(payload: GroupCreate) -> dict[str, Any]:
            scheduler = self._get_scheduler()
            try:
                days = frozenset(Weekday(day) for day in payload.days)
                time = datetime.time.fromisoformat(payload.time)
                group = scheduler.create_group(days, time)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return group.to_dict()

        @app.post("/plan/groups/{group_id}")
        async def update_group(group_id: str, payload: GroupTimeUpdate) -> dict[str, Any]:
            scheduler = self._get_scheduler()
            try:
                time = datetime.time.fromisoformat(payload.time)
                scheduler.set_group_time(group_id, time, permanent=payload.permanent)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return scheduler.get_plan().to_dict()

        @app.delete("/plan/groups/{group_id}")
        async def remove_group(group_id: str) -> dict[str, str]:
            try:
                self._get_scheduler().delete_group(group_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return {"status": "ok"}

        @app.post("/plan/days/{day}")
        async def set_day(day: str, payload: DayTimeUpdate) -> dict[str, Any]:
            scheduler = self._get_scheduler()
            weekday = self._get_weekday(day)
            self._require_free_day(scheduler, weekday)
            try:
                time = datetime.time.fromisoformat(payload.time)
                if payload.permanent:
                    scheduler.create_group(frozenset({weekday}), time)
                else:
                    scheduler.set_day_once(weekday, time)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return scheduler.get_plan().to_dict()

        @app.post("/plan/disable")
        async def disable_plan() -> dict[str, str]:
            self._get_scheduler().set_enabled(False)
            return {"status": "ok"}

        @app.post("/plan/stop")
        async def stop_plan() -> dict[str, str]:
            await self._get_scheduler().stop_alarm()
            return {"status": "ok"}

        @app.post("/plan/snooze")
        async def snooze_plan(payload: SnoozeRequest) -> dict[str, Any]:
            until = await self._get_scheduler().snooze_alarm(payload.minutes)
            return {"snooze_until": until.isoformat()}

        @app.get("/modules")
        async def list_modules() -> list[dict[str, Any]]:
            return [
                {
                    "name": module.name,
                    "enabled": module.enabled,
                    "display_name": module.display_name,
                    "icon": module.icon,
                }
                for module in self._modules.values()
            ]

        @app.post("/modules/{name}/enable")
        async def enable_module(name: str) -> dict[str, str]:
            await self._get_module(name).enable()
            return {"status": "ok"}

        @app.post("/modules/{name}/disable")
        async def disable_module(name: str) -> dict[str, str]:
            await self._get_module(name).disable()
            return {"status": "ok"}

        @app.get("/modules/{name}/settings/schema")
        async def get_settings_schema(name: str) -> dict[str, Any]:
            return await self._get_module(name).get_settings_schema()

        @app.get("/modules/{name}/settings")
        async def get_settings(name: str) -> dict[str, Any]:
            return await self._get_module(name).get_settings()

        @app.post("/modules/{name}/settings")
        async def update_settings(name: str, values: dict[str, Any]) -> dict[str, Any]:
            module = self._get_module(name)
            try:
                await module.update_settings(values)
            except SettingsValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return await module.get_settings()

    def _register_ui_routes(self) -> None:
        """Server-rendered HTML control panel: classic forms, no JS. Separate
        routes from the JSON API above (prefixed /ui) even though they end up
        calling the same Scheduler/Module methods - keeps the REST API a pure
        JSON contract and the HTML pages a pure Post/Redirect/Get flow."""
        app = self.app
        templates = self._templates

        @app.get("/", include_in_schema=False)
        async def root_redirect() -> RedirectResponse:
            return RedirectResponse("/ui/", status_code=303)

        @app.get("/ui/", include_in_schema=False)
        async def ui_index(request: Request, error: str | None = None):
            scheduler = self._get_scheduler()
            plan = scheduler.get_plan()
            overrides_by_weekday = _overrides_by_weekday(plan)

            groups = []
            for group in plan.groups:
                sorted_days = sorted(group.days, key=lambda d: d.value)
                groups.append(
                    {
                        "id": group.id,
                        "days": sorted_days,
                        "time": group.time,
                        "overrides": [
                            (day, *overrides_by_weekday[day])
                            for day in sorted_days
                            if day in overrides_by_weekday
                        ],
                    }
                )

            assigned_days = {day for group in plan.groups for day in group.days}
            free_days = [
                {"day": day, "override": overrides_by_weekday.get(day)}
                for day in Weekday
                if day not in assigned_days
            ]

            next_trigger = scheduler.next_trigger(plan, datetime.datetime.now(scheduler.tz))
            next_alarm = None
            next_group_id = None
            if next_trigger is not None:
                next_weekday = Weekday(next_trigger.weekday())
                owning_group = next(
                    (group for group in plan.groups if next_weekday in group.days), None
                )
                next_alarm = {
                    "weekday": next_weekday.name.capitalize(),
                    "date": next_trigger.date(),
                    "time": next_trigger.time(),
                }
                next_group_id = owning_group.id if owning_group is not None else None

            modules = []
            for module in self._modules.values():
                schema = await module.get_settings_schema()
                modules.append(
                    {
                        "name": module.name,
                        "display_name": module.display_name,
                        "enabled": module.enabled,
                        "has_settings": bool(schema),
                    }
                )
            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "plan": plan,
                    "groups": groups,
                    "free_days": free_days,
                    "next_alarm": next_alarm,
                    "next_group_id": next_group_id,
                    "modules": modules,
                    "error": error,
                },
            )

        @app.post("/ui/plan/groups", include_in_schema=False)
        async def ui_create_group(
            time: str = Form(""), days: list[str] = Form([])
        ) -> RedirectResponse:
            scheduler = self._get_scheduler()
            try:
                if not days:
                    raise ValueError("select at least one day")
                parsed_days = frozenset(Weekday(int(day)) for day in days)
                parsed_time = datetime.time.fromisoformat(time)
                scheduler.create_group(parsed_days, parsed_time)
            except ValueError as exc:
                return RedirectResponse(f"/ui/?error={exc}", status_code=303)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/change", include_in_schema=False)
        async def ui_change_group(
            group_id: str = Form(""), time: str = Form(""), scope: str = Form("next")
        ) -> RedirectResponse:
            scheduler = self._get_scheduler()
            try:
                parsed_time = datetime.time.fromisoformat(time)
                scheduler.set_group_time(group_id, parsed_time, permanent=(scope == "permanent"))
            except ValueError as exc:
                return RedirectResponse(f"/ui/?error={exc}", status_code=303)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/groups/{group_id}/delete", include_in_schema=False)
        async def ui_delete_group(group_id: str) -> RedirectResponse:
            try:
                self._get_scheduler().delete_group(group_id)
            except ValueError as exc:
                return RedirectResponse(f"/ui/?error={exc}", status_code=303)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/days/{day}", include_in_schema=False)
        async def ui_set_day(
            day: str, time: str = Form(""), scope: str = Form("next")
        ) -> RedirectResponse:
            scheduler = self._get_scheduler()
            weekday = self._get_weekday(day)
            if scheduler.is_day_assigned(weekday):
                return RedirectResponse(
                    f"/ui/?error={weekday.name.capitalize()} already belongs to a sleep plan group",
                    status_code=303,
                )
            try:
                parsed_time = datetime.time.fromisoformat(time)
                if scope == "permanent":
                    scheduler.create_group(frozenset({weekday}), parsed_time)
                else:
                    scheduler.set_day_once(weekday, parsed_time)
            except ValueError as exc:
                return RedirectResponse(f"/ui/?error={exc}", status_code=303)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/disable", include_in_schema=False)
        async def ui_disable_plan() -> RedirectResponse:
            self._get_scheduler().set_enabled(False)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/stop", include_in_schema=False)
        async def ui_stop_plan() -> RedirectResponse:
            await self._get_scheduler().stop_alarm()
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/snooze", include_in_schema=False)
        async def ui_snooze_plan(minutes: float = Form(9)) -> RedirectResponse:
            await self._get_scheduler().snooze_alarm(minutes)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/modules/{name}/enable", include_in_schema=False)
        async def ui_enable_module(name: str) -> RedirectResponse:
            await self._get_module(name).enable()
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/modules/{name}/disable", include_in_schema=False)
        async def ui_disable_module(name: str) -> RedirectResponse:
            await self._get_module(name).disable()
            return RedirectResponse("/ui/", status_code=303)

        @app.get("/ui/modules/{name}/settings", include_in_schema=False)
        async def ui_module_settings(request: Request, name: str, error: str | None = None):
            module = self._get_module(name)
            schema = await module.get_settings_schema()
            values = await module.get_settings()
            return templates.TemplateResponse(
                request,
                "module_settings.html",
                {
                    "module": module,
                    "schema": _resolve_widgets(schema),
                    "values": values,
                    "error": error,
                },
            )

        @app.post("/ui/modules/{name}/settings", include_in_schema=False)
        async def ui_update_module_settings(name: str, request: Request) -> RedirectResponse:
            module = self._get_module(name)
            schema = await module.get_settings_schema()
            form = await request.form()
            try:
                values = _form_to_settings(form, schema)
                await module.update_settings(values)
            except (SettingsValidationError, ValueError) as exc:
                return RedirectResponse(
                    f"/ui/modules/{name}/settings?error={exc}", status_code=303
                )
            return RedirectResponse(f"/ui/modules/{name}/settings", status_code=303)
