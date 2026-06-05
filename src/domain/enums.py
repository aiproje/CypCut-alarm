"""Domain enum'ları."""
from __future__ import annotations

from enum import Enum


class MachineState(str, Enum):
    """Makinenin olası çalışma durumları."""

    IDLE = "IDLE"
    WORKING = "WORKING"
    PAUSED = "PAUSED"
    ALARM = "ALARM"

    @classmethod
    def from_value(cls, value: str | None) -> "MachineState":
        if not value:
            return cls.IDLE
        try:
            return cls(value.upper())
        except ValueError:
            return cls.IDLE


class EventKind(str, Enum):
    """Parser'dan dönen event türleri."""

    START = "START"
    STOP = "STOP"
    RESUME = "RESUME"
    ALARM = "ALARM"
    ALARM_CLEAR = "ALARM_CLEAR"
    IGNORED = "IGNORED"


__all__ = ["MachineState", "EventKind"]
