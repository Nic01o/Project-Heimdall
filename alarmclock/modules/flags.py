"""Global registry of every "flag" any module in the program could emit -
button gestures, alarm lifecycle events, and (in the future) anything else
an input module wants to put on the bus.

An input module (e.g. button) decides which of its own gestures to actually
put on the bus via a per-flag `send_<flag>` toggle setting; an output module
(e.g. led) decides, per flag, whether and how it reacts via a `reaction_<flag>`
select setting (see `alarmclock.modules.base.OutputModule`). Both sides are
rendered from the same `FLAG_SOURCES` registry, which is how the two get
"linked" in the UI - through a shared vocabulary of strings, never a direct
reference to one another (modules still only ever talk through the bus).

Some flags are multiplexed onto a single bus event and discriminated by a
payload key (all button gestures arrive as `button.flag` with a `flag`
payload key); others are each their own bare event name (the alarm lifecycle
events). `FlagSource` captures either shape so `OutputModule` can subscribe
and dispatch generically regardless of which shape a given flag uses.

If/when a button module is reintroduced, it should build its `send_<flag>`
settings by filtering this registry for `event == "button.flag"`, rather
than reviving its own local flag list.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlagSource:
    """Where a flag comes from on the bus.

    `payload_flag` is the payload key that carries the flag name when
    several flags share one event (e.g. `button.flag`'s `flag` key); leave
    it `None` when the event name itself is the flag (e.g. `alarm.triggered`).

    `suppress_while_blinking` controls whether this flag's reaction is
    skipped while an output module has a continuous "blink" reaction running
    (True for ordinary flags, so they can't fight an alarm blink pattern).
    Alarm lifecycle flags set this False since they're precisely what starts/
    stops that blink pattern and must always take effect.
    """

    event: str
    payload_flag: str | None = None
    suppress_while_blinking: bool = True


FLAG_SOURCES: dict[str, FlagSource] = {

    "alarm_triggered": FlagSource("alarm.triggered", suppress_while_blinking=False),
    "alarm_stopped": FlagSource("alarm.stopped", suppress_while_blinking=False),
    "alarm_snoozed": FlagSource("alarm.snoozed", suppress_while_blinking=False),

    "press": FlagSource("button.flag", "flag"),
    "release": FlagSource("button.flag", "flag"),
    "click": FlagSource("button.flag", "flag"),
    "double_click": FlagSource("button.flag", "flag"),
    "multi_click": FlagSource("button.flag", "flag"),
    "long_press": FlagSource("button.flag", "flag"),


}

# Kept for callers that just want "every flag button-style modules can send".
BUTTON_FLAGS: list[str] = [name for name, source in FLAG_SOURCES.items() if source.event == "button.flag"]


def resolve_flag(event: str, payload: dict) -> str | None:
    """Given a received bus event and payload, return which registered flag
    (if any) it corresponds to."""
    for name, source in FLAG_SOURCES.items():
        if source.event != event:
            continue
        if source.payload_flag is None:
            return name
        if payload.get(source.payload_flag) == name:
            return name
    return None
