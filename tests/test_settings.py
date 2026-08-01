"""Tests for alarmclock.core.settings.Settings class."""

from pathlib import Path
import pytest
import yaml
import alarmclock.core.settings as settings_module
from alarmclock.core.settings import Settings


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """A Settings singleton whose paths are entirely redirected into tmp_path.

    Settings derives its paths from `Path(__file__)`, mixing two conventions:
    settings.yaml/default_settings.yaml/module default_settings.yaml are relative
    to alarmclock/core (one `.parent`), while hardware_config.yaml lives at the
    repo root's config/ dir (three `.parent`s up from alarmclock/core/settings.py).
    We mirror that layout under tmp_path (standing in for the repo root) and
    monkeypatch the module's `__file__` before instantiation, so the singleton
    never reads or writes the real project files.
    """
    core_dir = tmp_path / "alarmclock" / "core"
    core_dir.mkdir(parents=True)
    monkeypatch.setattr(settings_module, "__file__", str(core_dir / "settings.py"))

    Settings._instance = None
    settings = Settings()
    yield settings
    Settings._instance = None


@pytest.fixture
def isolated_settings_with_configs(isolated_settings, tmp_path):
    """isolated_settings plus a full set of hardware/default config files on disk."""
    core_dir = tmp_path / "alarmclock" / "core"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    modules_dir = tmp_path / "alarmclock" / "modules"

    hardware_config = {
        "modules": {
            "button": {"name": "button top", "enabled": True},
            "led": {"name": "led front", "enabled": False},
        }
    }
    with open(config_dir / "hardware_config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(hardware_config, f)

    core_defaults = {
        "core": {
            "scheduler": {"timezone": "Europe/Berlin", "snooze": 8},
        }
    }
    with open(core_dir / "default_settings.yaml", "w", encoding="utf-8") as f:
        yaml.dump(core_defaults, f)

    button_dir = modules_dir / "button"
    button_dir.mkdir(parents=True)
    button_defaults = {"modules": {"button": {"enabled": True, "debounce_time": 100}}}
    with open(button_dir / "default_settings.yaml", "w", encoding="utf-8") as f:
        yaml.dump(button_defaults, f)

    led_dir = modules_dir / "led"
    led_dir.mkdir(parents=True)
    led_defaults = {"modules": {"led": {"enabled": False, "brightness": 50}}}
    with open(led_dir / "default_settings.yaml", "w", encoding="utf-8") as f:
        yaml.dump(led_defaults, f)

    return isolated_settings


# -- Singleton behaviour --------------------------------------------------------

def test_settings_singleton_returns_same_instance():
    """__new__ hands back the same object across repeated calls."""
    Settings._instance = None
    try:
        assert Settings() is Settings()
    finally:
        Settings._instance = None


def test_get_instance_initializes_the_singleton(tmp_path, monkeypatch):
    """get_instance() must fully initialize a fresh singleton, not just allocate it.

    Regression test: get_instance() used to call cls.__new__(cls) directly,
    which skips __init__ entirely, leaving _store as None (the class-level
    default) when get_instance() is the first thing ever called.
    """
    core_dir = tmp_path / "alarmclock" / "core"
    core_dir.mkdir(parents=True)
    monkeypatch.setattr(settings_module, "__file__", str(core_dir / "settings.py"))

    Settings._instance = None
    try:
        settings = Settings.get_instance()
        assert settings._store is not None
        assert settings._settings_path == core_dir / "settings.yaml"
    finally:
        Settings._instance = None


def test_get_instance_and_constructor_share_state(isolated_settings):
    """Settings() and Settings.get_instance() must refer to the same object."""
    assert Settings.get_instance() is isolated_settings
    assert Settings() is isolated_settings


# -- Initialization ---------------------------------------------------------------

def test_settings_initialization_sets_path_and_store(isolated_settings, tmp_path):
    assert isolated_settings._settings_path == tmp_path / "alarmclock" / "core" / "settings.yaml"
    assert isolated_settings._store is not None
    assert isolated_settings._store._path == isolated_settings._settings_path


def test_settings_reinitialization_is_a_noop(isolated_settings):
    """Calling __init__ again (e.g. via Settings()) must not reset state."""
    store_before = isolated_settings._store
    isolated_settings.set_setting("core.marker", "keep-me")

    Settings()  # second construction of the same singleton

    assert isolated_settings._store is store_before
    assert isolated_settings.get_setting("core.marker") == "keep-me"


# -- get_setting / set_setting -----------------------------------------------------

def test_get_setting_returns_default_when_missing(isolated_settings):
    assert isolated_settings.get_setting("nothing.here") is None
    assert isolated_settings.get_setting("nothing.here", "fallback") == "fallback"


def test_set_then_get_setting_roundtrip(isolated_settings):
    isolated_settings.set_setting("core.scheduler.timezone", "America/New_York")
    isolated_settings.set_setting("modules.button.enabled", True)

    assert isolated_settings.get_setting("core.scheduler.timezone") == "America/New_York"
    assert isolated_settings.get_setting("modules.button.enabled") is True


def test_set_setting_persists_to_disk(isolated_settings, tmp_path):
    """set_setting must go through the store, i.e. actually hit the file."""
    isolated_settings.set_setting("core.volume", 42)

    with open(tmp_path / "alarmclock" / "core" / "settings.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    assert content["core"]["volume"] == 42


# -- update_settings (merge) -------------------------------------------------------

def test_update_settings_deep_merges(isolated_settings):
    isolated_settings.set_setting("modules.button.enabled", True)
    isolated_settings.set_setting("modules.button.name", "Physical")

    isolated_settings.update_settings({
        "modules": {"button": {"enabled": False}, "led": {"enabled": True}},
        "core": {"scheduler": {"timezone": "Asia/Tokyo"}},
    })

    assert isolated_settings.get_setting("modules.button.enabled") is False
    assert isolated_settings.get_setting("modules.button.name") == "Physical"
    assert isolated_settings.get_setting("modules.led.enabled") is True
    assert isolated_settings.get_setting("core.scheduler.timezone") == "Asia/Tokyo"


# -- remove_setting -----------------------------------------------------------------

def test_remove_setting_removes_existing_key(isolated_settings):
    isolated_settings.set_setting("test.key", "value")
    assert isolated_settings.remove_setting("test.key") is True
    assert isolated_settings.get_setting("test.key") is None


def test_remove_setting_returns_false_for_missing_key(isolated_settings):
    assert isolated_settings.remove_setting("does.not.exist") is False


# -- load_configuration / get_configuration -----------------------------------------

def test_load_configuration_merges_core_defaults(isolated_settings_with_configs):
    config = isolated_settings_with_configs.load_configuration()
    assert config["core"]["scheduler"]["timezone"] == "Europe/Berlin"
    assert config["core"]["scheduler"]["snooze"] == 8


def test_load_configuration_includes_enabled_module_defaults(isolated_settings_with_configs):
    """Only modules marked enabled in hardware_config.yaml contribute defaults."""
    config = isolated_settings_with_configs.load_configuration()
    assert config["modules"]["button"]["enabled"] is True
    assert config["modules"]["button"]["debounce_time"] == 100


def test_load_configuration_skips_disabled_modules(isolated_settings_with_configs):
    """led is enabled: false in hardware_config.yaml, so its defaults are not merged in."""
    config = isolated_settings_with_configs.load_configuration()
    assert "led" not in config.get("modules", {})


def test_load_configuration_user_settings_override_defaults(isolated_settings_with_configs):
    isolated_settings_with_configs.set_setting("core", {"scheduler": {"timezone": "UTC", "snooze": 8}})

    config = isolated_settings_with_configs.load_configuration()
    assert config["core"]["scheduler"]["timezone"] == "UTC"


def test_load_configuration_persists_merged_config_to_disk(isolated_settings_with_configs, tmp_path):
    """load_configuration() must write the merged result back through the
    store, not just return it - otherwise settings.yaml never reflects the
    effective config (mirrors Module.__init__'s own persist-after-compute
    pattern in modules/base.py)."""
    config = isolated_settings_with_configs.load_configuration()

    with open(tmp_path / "alarmclock" / "core" / "settings.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    assert content == config


def test_load_configuration_missing_default_settings_file_is_tolerated(isolated_settings, tmp_path):
    """default_settings.yaml is optional (existence-checked); hardware_config.yaml is not."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    with open(config_dir / "hardware_config.yaml", "w", encoding="utf-8") as f:
        yaml.dump({"modules": {}}, f)

    config = isolated_settings.load_configuration()
    assert config == {}


def test_load_configuration_missing_hardware_config_raises(isolated_settings):
    """hardware_config.yaml is opened unconditionally, so a missing file is a hard error."""
    with pytest.raises(FileNotFoundError):
        isolated_settings.load_configuration()


def test_get_configuration_caches_after_first_load(isolated_settings_with_configs):
    config1 = isolated_settings_with_configs.get_configuration()
    config2 = isolated_settings_with_configs.get_configuration()
    assert config1 is config2


def test_get_configuration_triggers_load_when_uncached(isolated_settings_with_configs):
    assert isolated_settings_with_configs._configuration is None
    config = isolated_settings_with_configs.get_configuration()
    assert config["core"]["scheduler"]["timezone"] == "Europe/Berlin"


# -- save_configuration / get_store --------------------------------------------------

def test_save_configuration_writes_full_replacement(isolated_settings, tmp_path):
    isolated_settings.set_setting("stale", "value")

    isolated_settings.save_configuration({"fresh": {"data": True}})

    with open(tmp_path / "alarmclock" / "core" / "settings.yaml", "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    assert content == {"fresh": {"data": True}}


# -- reset_to_defaults / backup_configuration -----------------------------------------

def test_reset_to_defaults_clears_cache_and_deletes_file(isolated_settings, tmp_path):
    isolated_settings.set_setting("core.marker", "value")
    isolated_settings._configuration = {"cached": True}
    assert (tmp_path / "alarmclock" / "core" / "settings.yaml").exists()

    isolated_settings.reset_to_defaults()

    assert isolated_settings._configuration is None
    assert not (tmp_path / "alarmclock" / "core" / "settings.yaml").exists()


def test_reset_to_defaults_when_no_file_exists_does_not_raise(isolated_settings):
    isolated_settings.reset_to_defaults()  # no settings.yaml was ever written
    assert isolated_settings._configuration is None


def test_backup_configuration_copies_current_file(isolated_settings, tmp_path):
    isolated_settings.set_setting("core.marker", "backup-me")

    backup_path = isolated_settings.backup_configuration()

    assert backup_path != ""
    backup_file = Path(backup_path)
    assert backup_file.exists()
    assert backup_file.parent == tmp_path / "alarmclock" / "core"

    with open(backup_file, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    assert content["core"]["marker"] == "backup-me"


def test_backup_configuration_without_existing_file_returns_empty_string(isolated_settings):
    assert isolated_settings.backup_configuration() == ""


# -- validation placeholders (documenting current "always true" behaviour) -----------

def test_validate_configuration_is_currently_a_stub(isolated_settings):
    assert isolated_settings.validate_configuration({"anything": "goes"}) is True
    assert isolated_settings.validate_configuration({}) is True


def test_validate_setting_is_currently_a_stub(isolated_settings):
    assert isolated_settings.validate_setting("any.key", "any value") is True
    assert isolated_settings.validate_setting("any.key", None) is True
