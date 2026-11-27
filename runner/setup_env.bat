@echo off
setlocal enabledelayedexpansion
echo ========================================================
echo   House Inventory Compare - Python Environment Setup
echo ========================================================
echo.

set "PY_EXE="

:: 1. Check standard PATH and py launcher
python -c "import sys; assert sys.version_info >= (3, 8)" >nul 2>&1
if %errorlevel% equ 0 set "PY_EXE=python"

if not defined PY_EXE (
    py -3 -c "import sys; assert sys.version_info >= (3, 8)" >nul 2>&1
    if !errorlevel! equ 0 set "PY_EXE=py -3"
)

:: 2. Scan common Python installation directories with wildcards
if not defined PY_EXE (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
        if not defined PY_EXE if exist "%%D\python.exe" (
            "%%D\python.exe" -c "import sys" >nul 2>&1
            if !errorlevel! equ 0 set "PY_EXE="%%D\python.exe""
        )
    )
)

if not defined PY_EXE (
    for /d %%D in ("C:\Program Files\Python*") do (
        if not defined PY_EXE if exist "%%D\python.exe" (
            "%%D\python.exe" -c "import sys" >nul 2>&1
            if !errorlevel! equ 0 set "PY_EXE="%%D\python.exe""
        )
    )
)

if not defined PY_EXE (
    for /d %%D in ("C:\Python*") do (
        if not defined PY_EXE if exist "%%D\python.exe" (
            "%%D\python.exe" -c "import sys" >nul 2>&1
            if !errorlevel! equ 0 set "PY_EXE="%%D\python.exe""
        )
    )
)

:: 3. Deep search in %LOCALAPPDATA% (for winget/custom locations)
if not defined PY_EXE (
    for /f "delims=" %%F in ('where /r "%LOCALAPPDATA%" python.exe 2^>nul') do (
        if not defined PY_EXE (
            echo %%F | findstr /i "WindowsApps" >nul
            if !errorlevel! neq 0 (
                "%%F" -c "import sys" >nul 2>&1
                if !errorlevel! equ 0 set "PY_EXE="%%F""
            )
        )
    )
)

:: 4. If still not found, install via winget (machine-wide or user)
if not defined PY_EXE (
    echo [Info] Python not found! Attempting to install Python 3.12 via winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if !errorlevel! neq 0 (
        echo [Error] Auto install failed. Please manually install Python 3.12+ from:
        echo         https://www.python.org/downloads/
        pause
        exit /b 1
    )
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*" "C:\Program Files\Python*") do (
        if not defined PY_EXE if exist "%%D\python.exe" set "PY_EXE="%%D\python.exe""
    )
    if not defined PY_EXE (
        echo [Warning] Python was installed, but path could not be auto-detected.
        echo           Please install Python manually and check "Add Python to PATH".
        pause
        exit /b 1
    )
)

echo [1/3] Detected Python executable: %PY_EXE%
%PY_EXE% --version
echo.

:: Add Python directory to current session PATH
for /f "delims=" %%I in ('%PY_EXE% -c "import os, sys; print(os.path.dirname(sys.executable))"') do set "PY_DIR=%%I"
set "PATH=%PY_DIR%;%PY_DIR%\Scripts;%PATH%"

:: 2. Upgrade pip
echo [2/3] Upgrading pip...
%PY_EXE% -m pip install --upgrade pip

:: 3. Install required packages from requirements.txt
echo [3/3] Installing required packages (requests, curl_cffi, python-dotenv)...
%PY_EXE% -m pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo [Error] Package installation failed! Please check your internet connection.
    pause
    exit /b 1
)

:: 4. Verify imports
echo.
echo [Verify] Testing package imports...
%PY_EXE% -c "import requests, curl_cffi; print('  -> requests:', requests.__version__); print('  -> curl_cffi:', curl_cffi.__version__)"
if %errorlevel% neq 0 (
    echo [Error] Package verification failed!
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   [OK] Environment setup and verification completed!
echo   Next: Run download_and_setup_runner.ps1 as Administrator.
echo ========================================================
pause
