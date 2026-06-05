@echo off
REM ============================================================
REM  CypCut Monitor - Windows başlatma scripti
REM
REM  Kullanım:
REM    1) .env.example dosyasını .env olarak kopyalayıp düzenleyin
REM    2) İlk kurulumda: install.bat çalıştırın (sanal ortam + bağımlılıklar)
REM    3) Bu dosyayı Windows başlangıç klasörüne kısayol olarak ekleyin
REM       (Win+R -> shell:startup)
REM ============================================================

setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM Python yorumlayıcı (sistem PATH'inden)
where python >nul 2>nul
if errorlevel 1 (
    echo [HATA] Python bulunamadi. Lutfen Python 3.11+ yukleyin.
    pause
    exit /b 1
)

REM Sanal ortam oluştur (yoksa)
if not exist ".venv\Scripts\python.exe" (
    echo [BILGI] Sanal ortam olusturuluyor...
    python -m venv .venv
    if errorlevel 1 (
        echo [HATA] Sanal ortam olusturulamadi.
        pause
        exit /b 1
    )
)

REM Bağımlılıklar
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt >nul 2>nul
if errorlevel 1 (
    echo [HATA] Bagimliliklar yuklenemedi.
    pause
    exit /b 1
)

REM .env kontrolü
if not exist ".env" (
    echo [UYARI] .env dosyasi bulunamadi. .env.example kopyalaniyor...
    copy /Y ".env.example" ".env" >nul
    echo.
    echo  Lutfen .env dosyasini duzenleyin ve tekrar calistirin.
    echo  Cikiliyor...
    pause
    exit /b 1
)

REM Servisi başlat
echo.
echo ============================================================
echo  CypCut Monitor baslatiliyor...
echo  Durdurmak icin bu pencereyi kapatin veya Ctrl+C basin.
echo ============================================================
echo.

".venv\Scripts\python.exe" -m src.main

endlocal
