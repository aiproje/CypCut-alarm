"""Telegram istemcisi.

Sadece ``requests`` ile çalışan, kendi thread'inde polling yapan minimal bot.
Kanal/grup modunda çalışır; FOTO ve DURUM komutları aynı chat içinde herkes
tarafından kullanılabilir.

Desteklenen komutlar:
  - FOTO: Anlık kamera görüntüsü alıp aynı sohbete yollar.
  - DURUM: Mevcut durum + son 5 alarm + kamera bilgisi.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from ..logging_setup import get_logger

logger = get_logger(__name__)


class TelegramClient:
    """Telegram Bot API istemcisi."""

    BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        token: str,
        chat_id: str,
        poll_interval: float = 2.0,
        dry_run: bool = False,
    ) -> None:
        if not token:
            raise ValueError("Telegram token boş olamaz.")
        if not chat_id:
            raise ValueError("Telegram chat_id boş olamaz.")
        self._token = token
        self._chat_id = str(chat_id)
        self._poll_interval = max(0.5, poll_interval)
        self._dry_run = dry_run

        self._session = requests.Session()
        self._session.timeout = 30

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._offset: int = 0

        self._photo_provider: Optional[Callable[[], Optional[Path]]] = None
        self._status_provider: Optional[Callable[[], str]] = None

    @property
    def chat_id(self) -> str:
        return self._chat_id

    def set_photo_provider(self, provider: Callable[[], Optional[Path]]) -> None:
        """FOTO komutunda çağrılacak fotoğraf sağlayıcısı."""
        self._photo_provider = provider

    def set_status_provider(self, provider: Callable[[], str]) -> None:
        """DURUM komutunda çağrılacak durum sağlayıcısı."""
        self._status_provider = provider

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="TelegramPoller", daemon=True
        )
        self._thread.start()
        logger.info("Telegram polling başlatıldı (chat_id=%s)", self._chat_id)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
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
            self._stop_event.wait(self._poll_interval)
        logger.info("Telegram polling durduruldu.")

    def _poll_once(self) -> None:
        url = f"{self.BASE_URL}/bot{self._token}/getUpdates"
        params = {"timeout": 0, "offset": self._offset, "allowed_updates": '["message"]'}
        if self._dry_run:
            return

        try:
            response = self._session.get(url, params=params, timeout=10)
        except requests.RequestException as exc:
            logger.warning("Telegram getUpdates hatası: %s", exc)
            return

        if response.status_code != 200:
            logger.warning("Telegram getUpdates HTTP %s: %s", response.status_code, response.text[:200])
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
        elif command == "DURUM":
            self._handle_durum()
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
        self.send_photo(photo_path, caption=caption)
        try:
            photo_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Geçici fotoğraf silinemedi: %s", exc)

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

    def send_message(self, text: str) -> bool:
        """Kanal/grup'a metin mesajı gönderir."""
        if self._dry_run:
            logger.info("[DRY_RUN] send_message: %s", text)
            return True

        url = f"{self.BASE_URL}/bot{self._token}/sendMessage"
        try:
            response = self._session.post(
                url,
                json={"chat_id": self._chat_id, "text": text},
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.warning("send_message hatası: %s", exc)
            return False

        if response.status_code != 200:
            logger.warning("send_message HTTP %s: %s", response.status_code, response.text[:200])
            return False
        return True

    def send_photo(self, photo_path: Path, caption: str = "") -> bool:
        """Kanal/grup'a JPEG fotoğraf gönderir."""
        if self._dry_run:
            logger.info("[DRY_RUN] send_photo: %s caption=%s", photo_path, caption)
            return True

        url = f"{self.BASE_URL}/bot{self._token}/sendPhoto"
        try:
            with photo_path.open("rb") as fh:
                response = self._session.post(
                    url,
                    data={"chat_id": self._chat_id, "caption": caption},
                    files={"photo": (photo_path.name, fh, "image/jpeg")},
                    timeout=30,
                )
        except (OSError, requests.RequestException) as exc:
            logger.warning("send_photo hatası: %s", exc)
            return False

        if response.status_code != 200:
            logger.warning("send_photo HTTP %s: %s", response.status_code, response.text[:200])
            return False
        return True


__all__ = ["TelegramClient"]
