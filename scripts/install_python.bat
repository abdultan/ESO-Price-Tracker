@echo off
echo Python indiriliyor...

:: Python 3.11 indirme URL'si
set PYTHON_URL=https://www.python.org/ftp/python/3.11.6/python-3.11.6-amd64.exe
set PYTHON_FILE=python_installer.exe

:: Python indir
powershell -Command "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_FILE%'"

if not exist %PYTHON_FILE% (
    echo Python indirilemedi!
    exit /b 1
)

:: Sessiz kurulum
echo Python kuruluyor...
%PYTHON_FILE% /quiet InstallAllUsers=1 PrependPath=1 Include_test=0

:: Kurulum dosyasını sil
del %PYTHON_FILE%

:: PATH'i yenile
call refreshenv

echo Python kurulumu tamamlandi.