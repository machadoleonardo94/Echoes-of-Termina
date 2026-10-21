@echo off
:: Echoes FFT spectrum analyser — standalone window
:: Double-click to launch.  Python 3.10+ with PySide6 required.
::
:: Drag an audio file onto this file (or pass a path) to analyse it straight
:: away; otherwise use the "Open Audio..." button in the window.

cd /d "%~dp0"

if not exist "spectrum.py" (
    echo [ERROR] spectrum.py not found in this folder.
    pause
    exit /b 1
)

where pythonw >nul 2>&1
if %errorlevel% == 0 (
    start "" pythonw "spectrum.py" %*
) else (
    where python >nul 2>&1
    if %errorlevel% == 0 (
        start "" python "spectrum.py" %*
    ) else (
        echo [ERROR] Python not found. Install Python 3.10+ and try again.
        echo         https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
