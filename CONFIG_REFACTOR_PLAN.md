# Configuration System Refactoring Plan

## Problem

Settings are currently spread across multiple half-finished mechanisms (YAML defaults, Python
defaults, a broken `Settings.load_configuration()`, an unused `hardware_config.py`) with no clean
separation between:

- hardware wiring (which module instances exist on this device, what drives them) — deployment
  concern, rarely changes
- user-tunable settings (pin values, intervals, flags, ...) — end-user concern, changes often via
  the web UI

This refactor replaces all of it with one system, built fresh rather than patched onto any of the
existing attempts.

## Architecture

Two files, two audiences, two write paths:

| File | Contains | Written by | Written when |
|---|---|---|---|
| `hardware.toml` | instance registry: id, module class, driver, locked field overrides | hand-edited | daemon stopped |
| `settings.toml` | per-instance schema overrides (diff from schema defaults only) | web UI (`update_settings()`) | daemon running |

The web UI may **read** `hardware.toml` (e.g. to show "pin locked to 22") but never writes it —
this is what makes "web UI can never touch wiring" a structural guarantee (separate file) rather
than an application-level check.

Settings schema (`get_settings_schema()` / `FIELD_TYPES` in `settings_types.py`) stays as-is —
already implemented, already used by `led.py` and the generic web UI form rendering. Defaults live
in the schema (code); `settings.toml` only ever stores the delta from those defaults, so:
- a hand-edited `settings.toml` stays short,
- a schema default change applies automatically to instances that never overrode it.

### Field editability (D6)

Two independent, already-existing mechanisms, carried forward as-is:

1. **Not in the schema at all** → pure wiring (e.g. `driver`, module class). Lives only in
   `hardware.toml`. The web UI can't render or edit it because it only ever renders
   `get_settings_schema()` — nothing hides it, it simply isn't a setting.
2. **In the schema but locked for this instance** → `hardware.toml`'s per-instance `overrides`
   populate `locked_fields` (`base.py:61-71`); `update_settings()` rejects writes to locked fields
   (`base.py:134-136`); the web UI renders them read-only, not hidden (`webui_controller.py:726`).

### Multi-instance mapping (D3)

`hardware.toml`'s instance `id` becomes the storage key in `settings.toml` (replaces today's
module *name* as key, e.g. `modules.led` → `led_front` / `led_back`).

### Example

```toml
# hardware.toml
[[instances]]
id = "led_front"
module = "led"
driver = "real"
[instances.overrides]
pin = 22

[[instances]]
id = "led_back"
module = "led"
driver = "mock"
```

```toml
# settings.toml
[led_front]
blink_interval_seconds = 0.5

[led_back]
active = false
```

## Decisions

| # | Decision | Status |
|---|---|---|
| D1 | Web UI is the primary settings editor; hand-editing `hardware.toml` is the fallback for webui-less devices, built last | done |
| D2 | Legacy `Settings` API (`load_configuration`, dot-path `_Store` methods) is removed, not repaired | done |
| D3 | Multiple instances per module type, built in now | done |
| D4 | `WebUIController` moves onto the same `self.settings` + store pattern as `base.Module` (currently only mutates `self.config` in memory, never persists) | done |
| D5 | Storage format: TOML | done |
| D6 | Field editability: schema-membership + `locked_fields`, see above | done |

## Implementation checklist

- [x] `config/hardware.toml` — instance registry, replaces `config/hardware_config.py`
- [x] `config/settings.toml` — replaces `config/settings.yaml`
- [x] New store (`alarmclock/core/settings.py`): `HardwareRegistry` (reads `hardware.toml`,
      read-only) + `SettingsStore` (get/set on `settings.toml`, `set()` merges into existing
      overrides). Dropped `_Store`'s dot-path API and `Settings.load_configuration/
      save_configuration/reset_to_defaults/backup_configuration`. `tests/test_store.py` /
      `tests/test_settings.py` rewritten to test the new classes.
- [x] TOML writer dependency: `tomli-w` (reading is stdlib `tomllib`, 3.11+)
- [x] `base.py`: seeds `self.settings` from schema defaults + `settings.toml[instance_id]` (key is
      now the instance id, not `modules.<type>`); no more full-dict write on construction, only
      `update_settings()`/`set_active()` persist (and only the diff); `overrides` alone (no more
      separate `override_enabled` flag) drives `locked_fields`
- [x] `daemon.py`: reads `hardware.toml` via `HardwareRegistry` to build instances (id + module +
      driver + overrides), passes only wiring fields as `config=`; fixed module-class lookup
      (was guessing `Type.capitalize()`, now finds the concrete `Module` subclass defined in
      `alarmclock.modules.<type>.<type>`); `enable()`/`disable()` now actually called per
      instance's `active` setting on boot. Found + fixed along the way: `led.py` imported
      `default_settings` as a bare top-level module instead of relatively, so it silently could
      never actually be imported via the normal package path. Smoke-tested end to end
      (`hardware.toml`'s `led_front` instance loads, inits, and enables with the mock driver).
- [x] `webui_controller.py`: seeds `self.settings` from schema defaults + `store.get("webui")`
      (same convention as `base.Module`); `enable()`/`_resolve_accent_colors()`/password checks
      read `self.settings` instead of `self.config`; `update_settings()` validates + persists
      only the diff via `store.set("webui", validated)`. Fixed `self.enabled = True` being set
      unconditionally in `__init__`, which made `enable()`'s own idempotency guard silently no-op
      every startup - webui's uvicorn server never actually bound a port before this. Fixing that
      exposed a real bug in `logger_wrapper.py` (`module_name` was positional before `*args`, so
      any positional format args after the message landed in the wrong slot) - made `module_name`
      keyword-only across `debug/info/warning/error/critical`; this incidentally also fixed the
      pre-existing unrelated `test_integration.py` failure. Full suite: 113 passed. Manually
      smoke-tested `python -m alarmclock.daemon` end to end - webui now actually binds
      `0.0.0.0:5000` and logs correctly.
      - Reading `hardware.toml` from the web UI to annotate locked fields (the read-only half of
        D6) is not yet wired up - tracked as follow-up, not done in this pass.
- [x] deleted `config/hardware_config.py` (superseded by `hardware.toml`), the leftover empty
      `config/settings.yaml` (superseded by `settings.toml`), and `pyyaml` from
      `requirements.txt` (nothing left imports `yaml`)
