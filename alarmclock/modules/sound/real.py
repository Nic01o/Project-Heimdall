"""Real speaker driver: drives an actual GPIO pin via RPi.GPIO. Only ever
imported when a module's `driver` setting is "real" - keeps RPi.GPIO (only
installable/importable on actual Raspberry Pi hardware) out of the import
path everywhere else.
"""

from __future__ import annotations

import RPi.GPIO as GPIO


class RealSpeakerDriver:
    def __init__(self, pin: int) -> None:
        self.pin = pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.OUT)

    async def write(self, on: bool) -> None:
        GPIO.output(self.pin, GPIO.HIGH if on else GPIO.LOW)
