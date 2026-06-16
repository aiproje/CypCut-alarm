from __future__ import annotations

import io
import socket
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Optional

import cv2
import numpy as np

from ..logging_setup import get_logger

logger = get_logger(__name__)

_HTML_PAGE = """\
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>CypCut Kamera Yayını</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #000; display: flex; flex-direction: column; align-items: center;
       justify-content: center; min-height: 100vh; font-family: Arial, sans-serif; }
.stream-container { width: 100%%; max-width: 960px; padding: 10px; }
.stream-container img { width: 100%%; height: auto; border-radius: 4px;
                        box-shadow: 0 0 20px rgba(0,150,255,0.3); }
.status { color: #0a0; text-align: center; margin-top: 12px; font-size: 14px; }
.status.offline { color: #a00; }
h1 { color: #fff; text-align: center; font-size: 18px; margin-bottom: 10px;
     font-weight: normal; opacity: 0.8; }
</style>
</head>
<body>
<h1>CypCut Kamera Yayını</h1>
<div class="stream-container">
<img id="stream" src="/stream.mjpg" alt="Kamera Yayını">
<div class="status" id="status">Yayın aktif</div>
</div>
<script>
var img = document.getElementById('stream');
var statusEl = document.getElementById('status');
img.onerror = function() {
    statusEl.className = 'status offline';
    statusEl.textContent = 'Yayın kesildi, yeniden bağlanılıyor...';
    setTimeout(function() {
        img.src = '/stream.mjpg?' + new Date().getTime();
        statusEl.className = 'status';
        statusEl.textContent = 'Yeniden bağlanıyor...';
    }, 3000);
};
img.onload = function() {
    statusEl.className = 'status';
    statusEl.textContent = 'Yayın aktif';
};
</script>
</body>
</html>
"""


class _StreamHandler(BaseHTTPRequestHandler):
    _server_ref: Optional['StreamServer'] = None

    def do_GET(self) -> None:
        if self.path == '/':
            self._serve_html()
        elif self.path.startswith('/stream.mjpg'):
            self._serve_mjpeg()
        else:
            self.send_error(404)

    def _serve_html(self) -> None:
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(_HTML_PAGE.encode('utf-8'))

    def _serve_mjpeg(self) -> None:
        server = self._server_ref
        if server is None or not server.is_running:
            self.send_error(503, "Stream not available")
            return

        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Connection', 'close')
        self.end_headers()

        last_frame_id = -1
        try:
            while server.is_running:
                frame_data, frame_id = server.get_latest_frame(last_frame_id)
                if frame_data is None:
                    time.sleep(0.05)
                    continue

                last_frame_id = frame_id
                try:
                    self.wfile.write(b'--FRAME\r\n')
                    self.wfile.write(b'Content-Type: image/jpeg\r\n')
                    self.wfile.write(f'Content-Length: {len(frame_data)}\r\n'.encode())
                    self.wfile.write(b'\r\n')
                    self.wfile.write(frame_data)
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
        except Exception:
            pass

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("HTTP: %s", format % args)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class StreamServer:
    """Hafif MJPEG stream sunucusu.

    Kamera karelerini HTTP üzerinden MJPEG olarak yayınlar.
    Sadece stdlib + OpenCV kullanır (ek bağımlılık yok).
    """

    def __init__(
        self,
        host: str = '0.0.0.0',
        port: int = 2373,
        width: int = 640,
        height: int = 480,
        fps: int = 10,
        quality: int = 70,
    ) -> None:
        self._host = host
        self._port = port
        self._width = width
        self._height = height
        self._fps = fps
        self._quality = quality

        self._server: Optional[_ThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._latest_frame: bytes = b''
        self._latest_frame_id: int = 0
        self._frame_lock = threading.Lock()

        self._cap: Optional[cv2.VideoCapture] = None

        _StreamHandler._server_ref = self

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    @property
    def port(self) -> int:
        return self._port

    def start(self, camera_index: int = 0) -> bool:
        """Stream sunucusunu başlatır."""
        if self._server is not None:
            logger.warning("Stream sunucusu zaten çalışıyor.")
            return True

        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            logger.warning("Kamera açılamadı (index: %d), stream başlatılamadı.", camera_index)
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap = cap

        self._stop_event.clear()

        try:
            self._server = _ThreadingHTTPServer((self._host, self._port), _StreamHandler)
            self._server.timeout = 0.5
        except (OSError, socket.error) as exc:
            logger.warning("Stream sunucusu başlatılamadı (port %d): %s", self._port, exc)
            cap.release()
            self._cap = None
            self._server = None
            return False

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="StreamHTTPServer",
            daemon=True,
        )
        self._server_thread.start()

        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="StreamCapture",
            daemon=True,
        )
        self._capture_thread.start()

        logger.info("Stream sunucusu başlatıldı: http://0.0.0.0:%d", self._port)
        return True

    def stop(self) -> None:
        """Stream sunucusunu durdurur."""
        self._stop_event.set()

        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None

        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        if self._server_thread is not None:
            self._server_thread.join(timeout=2.0)
            self._server_thread = None

        if self._capture_thread is not None:
            self._capture_thread.join(timeout=2.0)
            self._capture_thread = None

        logger.info("Stream sunucusu durduruldu.")

    def get_latest_frame(self, last_id: int = -1) -> tuple[Optional[bytes], int]:
        """Son kareyi döndürür. Eğer yeni kare yoksa (None, id) döner."""
        with self._frame_lock:
            if self._latest_frame_id == last_id:
                return None, self._latest_frame_id
            return self._latest_frame, self._latest_frame_id

    def _capture_loop(self) -> None:
        """Kamera karelerini yakalayıp JPEG'e çevirir."""
        cap = self._cap
        if cap is None:
            return

        sleep_time = 1.0 / self._fps
        frame_id = 0

        while not self._stop_event.is_set():
            try:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(sleep_time)
                    continue

                frame_id += 1

                if self._width and self._height:
                    h, w = frame.shape[:2]
                    if w != self._width or h != self._height:
                        frame = cv2.resize(frame, (self._width, self._height),
                                           interpolation=cv2.INTER_NEAREST)

                _, jpeg_data = cv2.imencode(
                    '.jpg',
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, self._quality],
                )

                with self._frame_lock:
                    self._latest_frame = jpeg_data.tobytes()
                    self._latest_frame_id = frame_id

            except Exception as exc:
                logger.warning("Stream capture hatası: %s", exc)

            self._stop_event.wait(sleep_time)

    def get_stream_url(self, local_ip: Optional[str] = None) -> str:
        """Stream URL'sini döndürür. Eğer local_ip verilmezse otomatik bulur."""
        if local_ip:
            ip = local_ip
        else:
            ip = self._get_local_ip()
        return f"http://{ip}:{self._port}"

    @staticmethod
    def _get_local_ip() -> str:
        """Makinenin yerel IP adresini bulur."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            try:
                s.connect(('10.254.254.254', 1))
                ip = s.getsockname()[0]
            except Exception:
                ip = '127.0.0.1'
            finally:
                s.close()
            return ip
        except Exception:
            return '127.0.0.1'


__all__ = ["StreamServer"]
