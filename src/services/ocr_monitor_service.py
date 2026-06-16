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
from ..infrastructure.ocr_table_parser import (
    OcrAlarmRow,
    format_table,
    parse_ocr_text,
    _translate_chinese,
    _identify_alarm,
    _get_event_kind_from_text,
    _extract_error_code,
)
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

_CHINESE_PATTERN = __import__('re').compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')


class OcrMonitorService:
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

        self._last_alarm_keys: set[str] = set()
        self._last_status: Optional[MachineState] = None
        self._last_scan_time: str = ''
        self._last_ocr_text: str = ''
        self._last_ocr_rows: list[OcrAlarmRow] = []

        self._ocr_log_path = config.ocr_log_path
        self._ensure_ocr_log_dir()

        self._background_since: Optional[float] = None
        self._background_notified: bool = False
        self._consecutive_failures: int = 0
        self._last_anomaly_notification_time: float = 0.0

    @property
    def last_status(self) -> Optional[MachineState]:
        return self._last_status

    @property
    def last_scan_time(self) -> str:
        return self._last_scan_time

    @property
    def last_ocr_text(self) -> str:
        return self._last_ocr_text

    @property
    def last_ocr_rows(self) -> list[OcrAlarmRow]:
        return self._last_ocr_rows

    @property
    def is_background(self) -> bool:
        return self._background_since is not None

    def _ensure_ocr_log_dir(self) -> None:
        self._ocr_log_path.parent.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
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
            "OCR izleme başlatıldı (aralık: %.1fs)", self._config.ocr_monitor_interval,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        logger.info("OCR izleme durduruldu.")

    # ------------------------------------------------------------------
    # Arkaplan / Pencere kontrolü
    # ------------------------------------------------------------------

    def _is_cypcut_window_visible(self) -> bool:
        try:
            import win32gui
            hwnd = self._find_window()
            if hwnd is None:
                return False
            if win32gui.IsIconic(hwnd):
                return False
            if not win32gui.IsWindowVisible(hwnd):
                return False
            return True
        except Exception:
            return True

    def _find_window(self) -> Optional[int]:
        try:
            import win32gui
        except ImportError:
            return None
        found_hwnd: Optional[int] = None

        def _enum_cb(hwnd: int, _: object) -> bool:
            nonlocal found_hwnd
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return True
            try:
                cls = win32gui.GetClassName(hwnd)
            except Exception:
                cls = "?"
            is_cp = "\u6fc0\u5149" in title or "CypCut" in title or "cypcut" in cls.lower()
            if is_cp:
                skips = ["Edge", "Chrome", "Firefox", "Visual Studio", "Notepad",
                         "VS Code", "Sublime", "GitHub", "Explorer", "cmd",
                         "Terminal", "PowerShell", "python", "Stack"]
                for sw in skips:
                    if sw.lower() in title.lower():
                        return True
                found_hwnd = hwnd
                return False
            return True

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception:
            pass
        return found_hwnd

    def _check_background_status(self, ok: bool) -> Optional[str]:
        visible = self._is_cypcut_window_visible()
        now = time.monotonic()

        if not visible:
            if self._background_since is None:
                self._background_since = now
                self._consecutive_failures = 0
                return None
            elapsed = now - self._background_since
            thr = self._config.background_long_threshold_seconds
            if elapsed >= thr and not self._background_notified:
                self._background_notified = True
                mins = int(elapsed // 60)
                return (
                    f"\u26a0\ufe0f CypCut Arkaplanda\n"
                    f"\n"
                    f"Makine: {self._config.machine_name}\n"
                    f"Pencere {mins} dakikadır arkaplanda/minimize.\n"
                    f"OCR izleme duraklatıldı.\n"
                    f"\n"
                    f"Pencereyi öne getirdiğinizde izleme devam eder."
                )
            return None

        if self._background_since is not None:
            self._background_since = None
            self._background_notified = False
            self._consecutive_failures = 0
            return (
                f"\u2705 CypCut Önde\n"
                f"\n"
                f"Makine: {self._config.machine_name}\n"
                f"Pencere tekrar görünür. OCR izleme devam ediyor."
            )

        if not ok:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 6 and (now - self._last_anomaly_notification_time) > 300:
                self._last_anomaly_notification_time = now
                return (
                    f"\u26a0\ufe0f OCR Uyar\u0131s\u0131\n"
                    f"\n"
                    f"Makine: {self._config.machine_name}\n"
                    f"Son {self._consecutive_failures} tarama anlams\u0131z sonu\u00e7 verdi.\n"
                    f"CypCut arkaplanda veya ekran kapal\u0131 olabilir."
                )
        else:
            self._consecutive_failures = 0
        return None

    # ------------------------------------------------------------------
    # Ana döngü
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        logger.info("OCR izleme döngüsü başladı.")
        while not self._stop_event.is_set():
            try:
                self._scan_once()
            except Exception as exc:
                logger.exception("OCR tarama hatası: %s", exc)
            self._stop_event.wait(self._config.ocr_monitor_interval)
        logger.info("OCR izleme döngüsü durduruldu.")

    def _scan_once(self) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not self._screen.is_available:
            return

        path = self._screen.capture()
        if path is None:
            return

        try:
            ocr_text = self._ocr.recognize(path)
        except Exception as exc:
            logger.exception("OCR hatası: %s", exc)
            ocr_text = None
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

        if ocr_text is None:
            return

        self._last_scan_time = ts
        self._last_ocr_text = ocr_text

        self._log_ocr_data(ts, ocr_text)

        rows = parse_ocr_text(ocr_text)
        self._last_ocr_rows = rows

        if not rows:
            bg = self._check_background_status(False)
            if bg:
                self._telegram.send_message(bg)
            return

        has = any(r.is_meaningful for r in rows)
        bg = self._check_background_status(has)
        if bg:
            self._telegram.send_message(bg)
            if self._background_since is not None:
                return

        self._evaluate_state(rows, ts)

    # ------------------------------------------------------------------
    # Durum değerlendirme
    # ------------------------------------------------------------------

    def _evaluate_state(self, rows: list[OcrAlarmRow], ts: str) -> None:
        current_alarm_keys: set[str] = set()
        active_alarms: list[OcrAlarmRow] = []
        cleared_alarms_list: list[OcrAlarmRow] = []
        other_events: list[OcrAlarmRow] = []

        for row in rows:
            ek = row.event_kind
            if ek == 'alarm':
                key = self._make_alarm_key(row)
                current_alarm_keys.add(key)
                active_alarms.append(row)
            elif ek == 'alarm_clear':
                cleared_alarms_list.append(row)
            elif ek in ('stop', 'start', 'resume'):
                other_events.append(row)

        new_alarms = current_alarm_keys - self._last_alarm_keys
        cleared = self._last_alarm_keys - current_alarm_keys

        previous_state = self._last_status or self._state_manager.state

        if active_alarms:
            new_state = MachineState.ALARM
        elif rows:
            new_state = MachineState.WORKING
        else:
            new_state = MachineState.IDLE

        # Yeni alarmlar varsa TEK mesajda birleştir
        if new_alarms and active_alarms:
            new_active = [r for r in active_alarms if self._make_alarm_key(r) in new_alarms]
            self._on_new_alarms_batch(new_active, ts)

        # Alarm temizlenmiş mi?
        if cleared_alarms_list and not active_alarms:
            self._on_all_alarms_cleared(ts)

        # Durum değişikliği
        if new_state != previous_state:
            self._on_state_change(previous_state, new_state, rows, ts)

        self._last_alarm_keys = current_alarm_keys
        self._last_status = new_state

    def _make_alarm_key(self, row: OcrAlarmRow) -> str:
        return "::".join([row.alarm_info or "", row.alarm_id or "", row.status or ""])

    # ------------------------------------------------------------------
    # Batch alarm bildirimi (tüm alarmlar TEK mesaj)
    # ------------------------------------------------------------------

    def _on_new_alarms_batch(self, alarm_rows: list[OcrAlarmRow], ts: str) -> None:
        if not alarm_rows:
            return

        now = datetime.now()
        cooldown_key = f"OCR_BATCH::{ts[:10]}"
        if not self._cooldown_ok(cooldown_key, now):
            logger.info("Batch alarm cooldown aktif, atlandı.")
            return

        alarm_count = len(alarm_rows)
        header = "\U0001f6a8 Alarm Tespit Edildi" if alarm_count == 1 else f"\U0001f6a8 {alarm_count} Alarm Tespit Edildi"
        parts: list[str] = [
            header,
            "",
            f"    Makine: {self._config.machine_name}",
            f"    Saat: {ts}",
            "",
        ]

        for i, row in enumerate(alarm_rows):
            code = row.get_alarm_code()
            eid = row.alarm_id or _extract_error_code(str(row.alarm_info or '') + str(row.status or '')) or '?'
            desc = row.get_turkish_description()

            if i > 0:
                parts.append("")

            parts.append(f"\u2757 Alarm {i+1}: {code or 'Bilinmeyen Alarm'}")
            if eid and eid != '?':
                parts.append(f"   Kimlik: {eid}")

            row_ts = row.timestamp or ''
            if row_ts:
                parts.append(f"   Zaman: {row_ts}")

            parts.append("")
            # Açıklamayı satır satır ekle
            for line in desc.split('\n'):
                if line.strip():
                    parts.append(f"   {line}")

            raw_info = _translate_chinese(str(row.alarm_info or '')) or ''
            raw_status = _translate_chinese(str(row.status or '')) or ''
            ham_parts = []
            if raw_info and raw_info not in desc and raw_info != code:
                ham_parts.append(raw_info)
            if raw_status and raw_status not in desc and raw_status not in ham_parts and raw_status != code:
                ham_parts.append(raw_status)
            if ham_parts:
                parts.append(f"   Ham: {' | '.join(ham_parts)}")

        parts.append("")
        if alarm_count > 1:
            parts.append("Yukarıda listelenen alarmlar için gerekli önlemleri alın.")
        else:
            parts.append("Gerekli önlemleri alın.")

        message = "\n".join(parts)
        sent = self._telegram.send_message(message)

        self._cooldown_repo.set_last_sent(cooldown_key, now)

        for row in alarm_rows:
            alarm_text = (
                f"{row.alarm_info or '?'} "
                f"(ID: {row.alarm_id or '?'}, Durum: {row.status or '?'})"
            )
            self._alarm_repo.insert(
                alarm_text=alarm_text,
                raw_line=f"OCR:{row.to_dict()}",
                occurred_at=now,
                telegram_sent=sent,
            )

        if sent:
            logger.info("Batch alarm bildirimi gönderildi (%d alarm)", len(alarm_rows))
        else:
            logger.warning("Batch alarm bildirimi GÖNDERİLEMEDİ (%d alarm)", len(alarm_rows))

    def _on_all_alarms_cleared(self, ts: str) -> None:
        message = (
            "\u2705 Alarmlar Temizlendi\n"
            "\n"
            f"    Makine: {self._config.machine_name}\n"
            f"    Saat: {ts}\n"
            "\n"
            "Tüm alarmlar kalktı, makine çalışmaya hazır.\n"
            "\n"
            "ℹ️ DURUM yazarak güncel durumu görebilirsiniz."
        )
        self._telegram.send_message(message)
        logger.info("Alarm temizleme bildirimi gönderildi.")

    # ------------------------------------------------------------------
    # Durum değişikliği
    # ------------------------------------------------------------------

    def _on_state_change(
        self,
        previous: MachineState,
        current: MachineState,
        rows: list[OcrAlarmRow],
        ts: str,
    ) -> None:
        event, icon, title = self._build_state_event(previous, current, rows, ts)
        if event is None:
            return

        result = self._state_manager.process(event)
        if not result.changed:
            return

        message = (
            f"{icon} {title}\n"
            "\n"
            f"    Makine: {self._config.machine_name}\n"
            f"    Saat: {ts}\n"
        )

        # Aktif alarm detayı
        alarm_lines = []
        for row in rows:
            if row.is_alarm_active:
                code = row.get_alarm_code() or 'Alarm'
                desc = row.get_turkish_description()[:150]
                eid = row.alarm_id or '?'
                alarm_lines.append(f"\n🚨 {code} (ID: {eid})")
                # Açıklamanın sadece ilk satırını al
                first_line = desc.split('\n')[0] if desc else ''
                if first_line:
                    alarm_lines.append(f"   {first_line}")

        if alarm_lines:
            message += "\n" + "".join(alarm_lines)

        # Temizlenen alarm var mı?
        cleared = []
        for row in rows:
            if row.is_alarm_clear:
                code = row.get_alarm_code() or ''
                info = _translate_chinese(str(row.alarm_info or row.status or ''))[:60]
                label = code if code else info
                if label:
                    cleared.append(f"\n✅ {label} temizlendi")

        if cleared:
            message += "\n" + "".join(cleared)

        # Durum/operasyon detayı
        for row in rows:
            if row.event_kind in ('stop', 'start', 'resume') and (row.operation or row.alarm_info):
                op = _translate_chinese(str(row.operation or row.alarm_info or ''))[:80]
                message += f"\n   📋 {op}"

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
        logger.info("Durum: %s -> %s (%s)", previous.value, current.value, title)

    def _build_state_event(
        self,
        previous: MachineState,
        current: MachineState,
        rows: list[OcrAlarmRow],
        ts: str,
    ):
        """Durum değişikliği için event, ikon ve başlık döndürür."""
        # Önce OCR satırlarındaki olay türüne bak
        has_alarm = any(r.event_kind == 'alarm' for r in rows)
        has_alarm_clear = any(r.event_kind == 'alarm_clear' for r in rows)
        has_stop = any(r.event_kind == 'stop' for r in rows)
        has_start = any(r.event_kind == 'start' for r in rows)
        has_resume = any(r.event_kind == 'resume' for r in rows)

        now = datetime.now()

        if current == MachineState.ALARM:
            event = ParsedEvent(
                kind=EventKind.ALARM,
                timestamp=now,
                text="OCR: Alarm durumu tespit edildi",
                raw_line=f"OCR:{[r.to_dict() for r in rows]}",
            )
            icon = "\U0001f6a8"
            title = "Alarm Durumu"
            return event, icon, title

        if current == MachineState.WORKING:
            if previous == MachineState.ALARM:
                event = ParsedEvent(
                    kind=EventKind.RESUME,
                    timestamp=now,
                    text="OCR: Alarm çözüldü, çalışıyor",
                    raw_line=f"OCR:{[r.to_dict() for r in rows]}",
                )
                icon = "\u2705"
                title = "Alarm Çözüldü, Çalışıyor"
            elif has_resume or previous == MachineState.PAUSED:
                event = ParsedEvent(
                    kind=EventKind.RESUME,
                    timestamp=now,
                    text="OCR: Makine devam ediyor",
                    raw_line=f"OCR:{[r.to_dict() for r in rows]}",
                )
                icon = "\u25b6\ufe0f"
                title = "Devam Ediyor"
            else:
                event = ParsedEvent(
                    kind=EventKind.START,
                    timestamp=now,
                    text="OCR: Çalışma başladı",
                    raw_line=f"OCR:{[r.to_dict() for r in rows]}",
                )
                icon = "\u25b6\ufe0f"
                title = "Çalışma Başladı"
            return event, icon, title

        if current == MachineState.PAUSED:
            # İş bitti mi yoksa sadece duraklama mı?
            has_completion_hint = any(
                "stop" in (r.operation or '').lower() and 'nest' in (r.alarm_info or '').lower()
                for r in rows
            )
            if has_completion_hint or has_stop:
                event = ParsedEvent(
                    kind=EventKind.STOP,
                    timestamp=now,
                    text="OCR: İş tamamlandı",
                    raw_line=f"OCR:{[r.to_dict() for r in rows]}",
                )
                icon = "\u23f9\ufe0f"
                title = "İş Tamamlandı"
            else:
                event = ParsedEvent(
                    kind=EventKind.STOP,
                    timestamp=now,
                    text="OCR: Makine durdu",
                    raw_line=f"OCR:{[r.to_dict() for r in rows]}",
                )
                icon = "\u23f8\ufe0f"
                title = "Makine Durdu"
            return event, icon, title

        return None, None, None

    # ------------------------------------------------------------------
    # Mesaj formatlama (eski uyumluluk)
    # ------------------------------------------------------------------

    def _format_ocr_alarm_message(self, alarm_row: OcrAlarmRow, ts: str) -> str:
        code = alarm_row.get_alarm_code() or 'Bilinmeyen'
        desc = alarm_row.get_turkish_description()
        return (
            "\U0001f6a8 Lazer Alarm\u0131 (OCR)\n"
            "\n"
            f"Makine: {self._config.machine_name}\n"
            f"Saat: {ts}\n"
            "\n"
            f"{code}\n"
            "\n"
            f"{desc}"
        )

    def _cooldown_ok(self, key: str, now: datetime) -> bool:
        last = self._cooldown_repo.get_last_sent(key)
        if last is None:
            return True
        return (now - last).total_seconds() >= self._config.alarm_cooldown_seconds

    # ------------------------------------------------------------------
    # OCR log
    # ------------------------------------------------------------------

    def _log_ocr_data(self, ts: str, raw_text: str) -> None:
        try:
            with open(self._ocr_log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Tarih: {ts}\n")
                f.write(f"{'='*60}\n")
                f.write(raw_text)
                f.write("\n")
                rows = parse_ocr_text(raw_text)
                if rows:
                    f.write("\n--- Ayrıştırılmış ---\n")
                    f.write(format_table(rows))
                    f.write("\n\nTürkçe:\n")
                    for r in rows:
                        if r.is_alarm_active:
                            f.write(f"  {r.get_alarm_code() or '?'}: {r.get_turkish_description()}\n")
                f.write(f"\n--- Durum ---\n")
                f.write(f"Durum: {self._last_status or '?'}\n")
                f.write(f"Arkaplan: {'Evet' if self._background_since is not None else 'Hayır'}\n")
                f.write(f"Hata sayısı: {self._consecutive_failures}\n")
                f.write("\n")
            logger.debug("OCR loglandı.")
        except Exception as exc:
            logger.warning("OCR log hatası: %s", exc)


class OcrMonitorLoop(threading.Thread):
    def __init__(self, service: OcrMonitorService) -> None:
        super().__init__(name="OcrMonitorLoop", daemon=True)
        self._service = service

    def stop(self) -> None:
        self._service.stop()

    def run(self) -> None:
        self._service.start()
        while self._service._thread is not None and self._service._thread.is_alive():
            time.sleep(1.0)


__all__ = ["OcrMonitorService", "OcrMonitorLoop"]
