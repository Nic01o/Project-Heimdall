"""Shared vocabulary of discrete "flags" that input modules can emit and
output modules can react to.

An input module (e.g. button) decides which of its own gestures to actually
put on the bus via a `flags` multiselect setting; an output module (e.g.
led) decides which of those flags it reacts to via a `reacts_to` multiselect
setting. Both settings are rendered from the same option list, which is how
the two get "linked" in the UI - through a shared vocabulary of strings,
never a direct reference to one another (modules still only ever talk
through the bus).
"""

from __future__ import annotations

BUTTON_FLAGS: list[str] = [
    "press",
    "release",
    "click",
    "double_click",
    "multi_click",
    "long_press",
]
