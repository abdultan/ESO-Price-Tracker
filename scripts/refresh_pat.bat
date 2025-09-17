@echo off
:: PATH cevre degiskenini yenile
echo PATH yenileniyor...

:: Registry'den PATH'i oku ve uygula
for /f "usebackq tokens=2,*" %%A in (`reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH`) do set "SysPath=%%B"
for /f "usebackq tokens=2,*" %%A in (`reg query "HKEY_CURRENT_USER\Environment" /v PATH`) do set "UserPath=%%B"

set "PATH=%SysPath%;%UserPath%"

echo PATH yenilendi.