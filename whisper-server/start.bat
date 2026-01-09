@echo off
chcp 65001 >nul
echo ========================================
echo YouTube Whisper Subtitle Service Startup
echo ========================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

:: Check Virtual Environment
if not exist "venv" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

:: Activate Virtual Environment
call venv\Scripts\activate.bat

:: Check Dependencies
echo [INFO] Checking dependencies...
python -m pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

:: Check ffmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] ffmpeg not found. Audio processing may fail.
    echo [TIP] Please download from https://ffmpeg.org/download.html and add to PATH.
    echo.
)

echo.
echo [INFO] Starting service...
echo [INFO] Service Address: http://127.0.0.1:8765
echo [INFO] API Docs: http://127.0.0.1:8765/docs
echo.
echo Press Ctrl+C to stop service
echo ========================================
echo.

python server.py

pause
