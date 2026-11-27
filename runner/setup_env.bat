@echo off
setlocal enabledelayedexpansion
echo ========================================================
echo   House Inventory Compare - Python Environment Setup
echo ========================================================
echo.

:: Helper: Find working python executable (skip Windows Store stub)
set "PY_EXE="

for %%P in (
    "python"
    "py -3"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Python312\python.exe"
) do (
    if not defined PY_EXE (
        %%~P -c "import sys; print(sys.executable)" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY_EXE=%%~P"
        )
    )
)

:: Special check for quoted full paths with spaces
if not defined PY_EXE (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -c "import sys" >nul 2>&1
        if !errorlevel! equ 0 set "PY_EXE="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
    )
)
if not defined PY_EXE (
    if exist "C:\Program Files\Python312\python.exe" (
        "C:\Program Files\Python312\python.exe" -c "import sys" >nul 2>&1
        if !errorlevel! equ 0 set "PY_EXE="C:\Program Files\Python312\python.exe""
    )
)

if not defined PY_EXE (
    echo [Info] Python not found! Attempting to install Python 3.12 via winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if !errorlevel! neq 0 (
        echo [Error] Auto install failed. Please manually install Python 3.12+ from:
        echo         https://www.python.org/downloads/
        echo         (Make sure to check "Add Python to PATH" during installation)
        pause
        exit /b 1
    )
    :: Check if installed immediately after winget
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PY_EXE="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
    ) else (
        echo [Success] Python installed! Please reboot or re-login Windows if it still cannot be found.
        pause
        exit /b 0
    )
)

echo [1/3] Detected Python executable: %PY_EXE%
%PY_EXE% --version
echo.

:: Add Python directory to current session PATH and ensure user PATH includes it
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
