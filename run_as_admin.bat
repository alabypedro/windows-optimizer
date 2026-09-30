@echo off
rem Abre o Windows Optimizer como Administrador (o Windows mostra o aviso do UAC).
rem Nada e alterado automaticamente: toda operacao continua exigindo confirmacao.
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel%==0 goto :run

echo Solicitando privilegios de Administrador...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw main.py
) else (
    python main.py
    if errorlevel 1 pause
)
