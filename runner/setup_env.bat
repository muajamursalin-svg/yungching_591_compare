@echo off
echo ========================================================
echo   House Inventory Compare - Python Environment Setup
echo ========================================================
echo.

:: 1. Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Info] Python not found! Attempting to install Python 3.12 via winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo [Error] Auto install failed. Please manually install Python 3.12+ from:
        echo         https://www.python.org/downloads/
        echo         (Make sure to check "Add Python to PATH" during installation)
        pause
        exit /b 1
    )
    echo [Success] Python installed! Please close this window and re-run setup_env.bat to load PATH.
    pause
    exit /b 0
)

echo [1/3] Detected Python version:
python --version
echo.

:: 2. Upgrade pip
echo [2/3] Upgrading pip...
python -m pip install --upgrade pip

:: 3. Install required packages from requirements.txt
echo [3/3] Installing required packages (requests, curl_cffi, python-dotenv)...
python -m pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo [Error] Package installation failed! Please check your internet connection.
    pause
    exit /b 1
)

:: 4. Verify imports
echo.
echo [Verify] Testing package imports...
python -c "import requests, curl_cffi; print('  -> requests:', requests.__version__); print('  -> curl_cffi:', curl_cffi.__version__)"
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
