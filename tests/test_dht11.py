"""Tests for the DHT11 module: settings schema, the poll loop that emits
dht11.reading events, graceful handling of failed reads, and both the mock
and real (Adafruit_DHT-mocked) sensor drivers.
"""

import asyncio
import sys
import types

from alarmclock.core.event_bus import EventBus
from alarmclock.modules.dht11.dht11 import DHT11Module
from alarmclock.modules.dht11.mock import MockDHT11Driver


def make_module(**extra_settings) -> DHT11Module:
    config = {"pin": 4, **extra_settings}
    return DHT11Module("dht11", EventBus(), config)


def test_settings_schema_includes_pin_driver_and_interval():
    async def scenario():
        module = make_module()
        schema = await module.get_settings_schema()
        assert schema["pin"] == {"type": "int", "min": 0, "max": 40, "label": "GPIO Pin"}
        assert schema["driver"]["type"] == "select"
        assert schema["driver"]["options"] == ["mock", "real"]
        assert schema["read_interval_seconds"]["type"] == "float"

    asyncio.run(scenario())


def test_pin_property_reads_from_settings():
    module = make_module()
    assert module.pin == 4


def test_defaults_to_mock_driver():
    async def scenario():
        module = make_module()
        await module.init()
        assert isinstance(module._driver, MockDHT11Driver)
        assert module._driver.pin == 4

    asyncio.run(scenario())


def test_poll_loop_emits_reading_events():
    async def scenario():
        bus = EventBus()
        module = DHT11Module("dht11", bus, {"pin": 4, "read_interval_seconds": 0.01})
        await module.init()
        module._driver.temperature_c = 23.5
        module._driver.humidity = 40.0

        readings = []

        async def on_reading(payload):
            readings.append(payload)

        bus.subscribe("dht11.reading", on_reading)

        await module.enable()
        await asyncio.sleep(0.03)
        await module.disable()

        assert len(readings) >= 2
        assert readings[0] == {"name": "dht11", "pin": 4, "temperature_c": 23.5, "humidity": 40.0}

    asyncio.run(scenario())


def test_failed_read_is_skipped_not_raised():
    async def scenario():
        bus = EventBus()
        module = DHT11Module("dht11", bus, {"pin": 4, "read_interval_seconds": 0.01})
        await module.init()
        module._driver.fail = True

        readings = []
        bus.subscribe("dht11.reading", lambda payload: readings.append(payload))

        await module.enable()
        await asyncio.sleep(0.03)
        await module.disable()

        assert readings == []

    asyncio.run(scenario())


def test_disable_stops_the_poll_loop():
    async def scenario():
        module = make_module(read_interval_seconds=0.01)
        await module.init()
        await module.enable()
        assert module._poll_task is not None

        await module.disable()
        assert module._poll_task is None
        assert module.enabled is False

    asyncio.run(scenario())


def _install_fake_adafruit_dht() -> types.ModuleType:
    """Injects a fake Adafruit_DHT module into sys.modules so real.py's
    driver logic can be verified without actual Raspberry Pi hardware."""
    calls: list[tuple] = []

    fake_dht = types.ModuleType("Adafruit_DHT")
    fake_dht.DHT11 = "DHT11"
    fake_dht.calls = calls
    fake_dht.result = (40.0, 23.5)  # (humidity, temperature)
    fake_dht.read_retry = lambda sensor, pin: (calls.append(("read_retry", sensor, pin)), fake_dht.result)[1]

    sys.modules["Adafruit_DHT"] = fake_dht
    sys.modules.pop("alarmclock.modules.dht11.real", None)
    return fake_dht


def test_real_dht11_driver_reads_temperature_and_humidity():
    fake_dht = _install_fake_adafruit_dht()
    try:
        from alarmclock.modules.dht11.real import RealDHT11Driver

        driver = RealDHT11Driver(pin=4)
        result = asyncio.run(driver.read())

        assert fake_dht.calls == [("read_retry", "DHT11", 4)]
        assert result == (23.5, 40.0)  # (temperature, humidity)
    finally:
        sys.modules.pop("Adafruit_DHT", None)
        sys.modules.pop("alarmclock.modules.dht11.real", None)


def test_real_dht11_driver_returns_none_on_failed_read():
    fake_dht = _install_fake_adafruit_dht()
    fake_dht.result = (None, None)
    try:
        from alarmclock.modules.dht11.real import RealDHT11Driver

        driver = RealDHT11Driver(pin=4)
        assert asyncio.run(driver.read()) is None
    finally:
        sys.modules.pop("Adafruit_DHT", None)
        sys.modules.pop("alarmclock.modules.dht11.real", None)


def test_dht11_module_selects_real_driver_when_configured():
    _install_fake_adafruit_dht()
    try:
        module = DHT11Module("dht11", EventBus(), {"pin": 4, "driver": "real"})
        asyncio.run(module.init())

        from alarmclock.modules.dht11.real import RealDHT11Driver

        assert isinstance(module._driver, RealDHT11Driver)
    finally:
        sys.modules.pop("Adafruit_DHT", None)
        sys.modules.pop("alarmclock.modules.dht11.real", None)
