@echo off
:: Echoes light-show editor
:: Double-click to launch.  Python 3.10+ with PySide6 required.
::
:: First-time setup (run once in this folder):
::   python -m pip install --user -r requirements.txt

cd /d "%~dp0"

if not exist "Echoes.pyw" (
    echo [ERROR] Echoes.pyw not found in this folder.
    pause
    exit /b 1
)

where pythonw >nul 2>&1
if %errorlevel% == 0 (
    start "" pythonw "Echoes.pyw"
) else (
    where python >nul 2>&1
    if %errorlevel% == 0 (
        start "" python "Echoes.pyw"
    ) else (
        echo [ERROR] Python not found. Install Python 3.10+ and try again.
        echo         https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
