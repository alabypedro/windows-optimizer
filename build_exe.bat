@echo off
rem Gera dist\WindowsOptimizer\WindowsOptimizer.exe com PyInstaller.
rem O .exe NAO pede Administrador ao abrir (asInvoker); ele oferece elevar quando necessario.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller psutil customtkinter || goto :error

python -m PyInstaller --noconfirm --clean --windowed --onedir ^
    --name WindowsOptimizer ^
    --collect-data customtkinter ^
    --hidden-import psutil ^
    main.py || goto :error

echo.
echo Pronto: dist\WindowsOptimizer\WindowsOptimizer.exe
echo Os logs e backups ficam em dist\WindowsOptimizer\logs e \backups
echo (ou em %%LOCALAPPDATA%%\WindowsOptimizer se a pasta nao for gravavel).
pause
exit /b 0

:error
echo Falha ao gerar o executavel.
pause
exit /b 1
