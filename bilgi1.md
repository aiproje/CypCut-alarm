Evet — ama “log” derken 3 farklı katman var ve hangisini görmek istediğine göre yöntem değişiyor.

Senin sistemde (fsdc.exe + 9527/9528) **gerçek logları görmenin 3 yolu var:**

---

# 🧠 1. WebSocket logları (EN DEĞERLİ)

Zaten sistem burada realtime event gönderiyor:

```
ws://localhost:9528/fsdcWs/
```

## 🔥 Python ile FULL LOG CATCHER

```python id="wslog1"
import asyncio
import websockets
import json
from datetime import datetime

async def listen():
    uri = "ws://localhost:9528/fsdcWs/"

    async with websockets.connect(uri, subprotocols=["web_taskclient"]) as ws:
        print("CONNECTED")

        while True:
            msg = await ws.recv()
            try:
                data = json.loads(msg)
            except:
                data = msg

            print(f"[{datetime.now()}] {data}")

asyncio.run(listen())
```

👉 Bu sana:

* TaskStart
* TaskFinished
* Refresh
* Progress updates

hepsini verir.

---

# 🧠 2. HTTP API log yok (ama trace yapılır)

Bu sistem:

```
http://localhost:9527/web/taskclient/...
```

👉 Normalde log endpoint yok.

Ama şunu yapabilirsin:

## 🔥 MITM-style request log (proxy)

### Fiddler / Charles / Burp:

* localhost:9527 intercept
* tüm API request/response görürsün

---

# 🧠 3. Windows process log (fsdc.exe iç logları)

Bu servis genelde log basar ama dosyaya yazıyorsa:

## 🔍 kontrol et:

```powershell id="log1"
Get-ChildItem C:\ -Recurse -Include *.log -ErrorAction SilentlyContinue | findstr fsdc
```

veya:

```powershell id="log2"
Get-Process fsdc | Select-Object Path
```

sonra o klasöre bak:

* logs/
* log/
* debug/
* runtime/

---

# 🧠 4. En güçlü yöntem: WS + HTTP FULL RECORD

Ben sana “tam logger” veriyorum:

## 🔥 FULL SYSTEM LOGGER (WS + HTTP birlikte)

```python id="full1"
import asyncio
import websockets
import json
import requests
import threading
from datetime import datetime

BASE = "http://localhost:9527"

def http_poll():
    while True:
        try:
            r = requests.post(BASE + "/web/taskclient/plan/list", json={})
            print("[HTTP]", r.text)
        except Exception as e:
            print("HTTP error:", e)

async def ws():
    uri = "ws://localhost:9528/fsdcWs/"

    async with websockets.connect(uri, subprotocols=["web_taskclient"]) as ws:
        print("WS CONNECTED")

        while True:
            msg = await ws.recv()
            try:
                data = json.loads(msg)
            except:
                data = msg

            print("[WS]", data)

threading.Thread(target=http_poll, daemon=True).start()
asyncio.run(ws())
```

---

# ⚠️ GERÇEK DURUM

Bu sistemde:

### ✔ WS = realtime log stream

### ✔ HTTP = state snapshot

### ❌ classic log file = genelde yok veya gizli

---

# 🚀 İstersen daha ileri seviye

Şunları da yapabilirim:

### 🔥 1. WS event replay (kaydedip tekrar oynatma)

### 🔥 2. Task lifecycle graph çıkarma

### 🔥 3. API hook interceptor (tam debug mode)

### 🔥 4. fsdc.exe internal protocol decode

Sadece söyle:
👉 “replay yap”
👉 veya “deep debug aç”

direkt sistemi reverse engineer seviyesine çıkarırım.
