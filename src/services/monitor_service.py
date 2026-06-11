"""Ana orkestratör servisi.

Akış:
  1. Config yükle
  2. DB initialize
  3. Son durumu yükle
  4. Camera initialize
  5. Telegram client kur
  6. Aktif log dosyasını bul
  7. Watchdog + tail reader + log finder başlat
  8. Telegram polling başlat
  9. Ana döngü: thread'lerin yaşamasını izle

Geliştirilmiş:
  - Tüm durum geçişlerinde bildirim (IDLE→WORKING dahil)
  - Durum metninde "kaç dakikadır çalışıyor" bilgisi
  - Video desteği
  - LogFinder ile periyodik tarama
"""
from __future__ import annotations

import signal
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import AppConfig
from ..domain.enums import EventKind, MachineState
from ..domain.events import ParsedEvent
from ..domain.machine_state import MachineStateManager
from ..infrastructure.camera_manager import CameraManager
from ..infrastructure.database import Database
from ..infrastructure.event_parser import EventParser
from ..infrastructure.log_directory_watcher import LogDirectoryWatcher
from ..infrastructure.log_finder import LogFinder, find_latest_log
from ..infrastructure.log_tail_reader import LogTailReader
from ..infrastructure.ocr_service import OcrService
from ..infrastructure.photo_service import MediaService
from ..infrastructure.screen_capture import ScreenCapture
from ..infrastructure.repositories import (
    AlarmRepository,
    CooldownRepository,
    StateRepository,
    TransitionRepository,
)
from ..infrastructure.rtf_cleaner import clean as rtf_clean
from ..infrastructure.telegram_client import TelegramClient
from ..logging_setup import get_logger

logger = get_logger(__name__)


class MonitorService:
    """Tüm bileşenleri koordine eden ana servis."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._stop_event = threading.Event()

        self._db = Database(config.db_path)
        self._state_repo = StateRepository(self._db)
        self._alarm_repo = AlarmRepository(self._db)
        self._transition_repo = TransitionRepository(self._db)
        self._cooldown_repo = CooldownRepository(self._db)

        self._state_manager = MachineStateManager()
        self._parser = EventParser()

        self._camera = CameraManager(
            preferred_index=config.camera_index,
            max_index=config.camera_scan_max_index,
        )
        self._media_service = MediaService(
            self._camera,
            video_duration=config.video_duration,
        )

        self._screen_capture = ScreenCapture()
        self._ocr_service = OcrService()

        self._telegram = TelegramClient(
            token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            poll_interval=config.telegram_poll_interval,
            dry_run=config.dry_run,
            retry_check_interval=config.telegram_retry_check_interval,
        )
        self._telegram.set_photo_provider(self._media_service.capture_jpeg)
        self._telegram.set_video_provider(self._media_service.capture_video)
        self._telegram.set_status_provider(self._build_status_text)
        self._telegram.set_screen_capture_provider(self._screen_capture.capture)
        self._telegram.set_ocr_provider(self._ocr_service.recognize)
        self._telegram.set_ocr_crop_provider(self._ocr_service.crop_image)

        self._tail: Optional[LogTailReader] = None
        self._watcher: Optional[LogDirectoryWatcher] = None
        self._log_finder: Optional[LogFinder] = None
        self._line_queue: list[str] = []
        self._line_lock = threading.Lock()

        # Çalışma başlangıç zamanı (süre hesaplamak için)
        self._work_started_at: Optional[datetime] = None

    def request_stop(self, *_: object) -> None:
        logger.info("Durdurma sinyali alındı.")
        self._stop_event.set()

    def run(self) -> None:
        """Servisi başlatır ve ana thread'i bloke eder."""
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        try:
            self._startup()
        except Exception as exc:
            logger.exception("Başlatma hatası: %s", exc)
            self._shutdown()
            return

        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(1.0)
        except KeyboardInterrupt:
            self.request_stop()
        finally:
            self._shutdown()

    def _startup(self) -> None:
        logger.info("=" * 60)
        logger.info("CypCut Monitor başlatılıyor")
        logger.info("Makine: %s | Log dizini: %s", self._config.machine_name, self._config.log_dir)
        logger.info("=" * 60)

        self._restore_state()
        self._camera.initialize()

        self._telegram.start()

        latest = find_latest_log(self._config.log_dir)
        if latest is None:
            logger.warning("Aktif log dosyası bulunamadı, bekleniyor: %s", self._config.log_dir)
            placeholder = self._config.log_dir / "CypCut-waiting.rtf"
        else:
            logger.info("Aktif log dosyası: %s", latest)
            placeholder = latest

        self._tail = LogTailReader(
            log_path=placeholder,
            on_line=self._enqueue_line,
            poll_interval=self._config.tail_poll_interval,
        )
        self._tail.start()

        self._watcher = LogDirectoryWatcher(
            directory=self._config.log_dir,
            on_new_file=self._on_new_file,
            retry_interval=self._config.log_watcher_retry_interval,
            fallback_scan_interval=self._config.log_finder_scan_interval,
        )
        self._watcher.start()

        # LogFinder ile ek tarama
        self._log_finder = LogFinder(
            log_dir=self._config.log_dir,
            scan_interval=self._config.log_finder_scan_interval,
            on_new_file=self._on_new_file,
        )
        self._log_finder.start()

    def _shutdown(self) -> None:
        logger.info("Servis kapatılıyor...")
        if self._log_finder is not None:
            self._log_finder.stop()
        if self._watcher is not None:
            self._watcher.stop()
        if self._tail is not None:
            self._tail.stop()
        if self._telegram is not None:
            self._telegram.stop()
        self._camera.close()
        logger.info("Servis durduruldu.")

    def _restore_state(self) -> None:
        state, last_event_text, last_event_at = self._state_repo.load()
        if last_event_text:
            logger.info("Kalıcı durum yüklendi: %s (son olay: %s @ %s)",
                        state.value, last_event_text, last_event_at)
        else:
            logger.info("Kalıcı durum yüklendi: %s (ilk çalıştırma)", state.value)
        self._state_manager.restore(state)

        # WORKING durumundaysa çalışma başlangıç zamanını hesapla
        if state == MachineState.WORKING and last_event_at is not None:
            self._work_started_at = last_event_at

    def _enqueue_line(self, line: str) -> None:
        """Tail reader thread'inden gelen satırları kuyruğa al."""
        with self._line_lock:
            self._line_queue.append(line)

    def _drain_queue(self) -> list[str]:
        with self._line_lock:
            items = self._line_queue
            self._line_queue = []
        return items

    def _on_new_file(self, path: Path) -> None:
        """Yeni log dosyası tespit edildiğinde tail reader'ı yönlendir."""
        if self._tail is None:
            return
        self._tail.switch_file(path)
        logger.info("Yeni log dosyasına geçildi: %s", path)

    def run_once(self) -> None:
        """Tek bir tick: kuyruğu boşalt, parse et, state güncelle, bildirim gönder."""
        for line in self._drain_queue():
            self._process_line(line)

    def _process_line(self, raw_line: str) -> None:
        cleaned = rtf_clean(raw_line)
        event = self._parser.parse(cleaned)
        if event is None:
            return
        self._handle_event(event)

    def _handle_event(self, event: ParsedEvent) -> None:
        result = self._state_manager.process(event)
        if not result.changed and not event.is_alarm and not event.is_alarm_clear:
            return

        ts = event.timestamp or datetime.now()
        if event.is_alarm:
            self._on_alarm(event, ts)
        elif event.is_alarm_clear:
            self._on_alarm_clear(event, ts)
        elif result.changed:
            self._on_transition(result, ts)

        self._state_repo.save(
            state=self._state_manager.state,
            last_event_text=event.text,
            last_event_at=ts,
        )

    def _on_alarm(self, event: ParsedEvent, ts: datetime) -> None:
        key = f"ALARM::{event.text}"
        should_send = self._cooldown_ok(key, ts)
        sent = False
        if should_send:
            message = self._format_alarm_message(event.text, ts)
            # Video çek (5 sn)
            video_path = self._media_service.capture_video()
            if video_path is not None:
                sent = self._telegram.send_video(video_path, caption=message)
            else:
                # Video alınamazsa fotoğraf dene
                photo_path = self._media_service.capture_jpeg()
                if photo_path is not None:
                    sent = self._telegram.send_photo(photo_path, caption=message)
                else:
                    sent = self._telegram.send_message(message)
            self._cooldown_repo.set_last_sent(key, ts)
            if sent:
                logger.info("Alarm bildirimi gönderildi: %s", event.text)
            else:
                logger.warning("Alarm bildirimi gönderilemedi: %s", event.text)
        else:
            logger.info("Alarm cooldown aktif, atlandı: %s", event.text)
            sent = False

        self._alarm_repo.insert(
            alarm_text=event.text,
            raw_line=event.raw_line,
            occurred_at=ts,
            telegram_sent=sent,
        )

    def _on_alarm_clear(self, event: ParsedEvent, ts: datetime) -> None:
        message = self._format_alarm_clear_message(event.text, ts)
        self._telegram.send_message(message)
        logger.info("Alarm temizleme bildirimi gönderildi: %s", event.text)

    def _on_transition(self, result, ts: datetime) -> None:
        from ..domain.machine_state import TransitionResult

        assert isinstance(result, TransitionResult)

        # Tüm geçişlerde bildirim gönder
        if result.current == MachineState.WORKING:
            if result.previous == MachineState.IDLE:
                # İlk çalışma başlangıcı
                self._work_started_at = ts
                message = self._format_start_message(ts, result.event.text)
            else:
                # Pause/Alarm -> Working (devam)
                self._work_started_at = ts
                message = self._format_resume_message(ts, result.event.text)

            # Video + fotoğraf gönder
            video_path = self._media_service.capture_video()
            if video_path is not None:
                sent = self._telegram.send_video(video_path, caption=message)
            else:
                photo_path = self._media_service.capture_jpeg()
                if photo_path is not None:
                    sent = self._telegram.send_photo(photo_path, caption=message)
                else:
                    sent = self._telegram.send_message(message)
            logger.info("Çalışma bildirimi gönderildi: %s", result.event.text)

        elif result.current == MachineState.PAUSED:
            self._work_started_at = None
            message = self._format_stop_message(ts, result.event.text)

            # Video + fotoğraf gönder
            video_path = self._media_service.capture_video()
            if video_path is not None:
                sent = self._telegram.send_video(video_path, caption=message)
            else:
                photo_path = self._media_service.capture_jpeg()
                if photo_path is not None:
                    sent = self._telegram.send_photo(photo_path, caption=message)
                else:
                    sent = self._telegram.send_message(message)
            logger.info("Duruş bildirimi gönderildi: %s", result.event.text)

        elif result.current == MachineState.ALARM:
            # Alarm zaten _on_alarm'da işleniyor, burada sadece durum değişikliği
            message = self._format_alarm_active_message(ts, result.event.text)
            sent = self._telegram.send_message(message)
            logger.info("Alarm durum bildirimi gönderildi: %s", result.event.text)

        else:
            return

        self._transition_repo.insert(
            from_state=result.previous,
            to_state=result.current,
            reason=result.event.text,
            occurred_at=ts,
            telegram_sent=sent,
        )

    def _cooldown_ok(self, key: str, now: datetime) -> bool:
        last = self._cooldown_repo.get_last_sent(key)
        if last is None:
            return True
        return (now - last).total_seconds() >= self._config.alarm_cooldown_seconds

    def _format_alarm_message(self, alarm_text: str, ts: datetime) -> str:
        return (
            "🚨 Lazer Alarmı\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts.strftime('%H:%M:%S')}\n"
            "\n"
            "Alarm:\n"
            f"{alarm_text}"
        )

    def _format_alarm_clear_message(self, alarm_text: str, ts: datetime) -> str:
        return (
            "✅ Alarm Temizlendi\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts.strftime('%H:%M:%S')}\n"
            f"Alarm: {alarm_text}"
        )

    def _format_alarm_active_message(self, ts: datetime, reason: str) -> str:
        return (
            "⚠️ Makine Alarm Durumunda\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts.strftime('%H:%M:%S')}\n"
            "\n"
            "Alarm:\n"
            f"{reason}"
        )

    def _format_stop_message(self, ts: datetime, reason: str) -> str:
        duration_text = self._get_work_duration(ts)
        return (
            "⏸️ Makine Durdu\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts.strftime('%H:%M:%S')}\n"
            + (f"Çalışma süresi: {duration_text}\n" if duration_text else "")
            + "\n"
            "Durum:\n"
            f"{reason}"
        )

    def _format_start_message(self, ts: datetime, reason: str) -> str:
        return (
            "▶️ Makine Başladı\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts.strftime('%H:%M:%S')}\n"
            "\n"
            "Durum:\n"
            f"{reason}"
        )

    def _format_resume_message(self, ts: datetime, reason: str) -> str:
        return (
            "▶️ Makine Devam Etti\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts.strftime('%H:%M:%S')}\n"
            "\n"
            "Durum:\n"
            f"{reason}"
        )

    def _get_work_duration(self, ts: datetime) -> str:
        """Son çalışma başlangıcından bu yana geçen süreyi hesaplar."""
        if self._work_started_at is None:
            return ""
        delta = ts - self._work_started_at
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds} saniye"
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        if minutes < 60:
            return f"{minutes} dakika {seconds} saniye"
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours} saat {minutes} dakika"

    def _build_status_text(self) -> str:
        state = self._state_manager.state
        last_event = self._state_manager.last_event
        last_at = self._state_manager.last_event_at

        cam_status = (
            f"✅ Bağlı (Index: {self._camera.active_index})"
            if self._camera.is_available
            else "❌ Bağlı Değil"
        )

        last_text = last_event.text if last_event else "-"
        last_at_text = last_at.strftime("%H:%M:%S") if last_at else "-"

        # Çalışma süresi bilgisi
        work_duration = ""
        if state == MachineState.WORKING and self._work_started_at is not None:
            delta = datetime.now() - self._work_started_at
            total_seconds = int(delta.total_seconds())
            if total_seconds < 60:
                work_duration = f"\nÇalışma süresi: {total_seconds} sn"
            else:
                minutes = total_seconds // 60
                seconds = total_seconds % 60
                if minutes < 60:
                    work_duration = f"\nÇalışma süresi: {minutes} dk {seconds} sn"
                else:
                    hours = minutes // 60
                    minutes = minutes % 60
                    work_duration = f"\nÇalışma süresi: {hours} sa {minutes} dk"

        # Durum açıklaması
        state_desc = {
            MachineState.IDLE: "💤 Boşta",
            MachineState.WORKING: "⚙️ Çalışıyor" + work_duration,
            MachineState.PAUSED: "⏸️ Duraklatılmış",
            MachineState.ALARM: "🚨 Alarm Durumunda",
        }.get(state, state.value)

        # Kuyruk bilgisi
        pending = self._telegram.pending_count
        pending_text = f"\n\nGönderilmemiş mesaj: {pending}" if pending > 0 else ""

        recent_alarms = self._alarm_repo.recent(limit=5)
        alarm_lines = []
        for a in recent_alarms:
            alarm_lines.append(
                f"  • {a['occurred_at']} - {a['alarm_text'][:80]}"
            )
        alarms_block = "\n".join(alarm_lines) if alarm_lines else "  (yok)"

        return (
            f"📊 Makine Durumu\n"
            f"\n"
            f"Makine: {self._config.machine_name}\n"
            f"Durum: {state_desc}\n"
            f"Saat: {datetime.now().strftime('%H:%M:%S')}\n"
            f"Son olay: {last_text} @ {last_at_text}\n"
            f"\n"
            f"Kamera:\n"
            f"{cam_status}\n"
            f"{pending_text}\n"
            f"\n"
            f"Son 5 Alarm:\n"
            f"{alarms_block}"
        )


class MonitorLoop(threading.Thread):
    """MonitorService'in kuyruk işleme döngüsünü ayrı bir thread'de çalıştırır."""

    def __init__(self, service: MonitorService, poll_interval: float = 0.2) -> None:
        super().__init__(name="MonitorLoop", daemon=True)
        self._service = service
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("Monitor loop başlatıldı.")
        while not self._stop_event.is_set():
            try:
                self._service.run_once()
            except Exception as exc:
                logger.exception("Monitor tick hatası: %s", exc)
            self._stop_event.wait(self._poll_interval)
        logger.info("Monitor loop durduruldu.")


__all__ = ["MonitorService", "MonitorLoop"]
