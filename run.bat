@echo off
rem Abre o Windows Optimizer no modo usuario (recomendado).
rem Operacoes que exigem Administrador oferecem reiniciar o programa pelo UAC.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw main.py
) else (
    python main.py
    if errorlevel 1 pause
)
