"""Telegram istemcisi.

Geliştirilmiş versiyon:
  - Başarısız mesajlar için retry kuyruğu (kayıp mesaj yok)
  - Session reconnect (internet kopması çözümü)
  - Video gönderme desteği
  - Exponential backoff retry
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

from ..logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class _PendingMessage:
    """Gönderilmemiş mesaj/foto/video için kuyruk elemanı."""

    msg_type: str  # "message", "photo", "video"
    text: str = ""
    file_path: Optional[Path] = None
    caption: str = ""
    retry_count: int = 0
    created_at: float = field(default_factory=time.monotonic)


class TelegramClient:
    """Telegram Bot API istemcisi.

    Ek özellikler:
      - Retry kuyruğu: İnternet yokken biriken mesajlar bağlanınca gönderilir
      - Exponential backoff: 1s, 2s, 4s, 8s... max 60s
      - Session yenileme: Hata sonrası yeni session açar
    """

    BASE_URL = "https://api.telegram.org"
    MAX_RETRIES = 20
    BACKOFF_BASE = 1.0
    BACKOFF_MAX = 60.0

    def __init__(
        self,
        token: str,
        chat_id: str,
        poll_interval: float = 2.0,
        dry_run: bool = False,
        retry_check_interval: float = 5.0,
    ) -> None:
        if not token:
            raise ValueError("Telegram token boş olamaz.")
        if not chat_id:
            raise ValueError("Telegram chat_id boş olamaz.")
        self._token = token
        self._chat_id = str(chat_id)
        self._poll_interval = max(0.5, poll_interval)
        self._dry_run = dry_run
        self._retry_check_interval = max(1.0, retry_check_interval)

        self._session = self._create_session()
        self._last_request_time: float = 0.0

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._retry_thread: Optional[threading.Thread] = None
        self._offset: int = 0

        self._photo_provider: Optional[Callable[[], Optional[Path]]] = None
        self._video_provider: Optional[Callable[[], Optional[Path]]] = None
        self._status_provider: Optional[Callable[[], str]] = None
        self._screen_capture_provider: Optional[Callable[[], Optional[Path]]] = None
        self._ocr_provider: Optional[Callable[[Path], Optional[str]]] = None
        self._ocr_crop_provider: Optional[Callable[[Path], Path]] = None

        # Retry kuyruğu
        self._pending: deque[_PendingMessage] = deque()
        self._pending_lock = threading.Lock()

    def _create_session(self) -> requests.Session:
        """Yeni bir HTTP session oluşturur."""
        session = requests.Session()
        session.timeout = 30
        # Connection pool ayarları
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5,
            pool_maxsize=10,
            max_retries=0,  # Kendi retry'ımızı kullanıyoruz
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _recreate_session(self) -> None:
        """Mevcut session'ı kapatıp yenisini oluşturur."""
        try:
            self._session.close()
        except Exception:
            pass
        self._session = self._create_session()
        logger.info("Telegram HTTP session yenilendi.")

    @property
    def chat_id(self) -> str:
        return self._chat_id

    def set_photo_provider(self, provider: Callable[[], Optional[Path]]) -> None:
        self._photo_provider = provider

    def set_video_provider(self, provider: Callable[[], Optional[Path]]) -> None:
        self._video_provider = provider

    def set_status_provider(self, provider: Callable[[], str]) -> None:
        self._status_provider = provider

    def set_screen_capture_provider(self, provider: Callable[[], Optional[Path]]) -> None:
        self._screen_capture_provider = provider

    def set_ocr_provider(self, provider: Callable[[Path], Optional[str]]) -> None:
        self._ocr_provider = provider

    def set_ocr_crop_provider(self, provider: Callable[[Path], Path]) -> None:
        self._ocr_crop_provider = provider

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="TelegramPoller", daemon=True
        )
        self._thread.start()

        self._retry_thread = threading.Thread(
            target=self._retry_loop, name="TelegramRetry", daemon=True
        )
        self._retry_thread.start()
        logger.info("Telegram polling + retry başlatıldı (chat_id=%s)", self._chat_id)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._retry_thread is not None:
            self._retry_thread.join(timeout=timeout)
        try:
            self._session.close()
        except Exception:
            pass

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                logger.exception("Telegram poll hatası: %s", exc)
                self._recreate_session()
            self._stop_event.wait(self._poll_interval)
        logger.info("Telegram polling durduruldu.")

    def _retry_loop(self) -> None:
        """Kuyruktaki gönderilmemiş mesajları periyodik olarak dener."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self._retry_check_interval)
            if self._stop_event.is_set():
                break
            self._flush_pending()

    def _flush_pending(self) -> None:
        """Kuyruktaki mesajları göndermeyi dener."""
        with self._pending_lock:
            if not self._pending:
                return
            # Kuyruğun başını al, gönder, başarısızsa başa koy
            item = self._pending[0]

        if item.retry_count >= self.MAX_RETRIES:
            logger.warning("Maksimum retry aşıldı, mesaj atılıyor: %s", item.text[:50])
            with self._pending_lock:
                self._pending.popleft()
            return

        # Backoff hesapla
        backoff = min(
            self.BACKOFF_BASE * (2 ** item.retry_count),
            self.BACKOFF_MAX,
        )
        elapsed = time.monotonic() - item.created_at
        if elapsed < backoff:
            return  # Henüz zamanı gelmedi

        sent = False
        try:
            if item.msg_type == "message":
                sent = self._send_message_raw(item.text)
            elif item.msg_type == "photo" and item.file_path is not None:
                sent = self._send_photo_raw(item.file_path, item.caption)
            elif item.msg_type == "video" and item.file_path is not None:
                sent = self._send_video_raw(item.file_path, item.caption)
        except Exception as exc:
            logger.warning("Retry hatası: %s", exc)
            self._recreate_session()

        if sent:
            with self._pending_lock:
                self._pending.popleft()
            # Geçici dosyayı temizle
            if item.file_path is not None:
                try:
                    item.file_path.unlink(missing_ok=True)
                except OSError:
                    pass
            logger.info("Kuyruk mesajı gönderildi (retry #%d): %s",
                        item.retry_count, item.text[:50])
        else:
            with self._pending_lock:
                item.retry_count += 1
            logger.debug("Retry kuyruğunda bekliyor (#%d): %s",
                         item.retry_count, item.text[:50])

    def _add_to_pending(self, msg_type: str, text: str = "",
                        file_path: Optional[Path] = None,
                        caption: str = "") -> None:
        """Kuyruğa mesaj ekler."""
        with self._pending_lock:
            self._pending.append(_PendingMessage(
                msg_type=msg_type,
                text=text,
                file_path=file_path,
                caption=caption,
            ))
        logger.info("Kuyruğa eklendi (%s): %s", msg_type, text[:50])

    @property
    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    def _poll_once(self) -> None:
        url = f"{self.BASE_URL}/bot{self._token}/getUpdates"
        params = {"timeout": 0, "offset": self._offset, "allowed_updates": '["message"]'}
        if self._dry_run:
            return

        try:
            response = self._session.get(url, params=params, timeout=10)
            self._last_request_time = time.monotonic()
        except requests.RequestException as exc:
            logger.warning("Telegram getUpdates hatası: %s", exc)
            self._recreate_session()
            return

        if response.status_code != 200:
            logger.warning("Telegram getUpdates HTTP %s: %s",
                           response.status_code, response.text[:200])
            if response.status_code in (401, 403, 409):
                # Token geçersiz veya ban, yeniden deneme
                self._recreate_session()
            return

        payload = response.json()
        if not payload.get("ok"):
            logger.warning("Telegram getUpdates !ok: %s", payload)
            return

        for update in payload.get("result", []):
            update_id = update.get("update_id")
            if update_id is not None:
                self._offset = max(self._offset, update_id + 1)
            self._handle_update(update)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id"))
        text = (message.get("text") or "").strip()
        if not text:
            return

        if str(chat_id) != str(self._chat_id):
            logger.debug("Yabancı sohbetten mesaj geldi, yoksayıldı: %s", chat_id)
            return

        command = text.split()[0].upper()
        if command == "FOTO":
            self._handle_foto()
        elif command == "VIDEO":
            self._handle_video()
        elif command == "DURUM":
            self._handle_durum()
        elif command == "EKRAN":
            self._handle_ekran()
        elif command == "EKRAN_OCR":
            self._handle_ekran_ocr()
        else:
            logger.debug("Bilinmeyen komut yoksayıldı: %s", command)

    def _handle_foto(self) -> None:
        if self._photo_provider is None:
            self.send_message("Kamera yapılandırılmamış.")
            return
        try:
            photo_path = self._photo_provider()
        except Exception as exc:
            logger.exception("FOTO sağlayıcı hatası: %s", exc)
            self.send_message("Kamera hatası: %s" % exc)
            return
        if photo_path is None:
            self.send_message("Kamera bağlı değil veya görüntü alınamadı.")
            return
        caption = "📷 Anlık Kamera Görüntüsü"
        sent = self.send_photo(photo_path, caption=caption)
        if sent:
            try:
                photo_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Geçici fotoğraf silinemedi: %s", exc)

    def _handle_video(self) -> None:
        if self._video_provider is None:
            self.send_message("Video özelliği yapılandırılmamış.")
            return
        try:
            video_path = self._video_provider()
        except Exception as exc:
            logger.exception("VIDEO sağlayıcı hatası: %s", exc)
            self.send_message("Video hatası: %s" % exc)
            return
        if video_path is None:
            self.send_message("Kamera bağlı değil veya video alınamadı.")
            return
        caption = "🎥 Anlık Kamera Görüntüsü (5sn)"
        sent = self.send_video(video_path, caption=caption)
        if sent:
            try:
                video_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Geçici video silinemedi: %s", exc)

    def _handle_durum(self) -> None:
        if self._status_provider is None:
            self.send_message("Durum sağlayıcısı tanımlı değil.")
            return
        try:
            text = self._status_provider()
        except Exception as exc:
            logger.exception("DURUM sağlayıcı hatası: %s", exc)
            self.send_message("Durum alınamadı: %s" % exc)
            return
        self.send_message(text)

    def _handle_ekran(self) -> None:
        if self._screen_capture_provider is None:
            self.send_message("Ekran görüntüsü özelliği yapılandırılmamış.")
            return
        try:
            photo_path = self._screen_capture_provider()
        except Exception as exc:
            logger.exception("EKRAN sağlayıcı hatası: %s", exc)
            self.send_message("Ekran görüntüsü hatası: %s" % exc)
            return
        if photo_path is None:
            self.send_message("Ekran görüntüsü alınamadı.")
            return
        caption = "🖥️ CypCut Ekran Görüntüsü"
        sent = self.send_photo(photo_path, caption=caption)
        if sent:
            try:
                photo_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Geçici ekran görüntüsü silinemedi: %s", exc)

    def _handle_ekran_ocr(self) -> None:
        if self._screen_capture_provider is None:
            self.send_message("Ekran görüntüsü özelliği yapılandırılmamış.")
            return
        if self._ocr_provider is None:
            self.send_message("OCR özelliği yapılandırılmamış.")
            return
        if self._ocr_crop_provider is None:
            self.send_message("OCR kırpma özelliği yapılandırılmamış.")
            return

        # 1) Ekran görüntüsü al
        try:
            photo_path = self._screen_capture_provider()
        except Exception as exc:
            logger.exception("EKRAN_OCR ekran görüntüsü hatası: %s", exc)
            self.send_message("Ekran görüntüsü hatası: %s" % exc)
            return
        if photo_path is None:
            self.send_message("Ekran görüntüsü alınamadı.")
            return

        # 2) Görüntüyü kırp
        try:
            cropped_path = self._ocr_crop_provider(photo_path)
        except Exception as exc:
            logger.exception("EKRAN_OCR kırpma hatası: %s", exc)
            self.send_message("Görüntü kırpma hatası: %s" % exc)
            try:
                photo_path.unlink(missing_ok=True)
            except OSError:
                pass
            return

        # 3) Kırpılmış görüntüyü gönder
        self.send_photo(cropped_path, caption="🔍 Kırpılmış Ekran Görüntüsü")

        # 4) OCR yap
        try:
            ocr_text = self._ocr_provider(cropped_path)
        except Exception as exc:
            logger.exception("EKRAN_OCR OCR hatası: %s", exc)
            self.send_message("OCR hatası: %s" % exc)
            self._cleanup_ocr_files(photo_path, cropped_path)
            return

        # 5) OCR sonucunu gönder
        if ocr_text is None:
            self.send_message("OCR başarısız oldu veya sonuç alınamadı.")
        elif not ocr_text.strip():
            self.send_message("🔍 OCR Sonucu\n\nMetin bulunamadı.")
        else:
            self.send_message("🔍 OCR Sonucu\n\n" + ocr_text)

        self._cleanup_ocr_files(photo_path, cropped_path)

    def _cleanup_ocr_files(self, original: Path, cropped: Path) -> None:
        """OCR işleminden sonra geçici dosyaları temizler."""
        for p in (original, cropped):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def send_message(self, text: str) -> bool:
        """Kanal/grup'a metin mesajı gönderir. Başarısızsa kuyruğa ekler."""
        if self._dry_run:
            logger.info("[DRY_RUN] send_message: %s", text)
            return True

        sent = self._send_message_raw(text)
        if not sent:
            self._add_to_pending("message", text=text)
        return sent

    def _send_message_raw(self, text: str) -> bool:
        url = f"{self.BASE_URL}/bot{self._token}/sendMessage"
        try:
            response = self._session.post(
                url,
                json={"chat_id": self._chat_id, "text": text},
                timeout=15,
            )
            self._last_request_time = time.monotonic()
        except requests.RequestException as exc:
            logger.warning("send_message hatası: %s", exc)
            return False

        if response.status_code != 200:
            logger.warning("send_message HTTP %s: %s",
                           response.status_code, response.text[:200])
            return False
        return True

    def send_photo(self, photo_path: Path, caption: str = "") -> bool:
        """Kanal/grup'a JPEG fotoğraf gönderir. Başarısızsa kuyruğa ekler."""
        if self._dry_run:
            logger.info("[DRY_RUN] send_photo: %s caption=%s", photo_path, caption)
            return True

        sent = self._send_photo_raw(photo_path, caption)
        if not sent:
            self._add_to_pending("photo", text=caption,
                                 file_path=photo_path, caption=caption)
        return sent

    def _send_photo_raw(self, photo_path: Path, caption: str = "") -> bool:
        url = f"{self.BASE_URL}/bot{self._token}/sendPhoto"
        try:
            with photo_path.open("rb") as fh:
                response = self._session.post(
                    url,
                    data={"chat_id": self._chat_id, "caption": caption},
                    files={"photo": (photo_path.name, fh, "image/jpeg")},
                    timeout=30,
                )
            self._last_request_time = time.monotonic()
        except (OSError, requests.RequestException) as exc:
            logger.warning("send_photo hatası: %s", exc)
            return False

        if response.status_code != 200:
            logger.warning("send_photo HTTP %s: %s",
                           response.status_code, response.text[:200])
            return False
        return True

    def send_video(self, video_path: Path, caption: str = "") -> bool:
        """Kanal/grup'a video gönderir. Başarısızsa kuyruğa ekler."""
        if self._dry_run:
            logger.info("[DRY_RUN] send_video: %s caption=%s", video_path, caption)
            return True

        sent = self._send_video_raw(video_path, caption)
        if not sent:
            self._add_to_pending("video", text=caption,
                                 file_path=video_path, caption=caption)
        return sent

    def _send_video_raw(self, video_path: Path, caption: str = "") -> bool:
        url = f"{self.BASE_URL}/bot{self._token}/sendVideo"
        try:
            with video_path.open("rb") as fh:
                response = self._session.post(
                    url,
                    data={"chat_id": self._chat_id, "caption": caption},
                    files={"video": (video_path.name, fh, "video/mp4")},
                    timeout=60,
                )
            self._last_request_time = time.monotonic()
        except (OSError, requests.RequestException) as exc:
            logger.warning("send_video hatası: %s", exc)
            return False

        if response.status_code != 200:
            logger.warning("send_video HTTP %s: %s",
                           response.status_code, response.text[:200])
            return False
        return True


__all__ = ["TelegramClient"]
