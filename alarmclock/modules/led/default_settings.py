"""Default settings for the LED module."""

# Out-of-the-box behavior. Default ignore
_DEFAULT_REACTIONS = {
    "press":    "ignore",
    "release":  "ignore",
    "click":    "ignore",
    "double_click": "ignore",
    "multi_click": "ignore",
    "long_press": "ignore",
}

# Default pin for LED module
DEFAULT_PIN = 22

# Default blink interval in seconds
DEFAULT_BLINK_INTERVAL_SECONDS = 0.3

# Default flash duration in seconds
DEFAULT_FLASH_SECONDS = 0.15

# Available reaction types
REACTIONS = ["ignore", "on", "off", "toggle", "flash_1", "flash_2", "flash_3", "flash_4"]

# How many times to flash for each flash_N reaction.
_FLASH_TIMES = {"flash_1": 1, "flash_2": 2, "flash_3": 3, "flash_4": 4}