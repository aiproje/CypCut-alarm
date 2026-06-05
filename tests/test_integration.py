"""Entegrasyon testi: sahte bir log dosyası yazıp sistemin tepkisini izler."""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import AppConfig
from src.infrastructure.database import Database
from src.infrastructure.event_parser import EventParser
from src.infrastructure.log_finder import find_latest_log, is_cypcut_log
from src.infrastructure.log_tail_reader import LogTailReader
from src.infrastructure.repositories import (
    AlarmRepository,
    CooldownRepository,
    StateRepository,
    TransitionRepository,
)
from src.infrastructure.rtf_cleaner import clean as rtf_clean
from src.logging_setup import get_logger, setup_logging


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="cypcut_test_"))
    log_dir = tmpdir / "logs"
    log_dir.mkdir()
    db_path = tmpdir / "test.db"
    app_log = tmpdir / "app.log"

    setup_logging(app_log, level="DEBUG")
    log = get_logger("test")

    log.info("Test dizini: %s", tmpdir)

    db = Database(db_path)
    state_repo = StateRepository(db)
    alarm_repo = AlarmRepository(db)
    trans_repo = TransitionRepository(db)
    cooldown_repo = CooldownRepository(db)

    parser = EventParser()

    log_file = log_dir / "CypCut-20260605000000-1234-1.rtf"
    log_file.write_text(
        r"{\rtf1\ansi\ansicpg936\deff0\f0 (06.05 08:00:00) Ready.\par}"
        r"\par",
        encoding="utf-8",
    )

    log.info("Beklenen: en yeni dosya = %s", log_file)
    latest = find_latest_log(log_dir)
    assert latest == log_file, f"Beklenen {log_file}, bulunan {latest}"
    assert is_cypcut_log(log_file.name)

    received_lines: list[str] = []
    received_lock = threading.Lock()

    def on_line(line: str) -> None:
        with received_lock:
            received_lines.append(line)
        log.info("Yeni satır: %s", line)

    tail = LogTailReader(
        log_path=log_file, on_line=on_line, poll_interval=0.1
    )
    tail.start()

    time.sleep(0.5)

    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(
            r"{\rtf1\ansi\ansicpg936 (06.05 08:01:00) Start Processing\par}\par"
        )
        fh.flush()

    time.sleep(1.0)

    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(r"(06.05 08:02:00) Alarm:BCS100 Follow Error\par}\par")
        fh.flush()

    time.sleep(1.0)

    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(r"(06.05 08:03:00) Working --> Pause\par}\par")
        fh.flush()

    time.sleep(1.0)

    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(r"(06.05 08:05:00) Resume\par}\par")
        fh.flush()

    time.sleep(1.0)

    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(r"(06.05 08:06:00) Resume --> Working\par}\par")
        fh.flush()

    time.sleep(1.5)

    tail.stop()

    log.info("Alınan satırlar (%d):", len(received_lines))
    for line in received_lines:
        cleaned = rtf_clean(line)
        ev = parser.parse(cleaned)
        log.info("  %r -> %s", cleaned, ev.kind.value if ev else "NONE")

    state_repo.save(state=__import__("src.domain.enums", fromlist=["MachineState"]).MachineState.WORKING,
                    last_event_text="Resume --> Working",
                    last_event_at=__import__("datetime").datetime.now())
    alarm_repo.insert("BCS100 Follow Error", "raw", __import__("datetime").datetime.now(), True)
    cooldown_repo.set_last_sent("ALARM::BCS100 Follow Error", __import__("datetime").datetime.now())

    loaded_state, last_evt, last_at = state_repo.load()
    log.info("DB'den yüklenen: state=%s, last_event=%s", loaded_state.value, last_evt)

    shutil.rmtree(tmpdir, ignore_errors=True)
    log.info("Test tamam.")


if __name__ == "__main__":
    main()
