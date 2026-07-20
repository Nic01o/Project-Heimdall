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

from alarmclock.core.alarm import Alarm, Weekday
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


class AlarmCreate(BaseModel):
    time: str | None = None
    at: datetime.datetime | None = None
    label: str = ""
    repeat: list[int] = []
    enabled: bool = True


class SnoozeRequest(BaseModel):
    minutes: int = 9


class WebUIModule(Module):
    """HTTP control plane. Owns a FastAPI app; routes reach into the
    Scheduler and other modules via attach_context()."""

    display_name = "Web UI"
    icon = "globe"

    def __init__(self, name: str, bus: Any, config: dict[str, Any] | None = None) -> None:
        super().__init__(name, bus, config)
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

    def _get_existing_alarm(self, alarm_id: str) -> None:
        if self._get_scheduler().get_alarm(alarm_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown alarm {alarm_id!r}")

    def _register_routes(self) -> None:
        app = self.app

        @app.get("/alarms")
        async def list_alarms() -> list[dict[str, Any]]:
            return [alarm.to_dict() for alarm in self._get_scheduler().list_alarms()]

        @app.post("/alarms")
        async def create_alarm(payload: AlarmCreate) -> dict[str, Any]:
            scheduler = self._get_scheduler()
            try:
                parsed_time = (
                    datetime.time.fromisoformat(payload.time)
                    if payload.time is not None
                    else None
                )
                alarm = Alarm(
                    time=parsed_time,
                    at=payload.at,
                    label=payload.label,
                    repeat=frozenset(Weekday(day) for day in payload.repeat),
                    enabled=payload.enabled,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return scheduler.add_alarm(alarm).to_dict()

        @app.delete("/alarms/{alarm_id}")
        async def delete_alarm(alarm_id: str) -> dict[str, str]:
            self._get_existing_alarm(alarm_id)
            self._get_scheduler().remove_alarm(alarm_id)
            return {"status": "ok"}

        @app.post("/alarms/{alarm_id}/stop")
        async def stop_alarm(alarm_id: str) -> dict[str, str]:
            self._get_existing_alarm(alarm_id)
            await self._get_scheduler().stop_alarm(alarm_id)
            return {"status": "ok"}

        @app.post("/alarms/{alarm_id}/snooze")
        async def snooze_alarm(alarm_id: str, payload: SnoozeRequest) -> dict[str, Any]:
            self._get_existing_alarm(alarm_id)
            snoozed = await self._get_scheduler().snooze_alarm(alarm_id, payload.minutes)
            return snoozed.to_dict()

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
            alarms = [alarm.to_dict() for alarm in scheduler.list_alarms()]
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
                    "alarms": alarms,
                    "modules": modules,
                    "weekdays": list(Weekday),
                    "error": error,
                },
            )

        @app.post("/ui/alarms", include_in_schema=False)
        async def ui_create_alarm(
            time: str = Form(""),
            at: str = Form(""),
            label: str = Form(""),
            repeat: list[str] = Form([]),
        ) -> RedirectResponse:
            scheduler = self._get_scheduler()
            try:
                parsed_time = datetime.time.fromisoformat(time) if time else None
                parsed_at = (
                    datetime.datetime.fromisoformat(at).replace(tzinfo=scheduler.tz)
                    if at
                    else None
                )
                alarm = Alarm(
                    time=parsed_time,
                    at=parsed_at,
                    label=label,
                    repeat=frozenset(Weekday(int(day)) for day in repeat),
                )
            except ValueError as exc:
                return RedirectResponse(f"/ui/?error={exc}", status_code=303)
            scheduler.add_alarm(alarm)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/alarms/{alarm_id}/delete", include_in_schema=False)
        async def ui_delete_alarm(alarm_id: str) -> RedirectResponse:
            self._get_existing_alarm(alarm_id)
            self._get_scheduler().remove_alarm(alarm_id)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/alarms/{alarm_id}/stop", include_in_schema=False)
        async def ui_stop_alarm(alarm_id: str) -> RedirectResponse:
            self._get_existing_alarm(alarm_id)
            await self._get_scheduler().stop_alarm(alarm_id)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/alarms/{alarm_id}/snooze", include_in_schema=False)
        async def ui_snooze_alarm(alarm_id: str, minutes: int = Form(9)) -> RedirectResponse:
            self._get_existing_alarm(alarm_id)
            await self._get_scheduler().snooze_alarm(alarm_id, minutes)
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
