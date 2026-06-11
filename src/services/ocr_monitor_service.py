"""OCR tabanlı makine durumu izleme servisi.

Her N saniyede bir ekran görüntüsü alır, OCR ile alarm tablosunu okur,
durum değişikliklerini tespit eder ve Telegram'a bildirim gönderir.

Log dosyası okuma yerine ekran OCR'ı kullanır.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import AppConfig
from ..domain.enums import EventKind, MachineState
from ..domain.events import ParsedEvent
from ..domain.machine_state import MachineStateManager
from ..infrastructure.ocr_service import OcrService
from ..infrastructure.ocr_table_parser import OcrAlarmRow, format_table, parse_ocr_text
from ..infrastructure.repositories import (
    AlarmRepository,
    CooldownRepository,
    StateRepository,
    TransitionRepository,
)
from ..infrastructure.screen_capture import ScreenCapture
from ..infrastructure.telegram_client import TelegramClient
from ..logging_setup import get_logger

logger = get_logger(__name__)


class OcrMonitorService:
    """OCR ile ekran izleyerek makine durumunu takip eden servis.

    Akış:
      1. Ekran görüntüsü al
      2. Görüntüyü kırp (alarm tablosu alanı)
      3. OCR ile metin çıkar
      4. OCR çıktısını tablo satırlarına ayrıştır
      5. Önceki durumla karşılaştır, değişiklik varsa bildirim gönder
      6. Tüm OCR verilerini txt dosyasına logla
    """

    def __init__(
        self,
        config: AppConfig,
        screen_capture: ScreenCapture,
        ocr_service: OcrService,
        telegram: TelegramClient,
        state_manager: MachineStateManager,
        state_repo: StateRepository,
        alarm_repo: AlarmRepository,
        transition_repo: TransitionRepository,
        cooldown_repo: CooldownRepository,
    ) -> None:
        self._config = config
        self._screen = screen_capture
        self._ocr = ocr_service
        self._telegram = telegram
        self._state_manager = state_manager
        self._state_repo = state_repo
        self._alarm_repo = alarm_repo
        self._transition_repo = transition_repo
        self._cooldown_repo = cooldown_repo

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Önceki tarama sonucu (değişiklik tespiti için)
        self._last_alarm_keys: set[str] = set()
        self._last_status: Optional[MachineState] = None

        # OCR log dosyası
        self._ocr_log_path = config.ocr_log_path
        self._ensure_ocr_log_dir()

    def _ensure_ocr_log_dir(self) -> None:
        """OCR log dizinini oluşturur."""
        self._ocr_log_path.parent.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        """OCR izleme döngüsünü başlatır."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="OcrMonitor",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "OCR izleme başlatıldı (aralık: %.1fs, log: %s)",
            self._config.ocr_monitor_interval,
            self._ocr_log_path,
        )

    def stop(self) -> None:
        """OCR izleme döngüsünü durdurur."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        logger.info("OCR izleme durduruldu.")

    def _monitor_loop(self) -> None:
        """Ana izleme döngüsü: periyodik olarak OCR taraması yapar."""
        logger.info("OCR izleme döngüsü başladı.")
        while not self._stop_event.is_set():
            try:
                self._scan_once()
            except Exception as exc:
                logger.exception("OCR tarama hatası: %s", exc)
            self._stop_event.wait(self._config.ocr_monitor_interval)
        logger.info("OCR izleme döngüsü durduruldu.")

    def _scan_once(self) -> None:
        """Tek bir tarama: ekran görüntüsü al, OCR yap, değerlendir."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1) Ekran görüntüsü al
        if not self._screen.is_available:
            logger.warning("Ekran görüntüsü alınamıyor, atlanıyor.")
            return

        screenshot_path = self._screen.capture()
        if screenshot_path is None:
            logger.warning("Ekran görüntüsü alınamadı.")
            return

        # 2) OCR yap (kırp + tanıma)
        try:
            ocr_text = self._ocr.recognize(screenshot_path)
        except Exception as exc:
            logger.exception("OCR hatası: %s", exc)
            ocr_text = None
        finally:
            # Geçici ekran görüntüsünü temizle
            try:
                screenshot_path.unlink(missing_ok=True)
            except OSError:
                pass

        if ocr_text is None:
            logger.warning("OCR sonucu alınamadı.")
            return

        # 3) OCR verilerini logla (ham metin)
        self._log_ocr_data(ts, ocr_text)

        # 4) OCR çıktısını ayrıştır
        rows = parse_ocr_text(ocr_text)
        if not rows:
            logger.info("OCR tabloda veri bulunamadı.")
            return

        logger.info("OCR tablosu:\n%s", format_table(rows))

        # 5) Durum değişikliğini değerlendir
        self._evaluate_state(rows, ts)

    def _evaluate_state(self, rows: list[OcrAlarmRow], ts: str) -> None:
        """OCR satırlarına göre durum değişikliğini değerlendirir."""
        current_alarm_keys: set[str] = set()
        active_alarms: list[OcrAlarmRow] = []

        for row in rows:
            if row.is_alarm_active:
                key = self._make_alarm_key(row)
                current_alarm_keys.add(key)
                active_alarms.append(row)

        # Yeni alarm var mı?
        new_alarms = current_alarm_keys - self._last_alarm_keys
        cleared_alarms = self._last_alarm_keys - current_alarm_keys

        # Durum belirleme
        if active_alarms:
            new_state = MachineState.ALARM
        elif rows:
            # Tabloda veri var ama alarm yoksa çalışıyor demektir
            new_state = MachineState.WORKING
        else:
            new_state = MachineState.IDLE

        previous_state = self._last_status or self._state_manager.state

        # Yeni alarm geldi mi?
        for alarm_row in active_alarms:
            key = self._make_alarm_key(alarm_row)
            if key in new_alarms:
                self._on_new_alarm(alarm_row, ts)

        # Alarm temizlendi mi?
        if cleared_alarms and not active_alarms:
            self._on_all_alarms_cleared(ts)

        # Durum değişikliği var mı?
        if new_state != previous_state:
            self._on_state_change(previous_state, new_state, rows, ts)

        # Durumu güncelle
        self._last_alarm_keys = current_alarm_keys
        self._last_status = new_state

    def _make_alarm_key(self, row: OcrAlarmRow) -> str:
        """Alarm satırından benzersiz anahtar üretir."""
        parts = [row.alarm_info or "", row.alarm_id or "", row.status or ""]
        return "::".join(parts)

    def _on_new_alarm(self, alarm_row: OcrAlarmRow, ts: str) -> None:
        """Yeni bir alarm tespit edildiğinde çağrılır."""
        alarm_text = (
            f"{alarm_row.alarm_info or 'Bilinmeyen alarm'} "
            f"(ID: {alarm_row.alarm_id or '?'}, Durum: {alarm_row.status or '?'})"
        )

        key = f"OCR_ALARM::{self._make_alarm_key(alarm_row)}"
        now = datetime.now()

        if not self._cooldown_ok(key, now):
            logger.info("OCR alarm cooldown aktif, atlandı: %s", alarm_text)
            return

        message = self._format_ocr_alarm_message(alarm_row, ts)
        sent = self._telegram.send_message(message)

        self._cooldown_repo.set_last_sent(key, now)
        self._alarm_repo.insert(
            alarm_text=alarm_text,
            raw_line=f"OCR:{alarm_row.to_dict()}",
            occurred_at=now,
            telegram_sent=sent,
        )

        if sent:
            logger.info("OCR alarm bildirimi gönderildi: %s", alarm_text)
        else:
            logger.warning("OCR alarm bildirimi gönderilemedi: %s", alarm_text)

    def _on_all_alarms_cleared(self, ts: str) -> None:
        """Tüm alarm satırları tablodan silindiğinde çağrılır."""
        message = (
            "✅ Alarm Temizlendi (OCR)\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts}\n"
            "\n"
            "Tabloda aktif alarm kalmadı."
        )
        self._telegram.send_message(message)
        logger.info("OCR alarm temizleme bildirimi gönderildi.")

    def _on_state_change(
        self,
        previous: MachineState,
        current: MachineState,
        rows: list[OcrAlarmRow],
        ts: str,
    ) -> None:
        """Durum değişikliğinde çağrılır."""
        # Pseudo-event oluştur (mevcut sisteme entegre etmek için)
        if current == MachineState.ALARM:
            event = ParsedEvent(
                kind=EventKind.ALARM,
                timestamp=datetime.now(),
                text="OCR: Alarm durumu tespit edildi",
                raw_line=f"OCR:{[r.to_dict() for r in rows]}",
            )
        elif current == MachineState.WORKING:
            if previous == MachineState.ALARM:
                event = ParsedEvent(
                    kind=EventKind.RESUME,
                    timestamp=datetime.now(),
                    text="OCR: Çalışmaya devam ediliyor",
                    raw_line=f"OCR:{[r.to_dict() for r in rows]}",
                )
            else:
                event = ParsedEvent(
                    kind=EventKind.START,
                    timestamp=datetime.now(),
                    text="OCR: Çalışma başladı",
                    raw_line=f"OCR:{[r.to_dict() for r in rows]}",
                )
        elif current == MachineState.PAUSED:
            event = ParsedEvent(
                kind=EventKind.STOP,
                timestamp=datetime.now(),
                text="OCR: Makine durdu",
                raw_line=f"OCR:{[r.to_dict() for r in rows]}",
            )
        else:
            return

        result = self._state_manager.process(event)
        if not result.changed:
            return

        # Bildirim gönder
        if current == MachineState.ALARM:
            message = self._format_state_change_message("🚨 Alarm Durumu", ts, rows)
        elif current == MachineState.WORKING:
            message = self._format_state_change_message("▶️ Çalışıyor", ts, rows)
        elif current == MachineState.PAUSED:
            message = self._format_state_change_message("⏸️ Durdu", ts, rows)
        else:
            return

        sent = self._telegram.send_message(message)
        self._state_repo.save(
            state=current,
            last_event_text=event.text,
            last_event_at=datetime.now(),
        )
        self._transition_repo.insert(
            from_state=previous,
            to_state=current,
            reason=event.text,
            occurred_at=datetime.now(),
            telegram_sent=sent,
        )
        logger.info("OCR durum değişikliği: %s -> %s", previous.value, current.value)

    def _format_ocr_alarm_message(self, alarm_row: OcrAlarmRow, ts: str) -> str:
        """OCR alarm mesajı formatlar."""
        return (
            "🚨 Lazer Alarmı (OCR)\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts}\n"
            "\n"
            f"Alarm: {alarm_row.alarm_info or 'Bilinmeyen'}\n"
            f"ID: {alarm_row.alarm_id or '?'}\n"
            f"Durum: {alarm_row.status or '?'}\n"
            f"Zaman: {alarm_row.timestamp or '?'}"
        )

    def _format_state_change_message(
        self, title: str, ts: str, rows: list[OcrAlarmRow]
    ) -> str:
        """Durum değişikliği mesajı formatlar."""
        alarm_lines = []
        for row in rows:
            if row.is_alarm_active:
                alarm_lines.append(
                    f"  • {row.alarm_info or '?'} (ID: {row.alarm_id or '?'})"
                )
        alarm_block = "\n".join(alarm_lines) if alarm_lines else "  Yok"

        return (
            f"{title} (OCR)\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts}\n"
            "\n"
            f"Aktif Alarmlar:\n"
            f"{alarm_block}"
        )

    def _cooldown_ok(self, key: str, now: datetime) -> bool:
        """Cooldown süresi dolmuş mu kontrol eder."""
        last = self._cooldown_repo.get_last_sent(key)
        if last is None:
            return True
        return (now - last).total_seconds() >= self._config.alarm_cooldown_seconds

    def _log_ocr_data(self, ts: str, raw_text: str) -> None:
        """OCR ham verisini txt dosyasına loglar."""
        try:
            with open(self._ocr_log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Tarih: {ts}\n")
                f.write(f"{'='*60}\n")
                f.write(raw_text)
                f.write("\n")

                # Ayrıştırılmış satırları da ekle
                rows = parse_ocr_text(raw_text)
                if rows:
                    f.write(f"\n--- Ayrıştırılmış Tablo ---\n")
                    f.write(format_table(rows))
                    f.write("\n")

                # Durum bilgisi
                f.write(f"\n--- Durum ---\n")
                f.write(f"Mevcut durum: {self._last_status or 'Bilinmiyor'}\n")
                f.write(f"Aktif alarmlar: {len(self._last_alarm_keys)}\n")
                f.write("\n")

            logger.debug("OCR verisi loglandı: %s", self._ocr_log_path)
        except Exception as exc:
            logger.warning("OCR log yazma hatası: %s", exc)


class OcrMonitorLoop(threading.Thread):
    """OcrMonitorService'in çalışmasını ayrı bir thread'de yönetir."""

    def __init__(self, service: OcrMonitorService) -> None:
        super().__init__(name="OcrMonitorLoop", daemon=True)
        self._service = service

    def stop(self) -> None:
        self._service.stop()

    def run(self) -> None:
        self._service.start()
        # Service kendi thread'ini yönetir, burada sadece block ederiz
        while self._service._thread is not None and self._service._thread.is_alive():
            time.sleep(1.0)


__all__ = ["OcrMonitorService", "OcrMonitorLoop"]
