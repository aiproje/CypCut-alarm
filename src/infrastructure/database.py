"""SQLite veritabanı bağlantı yönetimi."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import PROJECT_ROOT

_SCHEMA_PATH: Path = PROJECT_ROOT / "schema.sql"


class Database:
    """Thread-safe SQLite veritabanı sarmalayıcı.

    - ``check_same_thread=False`` + ``Lock`` ile birden çok thread'den erişim.
    - Context manager ile kısa süreli connection yönetimi.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self.connect() as conn:
            conn.executescript(schema_sql)
            conn.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yeni bir bağlantı açar, iş bitince kapatır."""
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Transaction'lı bağlantı (başarıda commit, hatada rollback)."""
        with self._lock, self.connect() as conn:
            try:
                conn.execute("BEGIN")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise


__all__ = ["Database"]
