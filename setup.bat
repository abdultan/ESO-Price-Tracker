@echo off
title ESO Price Tracker - Kurulum
echo ==========================================
echo ESO Price Tracker Otomatik Kurulum
echo ==========================================
echo.

:: Python kontrolü
echo Python kontrol ediliyor...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python bulunamadi. Indiriliyor...
    call scripts\install_python.bat
    if %errorlevel% neq 0 (
        echo Python kurulumu basarisiz!
        pause
        exit /b 1
    )
)

:: Sanal ortam oluştur
echo Sanal ortam olusturuluyor...
python -m venv venv
call venv\Scripts\activate.bat

:: Kütüphaneleri yükle
echo Gerekli kutuphaneler yukleniyor...
pip install --upgrade pip
pip install -r requirements.txt

:: Playwright browser yükle
echo Tarayici yukleniyor (bu biraz zaman alabilir)...
playwright install chromium

:: Yapılandırma dosyası oluştur
echo Yapilandirma dosyasi olusturuluyor...
if not exist .env (
    copy .env.example .env
    echo.
    echo ONEMLI: .env dosyasini acip BOT_TOKEN ekleyin!
    echo BotFather'dan aldığınız token'i yazin.
    echo.
)

echo.
echo ==========================================
echo Kurulum tamamlandi!
echo 1. .env dosyasini duzenleyin
echo 2. run.bat ile botu baslatin
echo ==========================================
pause