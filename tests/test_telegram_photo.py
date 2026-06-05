"""Telegram bağlantısı + FOTO komutunu simüle eden test scripti.

Akış:
  1. .env'den konfigürasyonu yükle
  2. Kamerayı başlat (varsa JPEG çek, yoksa None)
  3. FOTO komutunun ürettiği mesajı Telegram'a gönder
  4. DURUM komutunun ürettiği mesajı Telegram'a gönder
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import AppConfig
from src.infrastructure.camera_manager import CameraManager
from src.infrastructure.database import Database
from src.infrastructure.photo_service import PhotoService
from src.infrastructure.repositories import (
    AlarmRepository,
    CooldownRepository,
    StateRepository,
    TransitionRepository,
)
from src.infrastructure.telegram_client import TelegramClient
from src.logging_setup import get_logger, setup_logging


def main() -> int:
    config = AppConfig.load()
    setup_logging(config.app_log_path, level="INFO")
    log = get_logger("telegram_test")

    errors = config.validate()
    if errors:
        for err in errors:
            log.error("Konfigürasyon hatası: %s", err)
        return 2

    log.info("Telegram bağlantısı test ediliyor (chat_id=%s)", config.telegram_chat_id)

    camera = CameraManager(
        preferred_index=config.camera_index,
        max_index=config.camera_scan_max_index,
    )
    camera.initialize()
    photo = PhotoService(camera)
    db = Database(config.db_path)
    state_repo = StateRepository(db)
    alarm_repo = AlarmRepository(db)
    trans_repo = TransitionRepository(db)
    cooldown_repo = CooldownRepository(db)

    state, last_evt, last_at = state_repo.load()
    cam_status = (
        f"✅ Bağlı (Index: {camera.active_index})"
        if camera.is_available
        else "❌ Bağlı Değil"
    )
    last_text = last_evt or "-"
    last_at_text = last_at.strftime("%Y-%m-%d %H:%M:%S") if last_at else "-"

    recent_alarms = alarm_repo.recent(limit=5)
    alarm_lines = []
    for a in recent_alarms:
        alarm_lines.append(
            f"  - {a['occurred_at']} | {a['alarm_text'][:80]}"
        )
    alarms_block = "\n".join(alarm_lines) if alarm_lines else "  (yok)"

    status_text = (
        f"📊 Makine Durumu\n"
        f"\n"
        f"Makine: {config.machine_name}\n"
        f"Durum: {state.value}\n"
        f"Saat: {datetime.now().strftime('%H:%M:%S')}\n"
        f"Son olay: {last_text} @ {last_at_text}\n"
        f"\n"
        f"Kamera:\n"
        f"{cam_status}\n"
        f"\n"
        f"Son 5 Alarm:\n"
        f"{alarms_block}"
    )

    telegram = TelegramClient(
        token=config.telegram_bot_token,
        chat_id=config.telegram_chat_id,
        poll_interval=config.telegram_poll_interval,
        dry_run=config.dry_run,
    )

    boot_text = (
        f"🤖 CypCut Monitor Test Mesajı\n"
        f"\n"
        f"Makine: {config.machine_name}\n"
        f"Saat: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"DRY_RUN: {config.dry_run}\n"
        f"\n"
        f"Kamera: {cam_status}"
    )
    log.info("Boot mesajı gönderiliyor...")
    if telegram.send_message(boot_text):
        log.info("✓ Boot mesajı gönderildi")
    else:
        log.error("✗ Boot mesajı gönderilemedi")
        return 3

    log.info("FOTO komutu simüle ediliyor...")
    photo_path = photo.capture_jpeg()
    foto_caption = (
        f"📷 Anlık Kamera Görüntüsü\n"
        f"\n"
        f"Makine:\n{config.machine_name}\n"
        f"Saat:\n{datetime.now().strftime('%H:%M:%S')}"
    )
    if photo_path is not None:
        log.info("Fotoğraf çekildi: %s", photo_path)
        if telegram.send_photo(photo_path, caption=foto_caption):
            log.info("✓ Fotoğraf Telegram'a gönderildi")
        else:
            log.error("✗ Fotoğraf gönderilemedi")
        try:
            photo_path.unlink(missing_ok=True)
        except OSError:
            pass
    else:
        log.warning("Kamera bağlı değil, sadece metin gönderilecek")
        fallback = foto_caption + "\n\n⚠️ Kamera bağlı değil veya görüntü alınamadı."
        if telegram.send_message(fallback):
            log.info("✓ Metin fallback gönderildi")
        else:
            log.error("✗ Metin fallback gönderilemedi")

    log.info("DURUM komutu simüle ediliyor...")
    if telegram.send_message(status_text):
        log.info("✓ DURUM mesajı gönderildi")
    else:
        log.error("✗ DURUM mesajı gönderilemedi")

    camera.close()
    log.info("Test tamamlandı.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
