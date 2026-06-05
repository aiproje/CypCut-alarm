@echo off
REM ============================================================
REM  İlk kurulum: sanal ortam + bağımlılıklar
REM ============================================================

setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

where python >nul 2>nul
if errorlevel 1 (
    echo [HATA] Python bulunamadi. Lutfen Python 3.11+ yukleyin.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [BILGI] Sanal ortam olusturuluyor...
    python -m venv .venv
)

echo [BILGI] Bagimliliklar yukleniyor...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [HATA] Bagimliliklar yuklenemedi.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [BILGI] .env.example -> .env kopyalaniyor
    copy /Y ".env.example" ".env"
    echo.
    echo  Lutfen .env dosyasini duzenleyin:
    echo    - TELEGRAM_BOT_TOKEN
    echo    - TELEGRAM_CHAT_ID
    echo.
    pause
)

echo.
echo Kurulum tamam. Servisi baslatmak icin start.bat calistirin.
pause
endlocal
