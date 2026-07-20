"""Real button driver: reads an actual GPIO pin via RPi.GPIO. Only ever
imported when a module's `driver` setting is "real" - keeps RPi.GPIO (only
installable/importable on actual Raspberry Pi hardware) out of the import
path everywhere else.

Assumes the classic wiring: button between the pin and GND, internal
pull-up enabled, so the pin reads LOW while pressed and HIGH while idle -
no external resistor needed.
"""

from __future__ import annotations

import RPi.GPIO as GPIO


class RealButtonDriver:
    def __init__(self, pin: int) -> None:
        self.pin = pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    async def read(self) -> bool:
        return GPIO.input(self.pin) == GPIO.LOW
