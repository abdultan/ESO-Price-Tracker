@echo off
title ESO Price Tracker - Bot
cd /d "%~dp0"

:: Sanal ortamı aktive et
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo Kurulum yapilmamis! Once setup.bat calistirin.
    pause
    exit /b 1
)

:: .env dosyası kontrolü
if not exist .env (
    echo .env dosyasi bulunamadi!
    echo setup.bat calistirin ve BOT_TOKEN ekleyin.
    pause
    exit /b 1
)

:: Bot'u çalıştır
echo ESO Price Tracker baslatiliyor...
echo Durdurmak icin Ctrl+C basin
echo.
python main.py

:: Hata durumunda bekle
if %errorlevel% neq 0 (
    echo.
    echo Bot hata ile kapandi!
    pause
)