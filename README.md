# CypCut-alarm

> Friendess CypCut lazer kesim makinelerinin log dosyalarını gerçek zamanlı
> izleyerek alarm, duruş ve devam olaylarını Telegram kanalına bildiren,
> USB kamera ile olay anında fotoğraf çeken, 7/24 çalışmaya uygun servis.

---

## Özellikler

- **Gerçek zamanlı log izleme:** `CypCut-YYYYMMDDHHMMSS-XXXX-1.rtf` dosyalarını tail modunda izler, dosya rotasyonlarını otomatik algılar.
- **RTF temizleme:** `striprtf` ile log satırlarını düz metne çevirir; bozuk CJK unicode karakterlerini temizler. **Log dosyaları asla düzenlenmez.**
- **Durum makinesi:** `IDLE`, `WORKING`, `PAUSED`, `ALARM` durumlarını thread-safe şekilde yönetir.
- **Telegram bildirimleri:** Alarm/duruş/devam olaylarını formatlı mesajlarla kanala gönderir. FOTO ve DURUM komutlarını destekler.
- **USB kamera:** Sistem başlangıcında otomatik kamera taraması, alarm/duruş anında otomatik JPEG çekimi.
- **Kalıcılık:** SQLite ile tüm olaylar saklanır; servis yeniden başlatıldığında kaldığı yerden devam eder.
- **Spam koruması:** Aynı alarm metni için yapılandırılabilir cooldown süresi (varsayılan 5 dk).
- **Düşük kaynak tüketimi:** Tail modu + olay-driven mimari; gereksiz polling yok.

---

## Mimari

```
┌─────────────────────────────────────────────────────────────────┐
│                      MonitorService (main)                      │
└─────────────┬──────────────────────┬─────────────────┬───────────┘
              │                      │                 │
   ┌──────────▼──────────┐ ┌─────────▼─────────┐ ┌─────▼──────┐
   │   LogTailReader     │ │  TelegramClient   │ │  Camera    │
   │   (tail thread)     │ │  (poll thread)    │ │  (cv2)     │
   └──────────┬──────────┘ └───────────────────┘ └────────────┘
              │ new line
              ▼
   ┌────────────────────┐         ┌──────────────────┐
   │   EventParser      │ ──────► │ MachineState     │
   │   (regex)          │ event   │ (state machine)  │
   └────────────────────┘         └─────────┬────────┘
                                            │ transition
                                            ▼
                                  ┌─────────────────────┐
                                  │  Cooldown + DB save │
                                  │  + Telegram notify  │
                                  └─────────────────────┘
```

| Modül | Sorumluluk |
|---|---|
| `src/config.py` | `.env`'den yapılandırma yükler |
| `src/logging_setup.py` | Döngüsel dosya loglama |
| `src/domain/enums.py` | `MachineState`, `EventKind` enum'ları |
| `src/domain/events.py` | `ParsedEvent` dataclass |
| `src/domain/machine_state.py` | Thread-safe state machine |
| `src/infrastructure/rtf_cleaner.py` | `striprtf` sarmalayıcı |
| `src/infrastructure/log_finder.py` | En güncel log dosyasını bul |
| `src/infrastructure/log_tail_reader.py` | Tail modunda okuma |
| `src/infrastructure/log_directory_watcher.py` | watchdog ile yeni dosya |
| `src/infrastructure/event_parser.py` | Satır → `ParsedEvent` |
| `src/infrastructure/database.py` | SQLite bağlantı yönetimi |
| `src/infrastructure/repositories.py` | Repository katmanı |
| `src/infrastructure/telegram_client.py` | Telegram polling bot |
| `src/infrastructure/camera_manager.py` | OpenCV kamera tespiti |
| `src/infrastructure/photo_service.py` | JPEG çekimi |
| `src/services/monitor_service.py` | Orkestratör |

---

## Kurulum (Windows 10)

### 1) Bağımlılıklar

```cmd
install.bat
```

Bu komut:
- `.venv` sanal ortamını oluşturur
- `requirements.txt`'teki paketleri kurar
- `.env.example` → `.env` kopyalar

### 2) `.env` Dosyası

```env
MACHINE_NAME=Lazer-1
LOG_DIR=C:\Program Files (x86)\Friendess\Share\fsdc\log\LogFiles
TAIL_POLL_INTERVAL=0.2

TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_POLL_INTERVAL=2.0
DRY_RUN=false

ALARM_COOLDOWN_SECONDS=300
CAMERA_INDEX=
CAMERA_SCAN_MAX_INDEX=4

DB_PATH=data\cypcut_monitor.db
APP_LOG_PATH=logs\application.log
APP_LOG_LEVEL=INFO
```

> **TELEGRAM_CHAT_ID** bir **kanal** veya **grup** olmalı; bot o kanala/grup'a
> eklenmiş olmalıdır. FOTO ve DURUM komutlarını aynı sohbetteki herkes
> gönderebilir.

### 3) İlk Çalıştırma (test)

```cmd
start.bat
```

Konsolda:
- `📷 Kamera bulundu (Index: 0)` veya `⚠️ Kamera bulunamadı`
- `Aktif log dosyası: ...CypCut-XXXXXX-XXXX-1.rtf`

görünmelidir.

### 4) Windows Başlangıcında Otomatik Başlatma

1. `Win + R` → `shell:startup` → Enter
2. Açılan klasöre `start_hidden.vbs`'in **kısayolunu** kopyalayın
3. Bilgisayar yeniden başladığında konsol penceresi açılmadan servis çalışır

---

## Geliştirme

### Manuel test

```cmd
python tests\test_parser_manual.py
python tests\test_integration.py
python tests\test_e2e.py
```

`test_e2e.py` gerçek bir CypCut oturumunu simüle eder (DRY_RUN modunda).

### DRY_RUN

`.env`'de `DRY_RUN=true` ayarlayın → Telegram HTTP çağrıları atlanır, sadece loglanır.

### Mimari Prensipler

- **OOP + SOLID:** Her modül tek sorumluluk üstlenir.
- **Type hints:** Tüm fonksiyonlarda tam tip açıklaması.
- **Thread safety:** `threading.Lock` ile korunan state.
- **Idempotent schema:** SQLite şeması her başlangıçta güvenle uygulanır.
- **Graceful shutdown:** SIGINT/SIGTERM'de tüm thread'ler düzgün kapanır.

---

## SQLite Şema

`schema.sql` dosyasında tanımlıdır, ilk çalıştırmada otomatik uygulanır.

| Tablo | Amaç |
|---|---|
| `state` | Mevcut durum + son olay (tek satır) |
| `alarm_events` | Tüm alarm olayları (id, metin, zaman, telegram_sent) |
| `state_transitions` | Tüm durum geçişleri |
| `cooldowns` | Alarm cooldown anahtarları |

---

## Tespit Edilen Olay Türleri

| Olay | Tetikleyici Satır | Durum |
|---|---|---|
| Çalışma başlangıcı | `Start Processing`, `Stop --> Working`, `Resume --> Working` | → WORKING |
| Duruş | `Working --> Pause`, `Pause` | → PAUSED |
| Devam | `Resume`, `Pause --> Resume` | → WORKING |
| Alarm | `Alarm:BCS100 ...` | → ALARM |
| Alarm temizleme | `Alarm Remove:BCS100 ...` | → PAUSED |

---

## Telegram Mesaj Formatları

```
🚨 Lazer Alarmı
Makine: Lazer-1
Saat: 08:51:16
Alarm:
BCS100 Follow Error
```

```
⏸️ Makine Durdu
Makine: Lazer-1
Saat: 08:51:17
Durum:
Working --> Pause
```

```
▶️ Makine Devam Etti
Makine: Lazer-1
Saat: 10:13:49
Durum:
Resume --> Working
```

```
✅ Alarm Temizlendi
Makine: Lazer-1
Saat: 08:52:00
Alarm: BCS100 Follow Error
```

---

## Lisans

Dahili kullanım.
