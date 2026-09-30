@echo off
rem Gera installer_output\WindowsOptimizer-Setup-<versao>.exe com Inno Setup 6.
rem Requer: dist\WindowsOptimizer\ (rode build_exe.bat antes) e Inno Setup 6
rem (winget install JRSoftware.InnoSetup).
cd /d "%~dp0"

if not exist "dist\WindowsOptimizer\WindowsOptimizer.exe" (
    echo dist\WindowsOptimizer\WindowsOptimizer.exe nao encontrado. Rode build_exe.bat primeiro.
    goto :error
)

set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
    echo Inno Setup 6 nao encontrado. Instale com: winget install JRSoftware.InnoSetup
    goto :error
)

rem a versao vem de utils\helpers.py (APP_VERSION), a mesma que o programa mostra
set "APPVER="
for /f "delims=" %%V in ('python -c "import sys; sys.path.insert(0, '.'); from utils.helpers import APP_VERSION; print(APP_VERSION)"') do set "APPVER=%%V"
if not defined APPVER set "APPVER=1.0.0"

"%ISCC%" /DAppVersion=%APPVER% installer.iss || goto :error

echo.
echo Pronto: instalador em installer_output\
pause
exit /b 0

:error
echo Falha ao gerar o instalador.
pause
exit /b 1
