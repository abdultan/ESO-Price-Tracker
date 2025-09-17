@echo off
title ESO Price Tracker - Guncelleme
cd /d "%~dp0"

echo Guncellemeler kontrol ediliyor...
git pull origin main

if %errorlevel% neq 0 (
    echo Git bulunamadi veya guncelleme basarisiz.
    echo Manuel olarak GitHub'dan yeni dosyalari indirin.
    pause
    exit /b 1
)

:: Sanal ortamı aktive et ve kütüphaneleri güncelle
call venv\Scripts\activate.bat
pip install -r requirements.txt --upgrade

echo Guncelleme tamamlandi!
pause