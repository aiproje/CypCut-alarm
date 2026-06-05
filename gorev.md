# Görev: CypCut Lazer Kesim Makinesi Alarm ve Duruş İzleme Sistemi

Python ile Windows 10 üzerinde çalışan, düşük kaynak tüketimli, üretim ortamına uygun bir izleme sistemi geliştir.

## Amaç

Friendess CypCut yazılımının oluşturduğu log dosyalarını gerçek zamanlı takip ederek:

* Makinenin çalışmaya başladığını tespit etmek
* Makinenin durduğunu tespit etmek
* Alarm oluştuğunu tespit etmek
* Alarm temizlendiğini tespit etmek
* Telegram kanalına bildirim göndermek

Sistem 7/24 çalışabilecek şekilde kararlı tasarlanmalıdır.

---

## Çalışma Ortamı

İşletim Sistemi:

Windows 10 Pro

Log klasörü:

C:\Program Files (x86)\Friendess\Share\fsdc\log\LogFiles

Dosya formatı:

CypCut-YYYYMMDDHHMMSS-XXXX-1.rtf

Örnek:

CypCut-20260605083823-5408-1.rtf

Her CypCut oturumunda yeni bir log dosyası oluşabilmektedir.

İşlem duraklatıp başlanınca da yeni bir log dosyası oluşabilmektedir. yani sürekli yeni log dosyası var mı izlenmeli.

Sistem her zaman en güncel log dosyasını otomatik bulmalıdır.

örnek log dosyaları ornek_log_dosyalari\ klasörü içinde mevcut proje dizininde.

---

## Teknik Gereksinimler

### Dil

Python 3.11+

### Kullanılabilecek Kütüphaneler

* watchdog
* python-telegram-bot veya requests
* striprtf
* pathlib
* logging
* sqlite3
* threading

Gereksiz bağımlılık kullanılmamalıdır.

Kod Python 3.11 ile tam uyumlu olmalıdır.

Python 3.13 özellikleri kullanılmamalıdır.

---

## Log İzleme

Sistem log dosyasını gerçek zamanlı takip etmelidir.

Yeni satırlar geldikçe işlenmelidir.

Dosyanın tamamı sürekli okunmamalıdır.

tail mantığında çalışmalıdır.

CPU kullanımı minimum olmalıdır.

---

## RTF Temizleme

Log satırları RTF formatındadır.

Örnek:

(06.05 08:51:16)\cf7\b Alarm:BCS100 Follow Error\cf0\b0

İşleme alınmadan önce düz metne çevrilmelidir.

Örnek sonuç:

(06.05 08:51:16) Alarm:BCS100 Follow Error

---

## Durum Takibi

Makinenin anlık durumu bellekte tutulmalıdır.

Olası durumlar:

* IDLE
* WORKING
* PAUSED
* ALARM

---

## Çalışma Başlangıcı Olayları

Aşağıdaki kayıtlar çalışma başlangıcı olarak değerlendirilmelidir:

Start Processing

veya

Stop --> Working

veya

Resume --> Working

Bu durumda durum:

WORKING

olarak güncellenmelidir.

---

## Alarm Olayları

Aşağıdaki ifade geçtiğinde alarm oluşmuştur:

Alarm:

Örnek:

Alarm:BCS100 Follow Error

Alarm içeriği ayrıştırılmalı ve saklanmalıdır.

Durum:

ALARM

olmalıdır.

Telegram bildirimi gönderilmelidir.

---

## Alarm Temizleme

Aşağıdaki ifade geçtiğinde alarm temizlenmiştir:

Alarm Remove:

Telegram bildirimi gönderilebilir ancak zorunlu değildir.

---

## Duruş Tespiti

Aşağıdaki geçişler duruş olarak kabul edilmelidir:

Working --> Pause

veya

Pause

Bu durumda:

PAUSED

durumuna geçilmelidir.

Telegram bildirimi gönderilmelidir.

---

## Devam Etme Tespiti

Aşağıdaki kayıtlar devam etme anlamına gelir:

Resume

Pause --> Resume

Resume --> Working

Bu durumda:

WORKING

durumuna geçilmelidir.

Telegram bildirimi gönderilmelidir.

---

## Spam Koruması

Aynı alarm için sürekli bildirim gönderilmemelidir.

Örnek:

Alarm:BCS100 Follow Error

aynı içerikle 5 dakika içinde tekrar oluşursa bildirim gönderme.

Alarm hash veya alarm metni bazlı kontrol yapılabilir.

---

## Telegram Bildirimleri

Mesaj formatı:

🚨 Lazer Alarmı

Makine: Lazer-1

Saat: 08:51:16

Alarm:
BCS100 Follow Error

---

Makine durduğunda:

⏸️ Makine Durdu

Makine: Lazer-1

Saat: 08:51:17

Durum:
Working --> Pause

---

Makine tekrar çalıştığında:

▶️ Makine Devam Etti

Makine: Lazer-1

Saat: 10:13:49

Durum:
Resume --> Working

---

## Kalıcılık

SQLite kullanılmalıdır.

Aşağıdaki bilgiler saklanmalıdır:

* alarm geçmişi
* duruş geçmişi
* çalışma başlangıçları
* son durum

Program yeniden başlatılırsa kaldığı yerden devam etmelidir.

---

## Log Rotasyonu

Yeni bir CypCut log dosyası oluştuğunda sistem bunu otomatik fark etmelidir.

Eski dosyadan yeni dosyaya otomatik geçmelidir.

Servisin yeniden başlatılması gerekmemelidir.

---

## Loglama

Uygulamanın kendi logları ayrı tutulmalıdır.

Örnek:

logs/application.log

Tüm hatalar kayıt altına alınmalıdır.

---

## Servis Modu

Uygulama arka planda sürekli çalışacak şekilde tasarlanmalıdır.

Windows da başlangıç uygulamaları klasörüne bash dosyasını doyarak scripti her windows başlangıcında başlatacağım.

Kod mimarisi servis uyumlu olmalıdır.

---


## Kamera Entegrasyonu

Sisteme USB kamera desteği eklenmelidir.

Amaç:

* Alarm oluştuğunda otomatik fotoğraf çekmek
* Telegram komutuyla fotoğraf göndermek

---

## Kamera Tespiti

Kamera indeksleri sabit kabul edilmemelidir.

Sistem başlangıcında mevcut kameraları taramalıdır.

Örnek:

0
1
2
3

Kullanılabilir kameralar test edilmelidir.

İlk başarılı görüntü alınabilen kamera otomatik seçilmelidir.

.env dosyasında isteğe bağlı olarak:

CAMERA_INDEX=

tanımlanabilir.

Tanımlıysa öncelikle bu kamera kullanılmalıdır.

Kamera bulunamazsa sistem çalışmaya devam etmeli ancak kamera özellikleri pasif duruma geçmelidir.

Log izleme sistemi hiçbir durumda durmamalıdır.

---

## Alarm Anında Fotoğraf

Aşağıdaki olaylarda otomatik fotoğraf çekilmelidir:

* Alarm:
* Working --> Pause

Fotoğraf çekildikten sonra Telegram mesajına eklenmelidir.

Örnek:

🚨 Lazer Alarmı

Makine: Lazer-1

Saat: 08:51:16

Alarm:
BCS100 Follow Error

📷 Alarm anındaki görüntü ekte.

---

## FOTO Komutu

Telegram mesajı:

FOTO

Yetkili kullanıcı gönderdiğinde:

1. Kameradan anlık görüntü al
2. JPEG olarak kaydet
3. Telegram'a gönder
4. Geçici dosyayı sil

Örnek cevap:

📷 Anlık Kamera Görüntüsü

Makine:
Lazer-1

Saat:
10:45:21

---


## Kamera Sağlık Kontrolü

Sistem başlangıcında:

📷 Kamera bulundu

veya

⚠️ Kamera bulunamadı

mesajı yazmalı terminalde.

---

## DURUM Komutuna Kamera Bilgisi Ekle

DURUM komutu çıktısına:

Kamera:
✅ Bağlı

veya

❌ Bağlı Değil

eklenmelidir.

Ayrıca kullanılan kamera indeksi belirtilmelidir.

Örnek:

Kamera:
✅ Bağlı (Index: 0)



## Kod Kalitesi

* Type hints kullanılmalı
* OOP mimarisi tercih edilmeli
* SOLID prensiplerine mümkün olduğunca uyulmalı
* Config değerleri .env dosyasından okunmalı
* Kod production seviyesinde olmalı
* Gereksiz polling yapılmamalı
* Bellek sızıntısı oluşturmamalı

Teslimat:

* Çalışan kaynak kod
* requirements.txt
* örnek .env
* README.md
* SQLite şema dosyası
