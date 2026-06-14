"""The loud assertion for states that should never occur: record on the wide
event, log, raise. Use instead of a silent return / except-pass."""

import logging
from typing import Any, NoReturn

log = logging.getLogger("impossible")


class ImpossibleStateError(RuntimeError):
    def __init__(self, reason: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.details = details or {}


def _record(reason: str, details: dict[str, Any] | None, severity: str) -> None:
    log.error("[IMPOSSIBLE] %s %s", reason, details or {})
    from .wide_event import get_current_event

    event = get_current_event()
    if event is not None:
        event.set_error("IMPOSSIBLE_STATE", reason)
        event.set_business("impossible", {"reason": reason, "details": details or {}, "severity": severity})


def impossible(reason: str, details: dict[str, Any] | None = None) -> NoReturn:
    """Records, logs, raises."""
    _record(reason, details, "critical")
    raise ImpossibleStateError(reason, details)


def impossible_soft(reason: str, details: dict[str, Any] | None = None) -> None:
    """Records, logs, returns — the rare path that can't crash."""
    _record(reason, details, "warning")
