#!/usr/bin/env bash
# Linux/macOS geliştirme ortamı için yardımcı script.
# Windows ortamında install.bat kullanılır.

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Lütfen .env dosyasını düzenleyin."
fi

python -m src.main
