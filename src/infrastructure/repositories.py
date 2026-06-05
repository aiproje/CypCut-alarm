"""Repository katmanı: domain nesnelerini veritabanına yazar/okur."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable, Optional

from ..domain.enums import MachineState
from .database import Database


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


class StateRepository:
    """Makinenin kalıcı durumunu yönetir."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(
        self,
        state: MachineState,
        last_event_text: Optional[str],
        last_event_at: Optional[datetime],
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO state (id, current_state, last_event, last_event_at, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    current_state = excluded.current_state,
                    last_event    = excluded.last_event,
                    last_event_at = excluded.last_event_at,
                    updated_at    = excluded.updated_at
                """,
                (
                    state.value,
                    last_event_text,
                    _iso(last_event_at),
                    _iso(datetime.now()),
                ),
            )

    def load(self) -> tuple[MachineState, Optional[str], Optional[datetime]]:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT current_state, last_event, last_event_at FROM state WHERE id=1"
            ).fetchone()
        if row is None:
            return MachineState.IDLE, None, None
        return (
            MachineState.from_value(row["current_state"]),
            row["last_event"],
            _parse_dt(row["last_event_at"]),
        )


class AlarmRepository:
    """Alarm olaylarını saklar."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(
        self,
        alarm_text: str,
        raw_line: str,
        occurred_at: datetime,
        telegram_sent: bool,
    ) -> int:
        with self._db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO alarm_events
                    (alarm_text, raw_line, occurred_at, telegram_sent)
                VALUES (?, ?, ?, ?)
                """,
                (alarm_text, raw_line, _iso(occurred_at), 1 if telegram_sent else 0),
            )
            return int(cur.lastrowid)

    def recent(self, limit: int = 10) -> list[dict]:
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, alarm_text, raw_line, occurred_at, telegram_sent
                FROM alarm_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


class TransitionRepository:
    """Durum geçişlerini saklar."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(
        self,
        from_state: Optional[MachineState],
        to_state: MachineState,
        reason: Optional[str],
        occurred_at: datetime,
        telegram_sent: bool,
    ) -> int:
        with self._db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO state_transitions
                    (from_state, to_state, reason, occurred_at, telegram_sent)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    from_state.value if from_state else None,
                    to_state.value,
                    reason,
                    _iso(occurred_at),
                    1 if telegram_sent else 0,
                ),
            )
            return int(cur.lastrowid)

    def recent(self, limit: int = 10) -> list[dict]:
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, from_state, to_state, reason, occurred_at, telegram_sent
                FROM state_transitions
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


class CooldownRepository:
    """Alarm cooldown sürelerini saklar.

    Bir alarm anahtarı (alarm_text) için son gönderim zamanı tutulur.
    Aynı anahtar 5 dakika (veya config'deki süre) içinde tekrar gönderilmez.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_last_sent(self, key: str) -> Optional[datetime]:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT last_sent_at FROM cooldowns WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return _parse_dt(row["last_sent_at"])

    def set_last_sent(self, key: str, when: datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO cooldowns (key, last_sent_at)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    last_sent_at = excluded.last_sent_at
                """,
                (key, _iso(when)),
            )

    def clear(self) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM cooldowns")


__all__ = [
    "StateRepository",
    "AlarmRepository",
    "TransitionRepository",
    "CooldownRepository",
]
