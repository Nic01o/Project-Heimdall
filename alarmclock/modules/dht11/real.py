"""Real DHT11 driver: reads an actual DHT11 sensor via Adafruit_DHT. Only
ever imported when a module's `driver` setting is "real" - keeps
Adafruit_DHT (only installable/importable on actual Raspberry Pi hardware)
out of the import path everywhere else.

Adafruit_DHT.read_retry() is a blocking call that bit-bangs the sensor's
single-wire protocol and retries on the checksum failures that protocol is
prone to - it runs in a worker thread so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio

import Adafruit_DHT


class RealDHT11Driver:
    def __init__(self, pin: int) -> None:
        self.pin = pin

    async def read(self) -> tuple[float, float] | None:
        humidity, temperature = await asyncio.to_thread(
            Adafruit_DHT.read_retry, Adafruit_DHT.DHT11, self.pin
        )
        if humidity is None or temperature is None:
            return None
        return temperature, humidity
