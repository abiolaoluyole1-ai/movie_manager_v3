@echo off
setlocal
cd /d "%~dp0"
title Movie Manager V3

echo.
echo ==========================================
echo           MOVIE MANAGER V3
echo ==========================================
echo.

where py >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    ) else (
        echo Python was not found.
        echo Install Python 3.11 or newer from python.org and tick "Add Python to PATH".
        echo.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo First launch: creating the private Python environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo Could not create the Python environment.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

echo Checking required packages...
python -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Package installation failed. Check your internet connection and try again.
    pause
    exit /b 1
)

if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
    echo.
    echo IMPORTANT:
    echo A new .env file was created.
    echo Open it and add your YouTube Data API key before discovery can run.
    echo.
)

echo Starting Movie Manager...
echo Keep this window open while using the dashboard.
echo Press Ctrl+C here only if you want to fully stop the local server.
echo.

python app.py

echo.
echo Movie Manager stopped.
pause
