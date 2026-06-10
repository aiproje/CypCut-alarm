"""End-to-end test: MonitorService'i DRY_RUN modunda çalıştırır."""
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
from src.infrastructure.repositories import (
    AlarmRepository,
    CooldownRepository,
    StateRepository,
    TransitionRepository,
)
from src.logging_setup import setup_logging


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="cypcut_e2e_"))
    log_dir = tmpdir / "logs"
    log_dir.mkdir()
    db_path = tmpdir / "test.db"
    app_log = tmpdir / "app.log"

    fake_log = log_dir / "CypCut-20260605000000-9999-1.rtf"
    fake_log.write_text(
        r"{\rtf1\ansi\ansicpg936\deff0\f0 (06.05 08:00:00) Ready.\par}\par",
        encoding="utf-8",
    )

    config = AppConfig(
        machine_name="Lazer-1",
        log_dir=log_dir,
        tail_poll_interval=0.1,
        telegram_bot_token="FAKE_TOKEN",
        telegram_chat_id="-1001234567890",
        telegram_poll_interval=1.0,
        dry_run=True,
        alarm_cooldown_seconds=300,
        camera_index=None,
        camera_scan_max_index=4,
        video_duration=5.0,
        db_path=db_path,
        app_log_path=app_log,
        app_log_level="DEBUG",
        telegram_retry_check_interval=5.0,
        log_watcher_retry_interval=10.0,
        log_finder_scan_interval=5.0,
    )

    setup_logging(app_log, level="DEBUG")

    from src.services.monitor_service import MonitorLoop, MonitorService

    service = MonitorService(config)
    service._startup()

    loop = MonitorLoop(service, poll_interval=0.1)
    loop.start()

    print("\nTest: olay akışı yazılıyor...")
    sequence = [
        (0.5, r"(06.05 08:01:00) Start Processing\par}\par"),
        (1.0, r"(06.05 08:02:00) Alarm:BCS100 Follow Error\par}\par"),
        (1.0, r"(06.05 08:02:01) Alarm Remove:BCS100 Follow Error\par}\par"),
        (1.0, r"(06.05 08:03:00) Working --> Pause\par}\par"),
        (1.5, r"(06.05 08:05:00) Resume\par}\par"),
        (1.0, r"(06.05 08:06:00) Resume --> Working\par}\par"),
        (2.0, None),
    ]

    for delay, content in sequence:
        time.sleep(delay)
        if content is None:
            print("  -> Test sonu, shutdown...")
            break
        with fake_log.open("a", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
        print(f"  + Satır yazıldı: {content[:60]}...")

    time.sleep(1.0)

    service.request_stop()
    loop.stop()
    loop.join(timeout=2.0)
    service._shutdown()

    db = Database(db_path)
    state_repo = StateRepository(db)
    alarm_repo = AlarmRepository(db)
    trans_repo = TransitionRepository(db)
    cooldown_repo = CooldownRepository(db)

    state, last_evt, last_at = state_repo.load()
    print(f"\n=== Sonuç ===")
    print(f"State: {state.value}")
    print(f"Last event: {last_evt} @ {last_at}")
    print(f"Alarms: {len(alarm_repo.recent(10))}")
    print(f"Transitions: {len(trans_repo.recent(10))}")
    print(f"Cooldowns: {[(k, v) for k, v in [(k, cooldown_repo.get_last_sent(k)) for k in ['ALARM::BCS100 Follow Error']]]}")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\nE2E test tamam.")


if __name__ == "__main__":
    main()
