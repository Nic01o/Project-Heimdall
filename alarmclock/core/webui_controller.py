"""Core WebUI Controller for alarm clock system.

This controller provides HTTP control plane functionality directly integrated
into the core system. It has direct access to the scheduler and modules, which
is a deliberate design choice since it's a core system component.
"""

from __future__ import annotations
import asyncio
import datetime
import re
import secrets
from pathlib import Path
from typing import Any
import uvicorn
from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.datastructures import FormData
from alarmclock.core.alarm import Weekday
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.base import Module, Configurable, available_module_types, write_registry_entry
from alarmclock.modules.settings_types import (
    FIELD_TYPES,
    TIMEZONES,
    SettingsValidationError,
    validate_against_schema,
)
from alarmclock.core.logger_wrapper import configure_external_logger, logger

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


def _split_reaction_fields(
    schema: dict[str, dict[str, Any]],
    values: dict[str, Any],
    locked_fields: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Pull a module's `reaction_<flag>` fields (see OutputModule) out of the
    generic per-field settings form into a dedicated add/remove list -
    dumping one dropdown per every possible flag stops being usable once a
    module reacts to more than a couple. Locked flags are left in the
    generic schema instead (rendered disabled, like any other locked field),
    since the reactions list only supports flags a user can actually edit.

    Returns `(generic_schema, reactions)`, where `reactions` is None if the
    module has no editable reaction fields at all (i.e. not an OutputModule).
    """
    editable_flags = sorted(
        key[len("reaction_"):]
        for key in schema
        if key.startswith("reaction_") and key not in locked_fields
    )
    generic_schema = {
        key: field
        for key, field in schema.items()
        if not (key.startswith("reaction_") and key[len("reaction_"):] in editable_flags)
    }
    if not editable_flags:
        return generic_schema, None

    options = [opt for opt in schema[f"reaction_{editable_flags[0]}"]["options"] if opt != "ignore"]
    active = [
        {"flag": flag, "value": values.get(f"reaction_{flag}", "ignore")}
        for flag in editable_flags
        if values.get(f"reaction_{flag}", "ignore") != "ignore"
    ]
    active_flags = {entry["flag"] for entry in active}
    free_flags = [flag for flag in editable_flags if flag not in active_flags]
    return generic_schema, {"active": active, "free_flags": free_flags, "options": options}


def _editable_schema(schema: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`active` is controlled via the dedicated enable/disable buttons
    (module.set_active(), see /modules/{name}/enable|disable) - excluded
    here so saving the generic settings form can't silently flip it off via
    the "unchecked checkbox" trap (`_form_to_settings` reads a bool field
    that's missing from the submitted form as False, but a hidden field is
    always "missing")."""
    return {key: field for key, field in schema.items() if key != "active"}


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
        elif field_type == "list":
            indexed_values = []
            i = 0
            sub_props = field.get("item_schema", {}).get("properties", {})
            if sub_props:
                while True:
                    item = {}
                    found_any_for_index = False
                    for sub_key in sub_props:
                        form_key = f"{key}-{i}-{sub_key}"
                        if form_key in form:
                            item[sub_key] = form[form_key]
                            found_any_for_index = True
                    if not found_any_for_index:
                        break
                    indexed_values.append(item)
                    i += 1
                if indexed_values:
                    values[key] = indexed_values
        elif key in form:
            raw = form[key]
            if field_type == "int":
                values[key] = int(raw)
            elif field_type == "float":
                values[key] = float(raw)
            else:
                values[key] = raw
    return values


# An instance id doubles as a TOML table key (settings.toml) and a URL path
# segment (/modules/<id>/...) - keep it to characters that are safe in both.
_INSTANCE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


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
    return "/"


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


class WebUIController(Configurable):
    """HTTP control plane controller. Owns a FastAPI app; routes reach into the
    Scheduler and other modules directly via attach_context()."""

    display_name = "Web UI"
    icon = "globe"

    def __init__(
        self,
        name: str,
        bus: Any,
        config: dict[str, Any] | None = None,
        settings_path: Path | None = None,
    ) -> None:
        super().__init__(name, bus, config, settings_path)
        self.name = name
        self.bus = bus
        self.config = config or {}
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
        # Core component, enabled by default - but starts False so enable()
        # (called once by daemon.py at startup) actually does its job of
        # binding the server instead of no-op'ing on its own guard.
        self.enabled = False

    def attach_context(self, scheduler: Scheduler, modules: list[Module]) -> None:
        """Give webui what it needs to act as a control plane. Called once by
        the daemon after every module has been init()'d."""
        self._scheduler = scheduler
        self._modules = {module.name: module for module in modules}

    # -- Module lifecycle -----------------------------------------------------

    async def init(self) -> None:
        """Initialize the web UI controller."""
        # No special initialization needed - all setup is done in __init__
        pass

    async def enable(self) -> None:
        """Enable the web UI controller."""
        if self.enabled:
            return

        self.enabled = True
        host = self.settings.get("host", "0.0.0.0")
        port = self.settings.get("port", 5000)
        # log_config=None skips uvicorn's own logging setup (which would
        # otherwise print unformatted "INFO:     message" lines) - route its
        # loggers through our own formatter instead.
        config = uvicorn.Config(self.app, host=host, port=port, log_level="info", log_config=None)
        configure_external_logger("uvicorn", "uvicorn")
        configure_external_logger("uvicorn.access", "uvicorn")
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
        while not server.started and not task.done() and waited < 5.0:
            await asyncio.sleep(0.01)
            waited += 0.01

        if not server.started:
            task.cancel()
            logger.error("webui failed to bind %s:%s", host, port, module_name=self.name)
            raise RuntimeError(f"webui failed to bind {host}:{port}")

        self._server = server
        self._server_task = task
        logger.info("webui listening on %s:%s", host, port, module_name=self.name)

    async def disable(self) -> None:
        """Disable the web UI controller."""
        if not self.enabled:
            return

        logger.info("webui shutting down", module_name=self.name)
        self.enabled = False
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            await self._server_task
        self._server = None
        self._server_task = None

    async def on_event(self, event: str, payload: Any = None) -> None:
        """Handle events (no special handling needed for this controller)."""
        pass

    # -- settings ---------------------------------------------------------------

    def get_settings_schema(self) -> dict[str, dict[str, Any]]:
        """Get the settings schema for this controller."""
        schema = {
            "host": {
                "type": "string",
                "label": "Host",
                "requires_restart": True,
                "default": "0.0.0.0",
            },
            "port": {
                "type": "int",
                "min": 1,
                "max": 65535,
                "label": "Port",
                "requires_restart": True,
                "default": 5000,
            },
            "timezone": {
                "type": "select",
                "options": TIMEZONES,
                "label": "Zeitzone",
                "requires_restart": True,
                "default": "UTC",
            },
            "password": {
                "type": "password",
                "label": "Password (empty disables the login prompt)",
                "default": "",
            },
            "color_profile": {
                "type": "select",
                "options": [*COLOR_PROFILES, "custom"],
                "label": "Farbprofil",
                "profiles": COLOR_PROFILES,
                "default": "sunrise",
            },
            "custom_accent": {
                "type": "color",
                "label": 'Akzentfarbe',
                "default": "#000000",
            },
            "custom_accent_strong": {
                "type": "color",
                "label": 'kräftiger Akzent',
                "default": "#000000",
            },
        }
        return schema

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
        """Validate and store new settings. Persists only the diff, same
        convention as Module.update_settings()."""
        schema = self.get_settings_schema()
        validated = validate_against_schema(values, schema)
        self.settings = {**self.settings, **validated}
        await self.save_config(self.name, validated)
        # A settings change may have set/cleared/rotated the password - drop
        # all logged-in sessions rather than track whether this particular
        # update touched it.
        self._sessions.clear()
        logger.info(
            "webui settings updated: %s",
            ", ".join(sorted(validated)) or "(none)",
            module_name=self.name,
        )

    # -- helpers used by routes ---------------------------------------------

    def _get_scheduler(self) -> Scheduler:
        """Get the scheduler instance (raises HTTPException if not attached)."""
        if self._scheduler is None:
            raise HTTPException(status_code=503, detail="scheduler not attached")
        return self._scheduler

    def _get_module(self, name: str) -> Module:
        """Get a module by name (raises HTTPException if not found)."""
        module = self._modules.get(name)
        if module is None:
            raise HTTPException(status_code=404, detail=f"unknown module {name!r}")
        return module

    def _get_weekday(self, day: str) -> Weekday:
        """Get a weekday enum from string."""
        try:
            return Weekday[day.upper()]
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown weekday {day!r}") from None

    def _require_free_day(self, scheduler: Scheduler, day: Weekday) -> None:
        """Check if a day is free (raises HTTPException if not)."""
        if scheduler.is_day_assigned(day):
            raise HTTPException(
                status_code=409,
                detail=f"{day.name.capitalize()} already belongs to a sleep plan group",
            )

    def _register_auth(self) -> None:
        """Gate every request behind a shared password, checked via a login
        page that sets a session cookie on success. Applies to the JSON API
        (/api) and the HTML pages alike since both are served from the same
        app - there's no route that should be reachable without it once a
        password is set. No password configured (the default) means no login
        is required, so existing installs aren't locked out."""

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

            if path.startswith("/api"):
                return JSONResponse(status_code=401, content={"detail": "not authenticated"})
            return RedirectResponse(f"/login?next={path}", status_code=303)

    def _register_login_routes(self) -> None:
        """The login form itself - a single password field, no separate
        identity to enter (a LAN-only device, one shared password)."""
        app = self.app
        templates = self._templates

        @app.get("/login", include_in_schema=False)
        async def login_form(request: Request, next: str = "/", error: str | None = None):
            return templates.TemplateResponse(
                request, "login.html", {"next": _safe_next(next), "error": error}
            )

        @app.post("/login", include_in_schema=False)
        async def login_submit(
            request: Request, password: str = Form(""), next: str = Form("/")
        ) -> RedirectResponse:
            safe_next = _safe_next(next)
            client_host = request.client.host if request.client else "unknown"
            configured = self.settings.get("password", "")
            if not configured or not _check_password(password, configured):
                logger.warning(
                    "failed login attempt from %s", client_host, module_name=self.name
                )
                return RedirectResponse(
                    f"/login?next={safe_next}&error=Wrong password", status_code=303
                )
            token = secrets.token_urlsafe(32)
            self._sessions.add(token)
            logger.info("login from %s", client_host, module_name=self.name)
            response = RedirectResponse(safe_next, status_code=303)
            response.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="lax")
            return response

    def _register_routes(self) -> None:
        """Register all API routes."""
        app = APIRouter(prefix="/api")

        @app.get("/plan")
        async def get_plan() -> dict[str, Any]:
            return (await self._get_scheduler().get_plan()).to_dict()

        @app.post("/plan/groups")
        async def create_group(payload: GroupCreate) -> dict[str, Any]:
            scheduler = self._get_scheduler()
            try:
                days = frozenset(Weekday(day) for day in payload.days)
                time = datetime.time.fromisoformat(payload.time)
                group = await scheduler.create_group(days, time)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return group.to_dict()

        @app.post("/plan/groups/{group_id}")
        async def update_group(group_id: str, payload: GroupTimeUpdate) -> dict[str, Any]:
            scheduler = self._get_scheduler()
            try:
                time = datetime.time.fromisoformat(payload.time)
                await scheduler.set_group_time(group_id, time, permanent=payload.permanent)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return (await scheduler.get_plan()).to_dict()

        @app.delete("/plan/groups/{group_id}")
        async def remove_group(group_id: str) -> dict[str, str]:
            try:
                await self._get_scheduler().delete_group(group_id)
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
                    await scheduler.create_group(frozenset({weekday}), time)
                else:
                    await scheduler.set_day_once(weekday, time)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return (await scheduler.get_plan()).to_dict()

        @app.post("/plan/disable")
        async def disable_plan() -> dict[str, str]:
            await self._get_scheduler().set_enabled(False)
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
            result = []
            for module in self._modules.values():
                # Determine the type based on the class name
                module_type = module.__class__.__name__.replace("Module", "").lower()

                # Create a more descriptive display_name that includes hardware-specific info
                display_name = module.display_name
                if hasattr(module, 'pin') and module_type in ('led', 'button'):
                    # Include pin information for LED and Button modules to make them more identifiable
                    display_name = f"{module.display_name} (Pin {module.pin})"

                result.append({
                    "name": module.name,
                    "type": module_type,
                    "enabled": module.enabled,
                    "needs_restart": module.needs_restart,
                    "display_name": display_name,
                    "icon": module.icon,
                })
            return result

        @app.post("/modules/{name}/enable")
        async def enable_module(name: str) -> dict[str, str]:
            try:
                await self._get_module(name).set_active(True)
            except SettingsValidationError as exc:
                logger.warning(
                    "enabling module %s failed: %s", name, exc, module_name=self.name
                )
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            logger.info("module %s enabled", name, module_name=self.name)
            return {"status": "ok"}

        @app.post("/modules/{name}/disable")
        async def disable_module(name: str) -> dict[str, str]:
            try:
                await self._get_module(name).set_active(False)
            except SettingsValidationError as exc:
                logger.warning(
                    "disabling module %s failed: %s", name, exc, module_name=self.name
                )
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            logger.info("module %s disabled", name, module_name=self.name)
            return {"status": "ok"}

        @app.post("/modules/{name}/restart")
        async def restart_module(name: str) -> dict[str, str]:
            await self._get_module(name).restart()
            logger.info("module %s restarted", name, module_name=self.name)
            return {"status": "ok"}

        @app.get("/modules/{name}/settings/schema")
        async def get_settings_schema(name: str) -> dict[str, Any]:
            return self._get_module(name).get_settings_schema()

        @app.get("/modules/{name}/settings")
        async def get_settings(name: str) -> dict[str, Any]:
            return await self._get_module(name).get_settings()

        @app.post("/modules/{name}/settings")
        async def update_settings(name: str, values: dict[str, Any]) -> dict[str, Any]:
            module = self._get_module(name)
            try:
                await module.update_settings(values)
            except SettingsValidationError as exc:
                logger.warning(
                    "updating settings for module %s failed: %s",
                    name,
                    exc,
                    module_name=self.name,
                )
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            logger.info("settings updated for module %s", name, module_name=self.name)
            return await module.get_settings()

        self.app.include_router(app)

    def _register_ui_routes(self) -> None:
        """Register all server-rendered HTML UI routes."""
        app = self.app
        templates = self._templates

        @app.get("/", include_in_schema=False)
        async def ui_index(
            request: Request,
            error: str | None = None,
            edit: str | None = None,
            confirm_delete: str | None = None,
        ):
            scheduler = self._get_scheduler()
            plan = await scheduler.get_plan()
            now = datetime.datetime.now(scheduler.tz)
            status = scheduler.get_alarm_status(now)

            if status.trigger is None:
                next_alarm_hint = "Kein Wecker geplant"
            else:
                delta_days = (status.trigger.date() - now.date()).days
                if delta_days == 0:
                    day_word = "heute"
                elif delta_days == 1:
                    day_word = "morgen"
                else:
                    day_word = WEEKDAY_LABELS[Weekday(status.trigger.date().weekday())]
                next_alarm_hint = f"Klingelt {day_word} um {status.trigger.strftime('%H:%M')} Uhr"

            day_owner = scheduler.day_owner()

            groups = []
            for group in plan.groups:
                reenable_blocked_by = scheduler.blocking_days(group)
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
                schema = module.get_settings_schema()
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
                    "is_skipped": status.is_skipped,
                    "override_time": status.override_time,
                    "next_alarm_hint": next_alarm_hint,
                    "affected_group_id": status.affected_group_id,
                    "any_editing": edit is not None,
                    "modules": modules,
                    "error": error,
                },
            )

        @app.post("/plan/groups", include_in_schema=False)
        async def ui_create_group(
            time: str = Form(""), days: list[str] = Form([])
        ) -> RedirectResponse:
            if not days:
                # No day picked (e.g. Enter pressed while every checkbox was
                # disabled/unchecked) - the confirm button is already inert
                # for mouse clicks via CSS; on the server side this is just
                # a no-op, not an error worth surfacing.
                return RedirectResponse("/", status_code=303)
            scheduler = self._get_scheduler()
            try:
                parsed_days = frozenset(Weekday(int(day)) for day in days)
                parsed_time = datetime.time.fromisoformat(time)
                await scheduler.create_group(parsed_days, parsed_time)
            except ValueError as exc:
                return RedirectResponse(f"/?error={exc}", status_code=303)
            return RedirectResponse("/", status_code=303)

        @app.post("/plan/groups/{group_id}/update", include_in_schema=False)
        async def ui_update_group(
            group_id: str, time: str = Form(""), days: list[str] = Form([])
        ) -> RedirectResponse:
            if not days:
                return RedirectResponse(f"/?edit={group_id}", status_code=303)
            scheduler = self._get_scheduler()
            try:
                parsed_days = frozenset(Weekday(int(day)) for day in days)
                parsed_time = datetime.time.fromisoformat(time)
                await scheduler.update_group(group_id, parsed_days, parsed_time)
            except ValueError as exc:
                return RedirectResponse(f"/?error={exc}&edit={group_id}", status_code=303)
            return RedirectResponse("/", status_code=303)

        @app.post("/plan/groups/{group_id}/toggle", include_in_schema=False)
        async def ui_toggle_group(group_id: str) -> RedirectResponse:
            try:
                await self._get_scheduler().toggle_group_enabled(group_id)
            except ValueError as exc:
                return RedirectResponse(f"/?error={exc}", status_code=303)
            return RedirectResponse("/", status_code=303)

        @app.post("/plan/groups/{group_id}/delete", include_in_schema=False)
        async def ui_delete_group(group_id: str) -> RedirectResponse:
            try:
                await self._get_scheduler().delete_group(group_id)
            except ValueError as exc:
                return RedirectResponse(f"/?error={exc}", status_code=303)
            return RedirectResponse("/", status_code=303)

        @app.post("/plan/master/skip", include_in_schema=False)
        async def ui_skip_next_alarm() -> RedirectResponse:
            await self._get_scheduler().skip_next_alarm()
            return RedirectResponse("/", status_code=303)

        @app.post("/plan/override", include_in_schema=False)
        async def ui_set_override(time: str = Form("")) -> RedirectResponse:
            scheduler = self._get_scheduler()
            try:
                parsed_time = datetime.time.fromisoformat(time)
            except ValueError as exc:
                return RedirectResponse(f"/?error={exc}", status_code=303)
            await scheduler.override_next_alarm_time(parsed_time)
            return RedirectResponse("/", status_code=303)

        @app.post("/plan/override/clear", include_in_schema=False)
        async def ui_clear_override() -> RedirectResponse:
            await self._get_scheduler().clear_alarm_override()
            return RedirectResponse("/", status_code=303)

        @app.post("/modules/{name}/enable", include_in_schema=False)
        async def ui_enable_module(name: str) -> RedirectResponse:
            try:
                await self._get_module(name).set_active(True)
            except SettingsValidationError as exc:
                logger.warning(
                    "enabling module %s failed: %s", name, exc, module_name=self.name
                )
                return RedirectResponse(f"/?error={exc}", status_code=303)
            logger.info("module %s enabled", name, module_name=self.name)
            return RedirectResponse("/", status_code=303)

        @app.post("/modules/{name}/disable", include_in_schema=False)
        async def ui_disable_module(name: str) -> RedirectResponse:
            try:
                await self._get_module(name).set_active(False)
            except SettingsValidationError as exc:
                logger.warning(
                    "disabling module %s failed: %s", name, exc, module_name=self.name
                )
                return RedirectResponse(f"/?error={exc}", status_code=303)
            logger.info("module %s disabled", name, module_name=self.name)
            return RedirectResponse("/", status_code=303)

        @app.post("/modules/{name}/restart", include_in_schema=False)
        async def ui_restart_module(name: str, next: str = Form("/")) -> RedirectResponse:
            await self._get_module(name).restart()
            logger.info("module %s restarted", name, module_name=self.name)
            return RedirectResponse(_safe_next(next), status_code=303)

        @app.get("/modules/new", include_in_schema=False)
        async def ui_new_module_form(request: Request, error: str | None = None):
            module_types = {
                key: (cls.display_name or key)
                for key, cls in available_module_types(self._settings_path).items()
            }
            return templates.TemplateResponse(
                request,
                "add_module.html",
                {"module_types": module_types, "error": error},
            )

        @app.post("/modules/new", include_in_schema=False)
        async def ui_create_module(
            instance_id: str = Form(""), module_type: str = Form("")
        ) -> RedirectResponse:
            instance_id = instance_id.strip()
            if not _INSTANCE_ID_RE.match(instance_id):
                return RedirectResponse(
                    "/modules/new?error=Name+darf+nur+Buchstaben%2C+Zahlen%2C+-+und+_+enthalten",
                    status_code=303,
                )
            if instance_id in self._modules:
                return RedirectResponse(
                    f"/modules/new?error=%22{instance_id}%22+existiert+bereits", status_code=303
                )

            module_cls = available_module_types(self._settings_path).get(module_type)
            if module_cls is None:
                return RedirectResponse(
                    f"/modules/new?error=unbekannter+Modultyp+%22{module_type}%22", status_code=303
                )

            try:
                write_registry_entry(self._settings_path, instance_id, module_type)
            except ValueError as exc:
                return RedirectResponse(f"/modules/new?error={exc}", status_code=303)

            try:
                module = module_cls(
                    name=instance_id, bus=self.bus, config={"module": module_type},
                    settings_path=self._settings_path,
                )
                await module.load_config(instance_id)
                await module.init()
                if module.settings.get("active", True):
                    await module.enable()
            except Exception as exc:
                logger.error(
                    "failed to bring up new module %s (%s): %s",
                    instance_id, module_type, exc, module_name=self.name,
                )
                return RedirectResponse(
                    f"/modules/new?error=Modul+angelegt%2C+Start+fehlgeschlagen%3A+{exc}",
                    status_code=303,
                )

            self._modules[instance_id] = module
            logger.info(
                "module %s (%s) added", instance_id, module_type, module_name=self.name
            )
            return RedirectResponse(f"/modules/{instance_id}/settings", status_code=303)

        @app.get("/modules/{name}/settings", include_in_schema=False)
        async def ui_module_settings(request: Request, name: str, error: str | None = None):
            module = self._get_module(name)
            schema = _editable_schema(module.get_settings_schema())
            values = await module.get_settings()
            generic_schema, reactions = _split_reaction_fields(schema, values, module.locked_fields)
            return templates.TemplateResponse(
                request,
                "module_settings.html",
                {
                    "module": module,
                    "schema": _resolve_widgets(generic_schema),
                    "reactions": reactions,
                    "values": values,
                    "locked_fields": module.locked_fields,
                    "error": error,
                },
            )

        async def _set_reaction(name: str, flag: str, reaction: str) -> RedirectResponse:
            module = self._get_module(name)
            try:
                await module.update_settings({f"reaction_{flag}": reaction})
            except SettingsValidationError as exc:
                logger.warning(
                    "setting reaction %s for module %s failed: %s",
                    flag,
                    name,
                    exc,
                    module_name=self.name,
                )
                return RedirectResponse(f"/modules/{name}/settings?error={exc}", status_code=303)
            logger.info(
                "module %s: reaction for %s set to %s", name, flag, reaction, module_name=self.name
            )
            return RedirectResponse(f"/modules/{name}/settings", status_code=303)

        @app.post("/modules/{name}/reactions", include_in_schema=False)
        async def ui_add_reaction(
            name: str, flag: str = Form(...), reaction: str = Form(...)
        ) -> RedirectResponse:
            return await _set_reaction(name, flag, reaction)

        @app.post("/modules/{name}/reactions/{flag}", include_in_schema=False)
        async def ui_update_reaction(name: str, flag: str, reaction: str = Form(...)) -> RedirectResponse:
            return await _set_reaction(name, flag, reaction)

        @app.post("/modules/{name}/settings", include_in_schema=False)
        async def ui_update_module_settings(name: str, request: Request) -> RedirectResponse:
            module = self._get_module(name)
            schema = _editable_schema(module.get_settings_schema())
            form = await request.form()
            try:
                values = _form_to_settings(form, schema)
                await module.update_settings(values)
            except (SettingsValidationError, ValueError) as exc:
                logger.warning(
                    "updating settings for module %s failed: %s",
                    name,
                    exc,
                    module_name=self.name,
                )
                return RedirectResponse(
                    f"/modules/{name}/settings?error={exc}", status_code=303
                )
            logger.info("settings updated for module %s", name, module_name=self.name)
            return RedirectResponse(f"/modules/{name}/settings", status_code=303)

        @app.get("/settings", include_in_schema=False)
        async def ui_webui_settings(request: Request):
            schema = self.get_settings_schema()
            resolved_schema = _resolve_widgets(schema)
            return templates.TemplateResponse(
                request,
                "webui_settings.html",
                {
                    "schema": resolved_schema,
                    "values": self.settings,
                },
            )

        @app.post("/settings", include_in_schema=False)
        async def ui_update_webui_settings(request: Request) -> RedirectResponse:
            schema = self.get_settings_schema()
            form = await request.form()
            try:
                values = _form_to_settings(form, schema)
                await self.update_settings(values)
            except (SettingsValidationError, ValueError) as exc:
                logger.warning("updating webui settings failed: %s", exc, module_name=self.name)
                return RedirectResponse(
                    f"/settings?error={exc}", status_code=303
                )
            return RedirectResponse("/settings", status_code=303)
