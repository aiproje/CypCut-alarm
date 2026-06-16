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
from ..infrastructure.ocr_table_parser import (
    OcrAlarmRow,
    format_table,
    _translate_chinese,
    _get_event_kind_from_text,
    _identify_alarm,
)
from ..infrastructure.rtf_cleaner import clean as rtf_clean
from ..infrastructure.stream_server import StreamServer
from ..infrastructure.telegram_client import TelegramClient
from ..logging_setup import get_logger
from .ocr_monitor_service import OcrMonitorService

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
        self._telegram.set_stream_provider(self._handle_stream_command)

        # Stream sunucusu
        self._stream_server: Optional[StreamServer] = None

        self._tail: Optional[LogTailReader] = None
        self._watcher: Optional[LogDirectoryWatcher] = None
        self._log_finder: Optional[LogFinder] = None
        self._line_queue: list[str] = []
        self._line_lock = threading.Lock()

        # OCR izleme servisi
        self._ocr_monitor: Optional[OcrMonitorService] = None

        # Çalışma başlangıç zamanı (süre hesaplamak için)
        self._work_started_at: Optional[datetime] = None

        # Stream koruyucu kilit
        self._stream_lock = threading.Lock()

    def request_stop(self, *_: object) -> None:
        logger.info("Durdurma sinyali alındı.")
        self._stop_event.set()

    def _handle_stream_command(self) -> Optional[str]:
        with self._stream_lock:
            if self._stream_server is not None:
                self._stream_server.stop()
                self._stream_server = None
                logger.info("Stream sunucusu durduruldu (komut ile).")
                return None

            if not self._config.stream_enabled:
                logger.warning("Stream özelliği devre dışı.")
                return ""

            if not self._camera.is_available:
                logger.warning("Kamera yok, stream başlatılamaz.")
                return ""

            camera_index = self._camera.active_index or 0
            self._stream_server = StreamServer(
                host='0.0.0.0',
                port=self._config.stream_port,
                width=self._config.stream_width,
                height=self._config.stream_height,
                fps=self._config.stream_fps,
                quality=self._config.stream_quality,
                hostname=self._config.stream_host,
            )

            ok = self._stream_server.start(camera_index=camera_index)
            if not ok:
                self._stream_server = None
                return ""

            url = self._stream_server.get_stream_url()
            logger.info("Stream sunucusu başlatıldı: %s", url)
            return url

        return ""

    def _stop_stream_if_running(self) -> None:
        if self._stream_server is not None:
            try:
                self._stream_server.stop()
            except Exception:
                pass
            self._stream_server = None

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

        # OCR izleme servisini başlat
        self._ocr_monitor = OcrMonitorService(
            config=self._config,
            screen_capture=self._screen_capture,
            ocr_service=self._ocr_service,
            telegram=self._telegram,
            state_manager=self._state_manager,
            state_repo=self._state_repo,
            alarm_repo=self._alarm_repo,
            transition_repo=self._transition_repo,
            cooldown_repo=self._cooldown_repo,
        )
        self._ocr_monitor.start()
        logger.info("OCR izleme servisi başlatıldı.")

        # Log dosyası okuma pasif (kod korunuyor ama başlatılmıyor)
        logger.info("Log dosyası okuma pasif modda (OCR izleme aktif).")

    def _shutdown(self) -> None:
        logger.info("Servis kapatılıyor...")
        if self._ocr_monitor is not None:
            self._ocr_monitor.stop()
        if self._log_finder is not None:
            self._log_finder.stop()
        if self._watcher is not None:
            self._watcher.stop()
        if self._tail is not None:
            self._tail.stop()
        if self._telegram is not None:
            self._telegram.stop()
        self._stop_stream_if_running()
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
        ocr = self._ocr_monitor
        ocr_state = ocr.last_status if ocr else None
        ocr_ts = ocr.last_scan_time if ocr else '-'
        ocr_rows = ocr.last_ocr_rows if ocr else []
        is_bg = ocr.is_background if ocr else False

        state = ocr_state or self._state_manager.state

        # Çalışma süresi
        work = ""
        if state == MachineState.WORKING and self._work_started_at:
            sec = int((datetime.now() - self._work_started_at).total_seconds())
            if sec >= 3600:
                work = f" ({sec//3600}sa {(sec%3600)//60}dk)"
            elif sec >= 60:
                work = f" ({sec//60}dk {sec%60}sn)"
            else:
                work = f" ({sec}sn)"

        icons = {MachineState.IDLE: "💤", MachineState.WORKING: "⚙️", MachineState.PAUSED: "⏸️", MachineState.ALARM: "🚨"}
        names = {MachineState.IDLE: "Boşta", MachineState.WORKING: "Çalışıyor", MachineState.PAUSED: "Duraklı", MachineState.ALARM: "Alarm"}
        durum = f"{icons.get(state, '❓')} {names.get(state, state.value)}{work}"

        cam = "✅ Kamera var" if self._camera.is_available else "❌ Kamera yok"
        stream = "✅ Yayın var" if self._stream_server and self._stream_server.is_running else "💤 Yayın yok"
        ocr_durum = "✅" if ocr and ocr._thread and ocr._thread.is_alive() else "❌"

        # Son OCR satırları
        satirlar = []
        for r in ocr_rows:
            if r.is_alarm_active:
                code = r.get_alarm_code() or 'Alarm'
                satirlar.append(f"🚨 {code}")
            elif r.event_kind in ('stop', 'start', 'resume'):
                op = _translate_chinese(str(r.operation or r.alarm_info or ''))[:50]
                if op:
                    satirlar.append(op)

        satir_text = "\n".join(satirlar) if satirlar else "(yok)"

        bg = "\n⚠️ CypCut arka planda!" if is_bg else ""

        return (
            f"📊 {self._config.machine_name}\n"
            f"{durum}{bg}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}\n"
            f"OCR {ocr_durum} {ocr_ts}\n"
            f"\n"
            f"{cam} | {stream}\n"
            f"\n"
            f"{satir_text}\n"
            f"\n"
            f"FOTO VIDEO EKRAN YAYIN"
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
