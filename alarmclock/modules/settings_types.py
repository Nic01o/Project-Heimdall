"""Shared vocabulary for settings fields.
Each type has one fixed meaning for validation AND rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEZONES: list[str] = [
    "UTC",
    "Europe/Berlin",
    "Europe/Vienna",
    "Europe/Zurich",
    "Europe/London",
    "Europe/Paris",
    "Europe/Madrid",
    "Europe/Rome",
    "Europe/Moscow",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Asia/Kolkata",
    "Australia/Sydney",
]

def detect_system_timezone(
    etc_timezone: Path = Path("/etc/timezone"), etc_localtime: Path = Path("/etc/localtime")
) -> str:
    """Best-effort IANA zone name of the host machine (e.g. "Europe/Berlin"),
    read straight from the OS - this device is Linux-only (see the RPi.GPIO
    dependency), so the two standard sources below cover it. Falls back to
    "UTC" if neither is available/valid (e.g. a minimal container without
    tzdata) - used as the "Zeitzone" setting's default so a fresh install
    matches the physical clock's location without manual configuration."""
    if etc_timezone.is_file():
        name = etc_timezone.read_text().strip()
        if _is_valid_zone(name):
            return name

    try:
        localtime_target = etc_localtime.resolve()
    except OSError:
        localtime_target = None
    if localtime_target is not None and "zoneinfo" in localtime_target.parts:
        name = "/".join(localtime_target.parts[localtime_target.parts.index("zoneinfo") + 1:])
        if _is_valid_zone(name):
            return name

    return "UTC"


def _is_valid_zone(name: str) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


FIELD_TYPES: dict[str, dict[str, Any]] = {
    "int": {"widget": "number"},
    "float": {"widget": "number"},
    "bool": {"widget": "toggle"},
    "string": {"widget": "text"},
    "password": {"widget": "password"},  # masked in the UI, e.g. WiFi key
    "select": {"widget": "dropdown"},  # needs "options"
    "multiselect": {"widget": "checkboxes"},  # needs "options"
    "color": {"widget": "colorpicker"},
    "list": {"widget": "none"},
    "list": {"widget": "none"},
}


class SettingsValidationError(ValueError):
    """Raised when values don't match a settings schema."""


def validate_against_schema(
        values: dict[str, Any], schema: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Validate `values` against `schema` (as returned by `get_settings_schema`).

    Returns the validated subset of `values`. Rejects unknown keys, unknown
    field types, and values that don't match their declared type/options/range.
    """
    validated: dict[str, Any] = {}
    for key, value in values.items():
        if key not in schema:
            raise SettingsValidationError(f"unknown setting {key!r}")
        field = schema[key]
        field_type = field.get("type")
        if field_type not in FIELD_TYPES:
            raise SettingsValidationError(f"{key!r} has unknown field type {field_type!r}")
        validated[key] = _validate_field(key, value, field, field_type)
    return validated


def _validate_field(key: str, value: Any, field: dict[str, Any], field_type: str) -> Any:
    if field_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SettingsValidationError(f"{key!r} must be an int")
        _check_range(key, value, field)
        return value

    if field_type == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SettingsValidationError(f"{key!r} must be a number")
        value = float(value)
        _check_range(key, value, field)
        return value

    if field_type == "bool":
        if not isinstance(value, bool):
            raise SettingsValidationError(f"{key!r} must be a bool")
        return value

    if field_type in ("string", "password", "color"):
        if not isinstance(value, str):
            raise SettingsValidationError(f"{key!r} must be a string")
        return value

    if field_type == "select":
        options = field.get("options", [])
        if value not in options:
            raise SettingsValidationError(f"{key!r} must be one of {options!r}")
        return value

    if field_type == "multiselect":
        options = field.get("options", [])
        if not isinstance(value, list) or any(v not in options for v in value):
            raise SettingsValidationError(f"{key!r} must be a subset of {options!r}")
        return value

    raise SettingsValidationError(f"{key!r} has unhandled field type {field_type!r}")


def _check_range(key: str, value: float, field: dict[str, Any]) -> None:
    minimum = field.get("min")
    maximum = field.get("max")
    if minimum is not None and value < minimum:
        raise SettingsValidationError(f"{key!r} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise SettingsValidationError(f"{key!r} must be <= {maximum}")
