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
import secrets
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.datastructures import FormData

from alarmclock.core.alarm import Weekday
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.base import Module
from alarmclock.modules.settings_types import FIELD_TYPES, SettingsValidationError

logger = logging.getLogger("alarmclock.modules.webui")

WEEKDAY_LABELS: dict[Weekday, str] = {
    Weekday.MONDAY: "Mo",
    Weekday.TUESDAY: "Di",
    Weekday.WEDNESDAY: "Mi",
    Weekday.THURSDAY: "Do",
    Weekday.FRIDAY: "Fr",
    Weekday.SATURDAY: "Sa",
    Weekday.SUNDAY: "So",
}

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_COOKIE_NAME = "session"

COLOR_PROFILES: dict[str, dict[str, str]] = {
    "sunrise": {"accent": "#ff8a5c", "accent_strong": "#f2673f"},
    "ocean": {"accent": "#4bb8d1", "accent_strong": "#1f6f85"},
    "forest": {"accent": "#6fae62", "accent_strong": "#3f7a34"},
    "mono": {"accent": "#9a9a9a", "accent_strong": "#5a5a5a"},
    "pink": {"accent": "#ff8ac2", "accent_strong": "#e2519b"},
}


def _resolve_widgets(schema: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fill in each field's default widget from FIELD_TYPES so templates only
    ever switch on `field.widget`, never on `field.type` directly."""
    resolved: dict[str, dict[str, Any]] = {}
    for key, field in schema.items():
        widget = field.get("widget", FIELD_TYPES[field["type"]]["widget"])
        resolved_field = {**field, "widget": widget}
        if widget in ("number", "slider") and "step" not in resolved_field:
            # Without an explicit step, HTML number/range inputs default to
            # step=1, which rejects a float field's own unmodified default
            # (e.g. min=0.1, value=1.0) as "not a multiple of step".
            resolved_field["step"] = "1" if field["type"] == "int" else "any"
        resolved[key] = resolved_field
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


def _check_password(candidate: str, password: str) -> bool:
    """Constant-time compare of a submitted password against the configured
    one. secrets.compare_digest avoids leaking the password's length/prefix
    through timing."""
    return secrets.compare_digest(candidate, password)


def _safe_next(candidate: str) -> str:
    """Only ever redirect back within this app - a `next` value pointing
    elsewhere (e.g. `//evil.example`) would otherwise turn the login form
    into an open redirect."""
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return "/ui/"


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
        self._sessions: set[str] = set()
        self.app = FastAPI(title="Alarm Clock")
        self.app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
        self._templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        self._templates.env.globals["webui_accent_colors"] = self._resolve_accent_colors
        self._register_auth()
        self._register_login_routes()
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
        self.logger.info("settings changed: %s", payload)

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
        self.needs_restart = False
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
            "host": {"type": "string", "label": "Host", "requires_restart": True},
            "port": {
                "type": "int",
                "min": 1,
                "max": 65535,
                "label": "Port",
                "requires_restart": True,
            },
            "password": {
                "type": "password",
                "label": "Password (empty disables the login prompt)",
            },
            "color_profile": {
                "type": "select",
                "options": [*COLOR_PROFILES, "custom"],
                "label": "Farbprofil",
                # Consumed client-side by module_settings.html to live-preview
                # a profile before it's saved, without a round-trip per pick.
                "profiles": COLOR_PROFILES,
            },
            "custom_accent": {
                "type": "color",
                "label": 'Akzentfarbe',
            },
            "custom_accent_strong": {
                "type": "color",
                "label": 'kräftiger Akzent',
            },
        }

    def _resolve_accent_colors(self) -> dict[str, str]:
        """Look up the accent colors the currently selected color profile
        implies - used by base.html to theme every page's "important
        elements" (nav, headings, buttons, links, focus rings) from a single
        setting, without every route having to thread it through manually."""
        profile = self.settings.get("color_profile", "sunrise")
        if profile == "custom":
            fallback = COLOR_PROFILES["sunrise"]
            return {
                "accent": self.settings.get("custom_accent") or fallback["accent"],
                "accent_strong": self.settings.get("custom_accent_strong")
                or fallback["accent_strong"],
            }
        return COLOR_PROFILES.get(profile, COLOR_PROFILES["sunrise"])

    async def update_settings(self, values: dict[str, Any]) -> None:
        await super().update_settings(values)
        # A settings change may have set/cleared/rotated the password - drop
        # all logged-in sessions rather than track whether this particular
        # update touched it.
        self._sessions.clear()

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

    def _register_auth(self) -> None:
        """Gate every request behind a shared password, checked via a login
        page that sets a session cookie on success. Applies to the JSON API
        and the /ui pages alike since both are served from the same app -
        there's no route that should be reachable without it once a password
        is set. No password configured (the default) means no login is
        required, so existing installs aren't locked out."""

        @self.app.middleware("http")
        async def require_auth(request: Request, call_next):
            password = self.settings.get("password", "")
            if not password:
                return await call_next(request)

            path = request.url.path
            if path == "/login" or path.startswith("/static"):
                return await call_next(request)

            token = request.cookies.get(SESSION_COOKIE_NAME)
            if token is not None and token in self._sessions:
                return await call_next(request)

            if path.startswith("/ui") or path == "/":
                return RedirectResponse(f"/login?next={path}", status_code=303)
            return JSONResponse(status_code=401, content={"detail": "not authenticated"})

    def _register_login_routes(self) -> None:
        """The login form itself - a single password field, no separate
        identity to enter (a LAN-only device, one shared password)."""
        app = self.app
        templates = self._templates

        @app.get("/login", include_in_schema=False)
        async def login_form(request: Request, next: str = "/ui/", error: str | None = None):
            return templates.TemplateResponse(
                request, "login.html", {"next": _safe_next(next), "error": error}
            )

        @app.post("/login", include_in_schema=False)
        async def login_submit(
            password: str = Form(""), next: str = Form("/ui/")
        ) -> RedirectResponse:
            safe_next = _safe_next(next)
            configured = self.settings.get("password", "")
            if not configured or not _check_password(password, configured):
                return RedirectResponse(
                    f"/login?next={safe_next}&error=Wrong password", status_code=303
                )
            token = secrets.token_urlsafe(32)
            self._sessions.add(token)
            response = RedirectResponse(safe_next, status_code=303)
            response.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="lax")
            return response

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
                    "needs_restart": module.needs_restart,
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

        @app.post("/modules/{name}/restart")
        async def restart_module(name: str) -> dict[str, str]:
            await self._get_module(name).restart()
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
        async def ui_index(
            request: Request,
            error: str | None = None,
            edit: str | None = None,
            confirm_delete: str | None = None,
        ):
            scheduler = self._get_scheduler()
            plan = scheduler.get_plan()
            now = datetime.datetime.now(scheduler.tz)

            reference_date = scheduler.get_alarm_reference_date(now)
            is_skipped = scheduler.is_next_alarm_skipped(now)
            override_time: datetime.time | None = None
            if reference_date is not None and not is_skipped and reference_date in plan.overrides:
                override_time = plan.overrides[reference_date]

            affected_group_id = None
            if reference_date is not None:
                affected_weekday = Weekday(reference_date.weekday())
                affected_group_id = next(
                    (
                        group.id
                        for group in plan.groups
                        if group.enabled and affected_weekday in group.days
                    ),
                    None,
                )

            trigger = scheduler.next_trigger(plan, now)
            if trigger is None:
                next_alarm_hint = "Kein Wecker geplant"
            else:
                delta_days = (trigger.date() - now.date()).days
                if delta_days == 0:
                    day_word = "heute"
                elif delta_days == 1:
                    day_word = "morgen"
                else:
                    day_word = WEEKDAY_LABELS[Weekday(trigger.date().weekday())]
                next_alarm_hint = f"Klingelt {day_word} um {trigger.strftime('%H:%M')} Uhr"

            day_owner: dict[Weekday, str] = {}
            for group in plan.groups:
                if not group.enabled:
                    continue
                for day in group.days:
                    day_owner[day] = group.id

            groups = []
            for group in plan.groups:
                reenable_blocked_by = (
                    sorted(group.days & day_owner.keys(), key=lambda d: d.value)
                    if not group.enabled
                    else []
                )
                groups.append(
                    {
                        "id": group.id,
                        "days": sorted(group.days, key=lambda d: d.value),
                        "time": group.time,
                        "enabled": group.enabled,
                        "reenable_blocked": bool(reenable_blocked_by),
                        "reenable_blocked_days": [
                            WEEKDAY_LABELS[day] for day in reenable_blocked_by
                        ],
                        "is_editing": edit == group.id,
                        "is_confirming_delete": confirm_delete == group.id,
                    }
                )

            modules = []
            for module in self._modules.values():
                schema = await module.get_settings_schema()
                if module.needs_restart:
                    status_class, status_label = "restart", "Neustart nötig"
                elif module.enabled:
                    status_class, status_label = "on", "Läuft"
                else:
                    status_class, status_label = "off", "Aus"
                modules.append(
                    {
                        "name": module.name,
                        "display_name": module.display_name,
                        "enabled": module.enabled,
                        "needs_restart": module.needs_restart,
                        "has_settings": bool(schema),
                        "status_class": status_class,
                        "status_label": status_label,
                    }
                )
            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "plan": plan,
                    "groups": groups,
                    "all_weekdays": list(Weekday),
                    "weekday_labels": WEEKDAY_LABELS,
                    "day_owner": day_owner,
                    "is_skipped": is_skipped,
                    "override_time": override_time,
                    "next_alarm_hint": next_alarm_hint,
                    "affected_group_id": affected_group_id,
                    "any_editing": edit is not None,
                    "modules": modules,
                    "error": error,
                },
            )

        @app.post("/ui/plan/groups", include_in_schema=False)
        async def ui_create_group(
            time: str = Form(""), days: list[str] = Form([])
        ) -> RedirectResponse:
            if not days:
                # No day picked (e.g. Enter pressed while every checkbox was
                # disabled/unchecked) - the confirm button is already inert
                # for mouse clicks via CSS; on the server side this is just
                # a no-op, not an error worth surfacing.
                return RedirectResponse("/ui/", status_code=303)
            scheduler = self._get_scheduler()
            try:
                parsed_days = frozenset(Weekday(int(day)) for day in days)
                parsed_time = datetime.time.fromisoformat(time)
                scheduler.create_group(parsed_days, parsed_time)
            except ValueError as exc:
                return RedirectResponse(f"/ui/?error={exc}", status_code=303)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/groups/{group_id}/update", include_in_schema=False)
        async def ui_update_group(
            group_id: str, time: str = Form(""), days: list[str] = Form([])
        ) -> RedirectResponse:
            if not days:
                return RedirectResponse(f"/ui/?edit={group_id}", status_code=303)
            scheduler = self._get_scheduler()
            try:
                parsed_days = frozenset(Weekday(int(day)) for day in days)
                parsed_time = datetime.time.fromisoformat(time)
                scheduler.update_group(group_id, parsed_days, parsed_time)
            except ValueError as exc:
                return RedirectResponse(f"/ui/?error={exc}&edit={group_id}", status_code=303)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/groups/{group_id}/toggle", include_in_schema=False)
        async def ui_toggle_group(group_id: str) -> RedirectResponse:
            try:
                self._get_scheduler().toggle_group_enabled(group_id)
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

        @app.post("/ui/plan/master/skip", include_in_schema=False)
        async def ui_skip_next_alarm() -> RedirectResponse:
            self._get_scheduler().skip_next_alarm()
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/override", include_in_schema=False)
        async def ui_set_override(time: str = Form("")) -> RedirectResponse:
            scheduler = self._get_scheduler()
            try:
                parsed_time = datetime.time.fromisoformat(time)
            except ValueError as exc:
                return RedirectResponse(f"/ui/?error={exc}", status_code=303)
            scheduler.override_next_alarm_time(parsed_time)
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/plan/override/clear", include_in_schema=False)
        async def ui_clear_override() -> RedirectResponse:
            self._get_scheduler().clear_alarm_override()
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/modules/{name}/enable", include_in_schema=False)
        async def ui_enable_module(name: str) -> RedirectResponse:
            await self._get_module(name).enable()
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/modules/{name}/disable", include_in_schema=False)
        async def ui_disable_module(name: str) -> RedirectResponse:
            await self._get_module(name).disable()
            return RedirectResponse("/ui/", status_code=303)

        @app.post("/ui/modules/{name}/restart", include_in_schema=False)
        async def ui_restart_module(name: str, next: str = Form("/ui/")) -> RedirectResponse:
            await self._get_module(name).restart()
            return RedirectResponse(_safe_next(next), status_code=303)

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
