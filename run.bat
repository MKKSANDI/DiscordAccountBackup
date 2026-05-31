@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "LOG_DIR=%TEMP%\DiscordAccountBackup"
set "LAUNCHER_LOG=%LOG_DIR%\launcher.log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1

set "ELEVATED_FLAG=0"
if /i "%~1"=="--elevated" (
    set "ELEVATED_FLAG=1"
    shift
)

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c """"%~f0"""" --elevated %*' -Verb RunAs"
    if errorlevel 1 (
        echo Could not launch the elevated process.
        echo Launcher log: "%LAUNCHER_LOG%"
        >>"%LAUNCHER_LOG%" echo [%date% %time%] failed to spawn elevated process.
        pause
        exit /b 1
    )
    exit /b 0
)

if "%ELEVATED_FLAG%"=="1" (
    if /i "%~1"=="--elevated" shift
)

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON="
    set "BOOTSTRAP_PYTHON="
)

if not defined PYTHON (
    python --version >nul 2>&1
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=python"
)

if not defined BOOTSTRAP_PYTHON if not defined PYTHON (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=py -3"
)

if not defined PYTHON if not defined BOOTSTRAP_PYTHON (
    echo Python was not found. Install Python 3.10+ and re-run.
    echo Launcher log: "%LAUNCHER_LOG%"
    >>"%LAUNCHER_LOG%" echo [%date% %time%] no python runtime found.
    pause
    exit /b 1
)

if not defined PYTHON (
    echo Creating local virtual environment...
    %BOOTSTRAP_PYTHON% -m venv .venv
    if errorlevel 1 (
        echo Failed to create .venv.
        echo Launcher log: "%LAUNCHER_LOG%"
        >>"%LAUNCHER_LOG%" echo [%date% %time%] failed to create .venv.
        pause
        exit /b 1
    )
    set "PYTHON=.venv\Scripts\python.exe"
)

%PYTHON% -m pip install --upgrade pip >nul 2>&1

%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies from requirements.txt.
    echo Launcher log: "%LAUNCHER_LOG%"
    >>"%LAUNCHER_LOG%" echo [%date% %time%] dependency install failed.
    pause
    exit /b 1
)

set "APP_ARGS=%*"
set "APP_ARGS=%APP_ARGS: --elevated=%"
set "APP_ARGS=%APP_ARGS:--elevated=%"

if defined APP_ARGS (
    %PYTHON% main.py %APP_ARGS%
) else (
    %PYTHON% main.py
)
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
    echo.
    echo DiscordAccountBackup exited with code %APP_EXIT%.
    echo Launcher log: "%LAUNCHER_LOG%"
    >>"%LAUNCHER_LOG%" echo [%date% %time%] app exited with code %APP_EXIT%.
    pause
)
exit /b %APP_EXIT%
