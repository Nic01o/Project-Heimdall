"""Shared vocabulary of discrete "flags" that input modules can emit and
output modules can react to.

An input module (e.g. button) decides which of its own gestures to actually
put on the bus via a `flags` multiselect setting; an output module (e.g.
led) decides, per flag, whether and how it reacts via a `reaction_<flag>`
select setting (see led.py's `REACTIONS`). Both sides are rendered from the
same option list, which is how the two get "linked" in the UI - through a
shared vocabulary of strings, never a direct reference to one another
(modules still only ever talk through the bus).
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
