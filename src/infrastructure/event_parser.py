"""Log satırlarını ParsedEvent'lere dönüştüren parser.

Öncelik sırası (en spesifikten):
  1. Alarm Remove:        -> ALARM_CLEAR
  2. Alarm:               -> ALARM
  3. Working --> Pause    -> STOP
  4. Pause --> Resume     -> RESUME
  5. Resume --> Working   -> START
  6. Stop --> Working     -> START
  7. Start Processing     -> START
  8. Resume               -> RESUME
  9. Pause                -> STOP

Diğer tüm satırlar IGNORED.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from ..domain.enums import EventKind
from ..domain.events import ParsedEvent


_TIMESTAMP_PATTERN = re.compile(
    r"^\((\d{2})\.(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\)\s*(.*)$"
)

_ALARM_CLEAR = re.compile(r"Alarm\s*Remove\s*:\s*(.+?)\s*$", re.IGNORECASE)
_ALARM = re.compile(r"Alarm\s*:\s*(.+?)\s*$", re.IGNORECASE)

_TRANSITION_WORKING_PAUSE = re.compile(r"Working\s*-->\s*Pause", re.IGNORECASE)
_TRANSITION_PAUSE_RESUME = re.compile(r"Pause\s*-->\s*Resume", re.IGNORECASE)
_TRANSITION_RESUME_WORKING = re.compile(r"Resume\s*-->\s*Working", re.IGNORECASE)
_TRANSITION_STOP_WORKING = re.compile(r"Stop\s*-->\s*Working", re.IGNORECASE)
_START_PROCESSING = re.compile(r"Start\s*Processing", re.IGNORECASE)
_BODY_ONLY_RESUME = re.compile(r"Resume", re.IGNORECASE)
_BODY_ONLY_PAUSE = re.compile(r"Pause", re.IGNORECASE)


class EventParser:
    """RTF-temizlenmiş satırları event'lere ayrıştırır."""

    def __init__(self, reference_year: Optional[int] = None) -> None:
        self._reference_year = reference_year or datetime.now().year

    def parse(self, line: str) -> Optional[ParsedEvent]:
        """Bir satırı parse eder. Event değilse None döner."""
        if not line or not line.strip():
            return None

        timestamp = self._extract_timestamp(line)
        body = line

        alarm_clear = _ALARM_CLEAR.search(body)
        if alarm_clear:
            return ParsedEvent(
                kind=EventKind.ALARM_CLEAR,
                timestamp=timestamp,
                text=alarm_clear.group(1).strip(),
                raw_line=line,
            )

        alarm = _ALARM.search(body)
        if alarm:
            return ParsedEvent(
                kind=EventKind.ALARM,
                timestamp=timestamp,
                text=alarm.group(1).strip(),
                raw_line=line,
            )

        if _TRANSITION_RESUME_WORKING.search(body):
            return ParsedEvent(
                kind=EventKind.START,
                timestamp=timestamp,
                text="Resume --> Working",
                raw_line=line,
            )

        if _TRANSITION_STOP_WORKING.search(body):
            return ParsedEvent(
                kind=EventKind.START,
                timestamp=timestamp,
                text="Stop --> Working",
                raw_line=line,
            )

        if _START_PROCESSING.search(body):
            return ParsedEvent(
                kind=EventKind.START,
                timestamp=timestamp,
                text="Start Processing",
                raw_line=line,
            )

        if _TRANSITION_WORKING_PAUSE.search(body):
            return ParsedEvent(
                kind=EventKind.STOP,
                timestamp=timestamp,
                text="Working --> Pause",
                raw_line=line,
            )

        if _TRANSITION_PAUSE_RESUME.search(body):
            return ParsedEvent(
                kind=EventKind.RESUME,
                timestamp=timestamp,
                text="Pause --> Resume",
                raw_line=line,
            )

        ts_match = _TIMESTAMP_PATTERN.match(line.strip())
        body_only = ts_match.group(6).strip() if ts_match else line.strip()

        if body_only == "Resume":
            return ParsedEvent(
                kind=EventKind.RESUME,
                timestamp=timestamp,
                text="Resume",
                raw_line=line,
            )

        if body_only == "Pause":
            return ParsedEvent(
                kind=EventKind.STOP,
                timestamp=timestamp,
                text="Pause",
                raw_line=line,
            )

        return None

    def _extract_timestamp(self, line: str) -> Optional[datetime]:
        m = _TIMESTAMP_PATTERN.match(line.strip())
        if not m:
            return None
        month, day, hh, mm, ss = (int(g) for g in m.groups()[:5])
        try:
            return datetime(self._reference_year, month, day, hh, mm, ss)
        except ValueError:
            return None


__all__ = ["EventParser"]
