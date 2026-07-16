@echo off
REM ============================================================
REM  VCE Read Aloud - one-click launcher for Windows.
REM  First run: creates a private Python environment and installs
REM  dependencies (needs internet once). After that it starts
REM  instantly and works offline.
REM ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Please install Python 3.10+ from
    echo https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo First-time setup: creating environment and installing dependencies...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Something went wrong installing dependencies. Check your
        echo internet connection and run start.bat again.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" run.py
if errorlevel 1 pause
endlocal
