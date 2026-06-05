"""Thread-safe makine durum yöneticisi.

Event'lere göre geçiş kuralları:
  START   -> WORKING
  STOP    -> PAUSED
  RESUME  -> WORKING
  ALARM   -> ALARM (WORKING üstünden)
  ALARM_CLEAR -> önceki state (PAUSED / WORKING)

IDLE durumundan ALARM'a geçiş kabul edilir (alarm önce IDLE iken de olabilir).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .enums import EventKind, MachineState
from .events import ParsedEvent


@dataclass(frozen=True)
class TransitionResult:
    """State machine'in bir event sonrası ürettiği sonuç."""

    previous: MachineState
    current: MachineState
    event: ParsedEvent
    changed: bool


class MachineStateManager:
    """Makinenin anlık durumunu tutar ve event'lere göre günceller."""

    def __init__(self, initial: MachineState = MachineState.IDLE) -> None:
        self._state: MachineState = initial
        self._last_event: Optional[ParsedEvent] = None
        self._last_event_at: Optional[datetime] = None
        self._last_alarm_text: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> MachineState:
        with self._lock:
            return self._state

    @property
    def last_event(self) -> Optional[ParsedEvent]:
        with self._lock:
            return self._last_event

    @property
    def last_event_at(self) -> Optional[datetime]:
        with self._lock:
            return self._last_event_at

    @property
    def last_alarm_text(self) -> Optional[str]:
        with self._lock:
            return self._last_alarm_text

    def restore(
        self,
        state: MachineState,
        last_event: Optional[ParsedEvent] = None,
        last_alarm_text: Optional[str] = None,
    ) -> None:
        """Servis yeniden başlatıldığında kalıcı durumu yükler."""
        with self._lock:
            self._state = state
            self._last_event = last_event
            self._last_alarm_text = last_alarm_text
            if last_event is not None:
                self._last_event_at = last_event.timestamp

    def process(self, event: ParsedEvent) -> TransitionResult:
        """Bir event'i değerlendirir, durumu günceller, sonucu döner."""
        with self._lock:
            previous = self._state
            current = self._apply(previous, event)
            self._state = current
            self._last_event = event
            self._last_event_at = event.timestamp
            if event.is_alarm:
                self._last_alarm_text = event.text
            elif event.is_alarm_clear:
                self._last_alarm_text = None
            return TransitionResult(
                previous=previous,
                current=current,
                event=event,
                changed=previous != current,
            )

    def _apply(self, previous: MachineState, event: ParsedEvent) -> MachineState:
        kind = event.kind
        if kind == EventKind.START:
            return MachineState.WORKING
        if kind == EventKind.STOP:
            return MachineState.PAUSED
        if kind == EventKind.RESUME:
            if previous == MachineState.ALARM:
                return MachineState.WORKING
            return MachineState.WORKING
        if kind == EventKind.ALARM:
            return MachineState.ALARM
        if kind == EventKind.ALARM_CLEAR:
            if previous == MachineState.ALARM:
                return MachineState.PAUSED
            return previous
        return previous


__all__ = ["MachineStateManager", "TransitionResult"]
