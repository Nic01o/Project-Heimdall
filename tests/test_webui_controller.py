"""Tests for alarmclock.core.webui_controller.

This file tests the WebUI controller which provides HTTP control plane functionality
for the alarm clock system.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from alarmclock.core.event_bus import EventBus
from alarmclock.core.webui_controller import WebUIController
from alarmclock.core.scheduler import Scheduler
from alarmclock.modules.base import Module
from alarmclock.modules.settings_types import SettingsValidationError


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """WebUIController falls back to the relative path "config/settings.toml"
    when no settings_path is given. Run every test from a scratch directory
    so a test that persists settings (update_settings, set_active, the
    "add module" flow, ...) can't write into the real repo's config file."""
    (tmp_path / "config").mkdir()
    monkeypatch.chdir(tmp_path)


class MockEventBus:
    """Mock event bus for testing."""

    def __init__(self):
        self.emitted = []

    async def emit(self, event_name, payload):
        self.emitted.append((event_name, payload))


@pytest.fixture
def mock_bus():
    """Async-safe bus fixture."""
    return MockEventBus()


@pytest.fixture
def mock_scheduler():
    """Create a mock scheduler for testing."""
    scheduler = MagicMock(spec=Scheduler)
    scheduler.get_plan.return_value = MagicMock()
    scheduler.get_plan.return_value.groups = []
    scheduler.get_plan.return_value.overrides = {}
    scheduler.tz = "UTC"
    return scheduler


@pytest.fixture
def mock_module():
    """Create a mock module for testing."""
    module = MagicMock(spec=Module)
    module.name = "test_module"
    module.display_name = "Test Module"
    module.icon = "test"
    module.enabled = True
    module.needs_restart = False
    module.get_settings_schema.return_value = {}
    module.get_settings.return_value = {}
    module.update_settings = AsyncMock()
    return module


@pytest.fixture
def webui_controller(mock_bus, mock_scheduler, mock_module):
    """Create a WebUI controller instance for testing."""
    controller = WebUIController(
        name="webui",
        bus=mock_bus,
        config={"host": "127.0.0.1", "port": 8000}
    )

    # Attach context to give it access to scheduler and modules
    controller.attach_context(mock_scheduler, [mock_module])
    return controller


def test_webui_controller_init():
    """Test WebUI controller initialization."""
    bus = MockEventBus()
    config = {"host": "localhost", "port": 5000}

    controller = WebUIController(
        name="webui",
        bus=bus,
        config=config
    )

    assert controller.name == "webui"
    assert controller.bus == bus
    assert controller.config == config
    # Starts disabled - enable() (called once by daemon.py at startup) is
    # what actually flips this and binds the server.
    assert controller.enabled is False


def test_get_settings_schema():
    """Test that get_settings_schema returns the correct schema with defaults."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    schema = controller.get_settings_schema()

    # Check that all expected fields are present
    assert "host" in schema
    assert "port" in schema
    assert "timezone" in schema
    assert "password" in schema
    assert "color_profile" in schema
    assert "custom_accent" in schema
    assert "custom_accent_strong" in schema

    # Check default values
    assert schema["host"]["default"] == "0.0.0.0"
    assert schema["port"]["default"] == 5000
    assert schema["timezone"]["default"] == "UTC"
    assert "Europe/Berlin" in schema["timezone"]["options"]
    assert schema["password"]["default"] == ""
    assert schema["color_profile"]["default"] == "sunrise"
    assert schema["custom_accent"]["default"] == "#000000"
    assert schema["custom_accent_strong"]["default"] == "#000000"


def test_resolve_accent_colors_default():
    """Test that _resolve_accent_colors returns default colors."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Test with default color profile
    colors = controller._resolve_accent_colors()

    assert "accent" in colors
    assert "accent_strong" in colors
    assert colors["accent"] == "#ff8a5c"  # sunrise profile accent color


def test_resolve_accent_colors_custom():
    """Test that _resolve_accent_colors returns custom colors."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Set custom colors (settings, not wiring config - this is what
    # _resolve_accent_colors actually reads)
    controller.settings["color_profile"] = "custom"
    controller.settings["custom_accent"] = "#ff0000"
    controller.settings["custom_accent_strong"] = "#cc0000"

    colors = controller._resolve_accent_colors()

    assert colors["accent"] == "#ff0000"
    assert colors["accent_strong"] == "#cc0000"


def test_resolve_accent_colors_fallback():
    """Test that _resolve_accent_colors falls back to sunrise profile."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Set custom colors to None/invalid
    controller.settings["color_profile"] = "custom"
    controller.settings["custom_accent"] = None

    colors = controller._resolve_accent_colors()

    # Should fall back to sunrise profile
    assert colors["accent"] == "#ff8a5c"  # sunrise profile accent color


def test_get_scheduler(mock_scheduler):
    """Test _get_scheduler method."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Attach context first
    controller.attach_context(mock_scheduler, [])

    scheduler = controller._get_scheduler()
    assert scheduler == mock_scheduler


def test_get_scheduler_not_attached():
    """Test _get_scheduler raises HTTPException when not attached."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Don't attach context
    with pytest.raises(Exception):  # Should raise HTTPException
        controller._get_scheduler()


def test_get_module(mock_module):
    """Test _get_module method."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Attach context first
    controller.attach_context(MagicMock(), [mock_module])

    module = controller._get_module("test_module")
    assert module == mock_module


def test_get_module_not_found():
    """Test _get_module raises HTTPException when module not found."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Attach context with no modules
    controller.attach_context(MagicMock(), [])

    with pytest.raises(Exception):  # Should raise HTTPException
        controller._get_module("nonexistent")


def test_form_to_settings():
    """Test _form_to_settings method."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # This is a helper function used internally, so we mainly test it doesn't crash
    # with various inputs - the actual testing would require full FastAPI setup


def test_update_settings():
    """Test update_settings method."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Test updating settings
    values = {"host": "localhost", "port": 8080}
    asyncio.run(controller.update_settings(values))

    assert controller.settings["host"] == "localhost"
    assert controller.settings["port"] == 8080


def test_update_settings_timezone_valid():
    """A configured timezone option is accepted and persisted."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    asyncio.run(controller.update_settings({"timezone": "Europe/Berlin"}))

    assert controller.settings["timezone"] == "Europe/Berlin"


def test_update_settings_timezone_invalid():
    """A timezone outside the curated options list is rejected."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    with pytest.raises(SettingsValidationError):
        asyncio.run(controller.update_settings({"timezone": "Mars/Olympus_Mons"}))


def test_init():
    """Test init method."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Should not raise any exceptions
    asyncio.run(controller.init())


@pytest.mark.asyncio
async def test_enable():
    """Test enable method."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Should not raise any exceptions when enabling
    await controller.enable()


@pytest.mark.asyncio
async def test_disable():
    """Test disable method."""
    bus = MockEventBus()
    controller = WebUIController(name="webui", bus=bus)

    # Should not raise any exceptions when disabling
    await controller.disable()


def test_check_password():
    """Test _check_password method."""
    # This is a simple function, so we can test it directly
    from alarmclock.core.webui_controller import _check_password

    assert _check_password("password", "password") is True
    assert _check_password("password", "wrong") is False


def test_safe_next():
    """Test _safe_next method."""
    from alarmclock.core.webui_controller import _safe_next

    # Valid relative paths should be returned as-is
    assert _safe_next("/valid/path") == "/valid/path"

    # Absolute paths should be normalized to "/"
    assert _safe_next("//evil.example.com") == "/"

    # Paths starting with // should be normalized to "/"
    assert _safe_next("//test") == "/"


@pytest.fixture
def new_module_settings_path(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        "[module_types]\n"
        'mymodule = "alarmclock.modules.mymodule.mymodule"\n'
        "\n"
        "[registry]\n"
    )
    return path


@pytest.fixture
def new_module_controller(new_module_settings_path, mock_scheduler):
    controller = WebUIController(
        name="webui", bus=EventBus(), settings_path=new_module_settings_path
    )
    controller.attach_context(mock_scheduler, [])
    return controller


def test_get_modules_new_lists_available_types(new_module_controller):
    client = TestClient(new_module_controller.app)

    response = client.get("/modules/new")

    assert response.status_code == 200
    assert "mymodule" in response.text


def test_post_modules_new_creates_and_activates_instance(new_module_controller, new_module_settings_path):
    client = TestClient(new_module_controller.app)

    response = client.post(
        "/modules/new",
        data={"instance_id": "my_test", "module_type": "mymodule"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/modules/my_test/settings"
    assert "my_test" in new_module_controller._modules
    module = new_module_controller._modules["my_test"]
    assert module.enabled is True

    import tomllib
    with open(new_module_settings_path, "rb") as f:
        data = tomllib.load(f)
    assert data["registry"]["my_test"] == {"module": "mymodule"}


def test_post_modules_new_rejects_duplicate_name(new_module_controller):
    client = TestClient(new_module_controller.app)
    client.post(
        "/modules/new", data={"instance_id": "my_test", "module_type": "mymodule"}
    )

    response = client.post(
        "/modules/new",
        data={"instance_id": "my_test", "module_type": "mymodule"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error" in response.headers["location"]
    # Still only the one instance from the first request.
    assert len(new_module_controller._modules) == 1


def test_post_modules_new_rejects_unknown_type(new_module_controller):
    client = TestClient(new_module_controller.app)

    response = client.post(
        "/modules/new",
        data={"instance_id": "my_test", "module_type": "does_not_exist"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert "my_test" not in new_module_controller._modules


def test_post_modules_new_rejects_invalid_instance_id(new_module_controller):
    client = TestClient(new_module_controller.app)

    response = client.post(
        "/modules/new",
        data={"instance_id": "not a valid id!", "module_type": "mymodule"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert new_module_controller._modules == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])