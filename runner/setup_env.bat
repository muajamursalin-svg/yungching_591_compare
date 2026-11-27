@echo off
chcp 65001 >nul
echo ========================================================
echo   房屋庫存比對系統 - 查詢伺服器 Python 環境一鍵安裝
echo ========================================================
echo.

:: 1. 檢查 Python 是否已安裝
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [提示] 尚未偵測到 Python！正在嘗試透過 winget 自動安裝 Python 3.12...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo [錯誤] 自動安裝失敗，請手動前往 https://www.python.org/downloads/ 下載安裝 Python 3.12+
        echo        (安裝時請務必勾選下方的 "Add Python to PATH")
        pause
        exit /b 1
    )
    echo [成功] Python 安裝完成！請關閉此視窗並「重新執行」本安裝檔以套用環境變數。
    pause
    exit /b 0
)

echo [1/3] 偵測到 Python 版本：
python --version
echo.

:: 2. 升級 pip
echo [2/3] 正在升級 pip...
python -m pip install --upgrade pip

:: 3. 安裝 requirements.txt 套件
echo [3/3] 正在安裝專案所需爬蟲套件 (requests, curl_cffi, python-dotenv)...
python -m pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo [錯誤] 套件安裝過程中發生錯誤，請檢查網路連線！
    pause
    exit /b 1
)

:: 4. 驗證套件匯入
echo.
echo [驗證] 正在測試套件是否可正常載入...
python -c "import requests, curl_cffi; print('  -> requests:', requests.__version__); print('  -> curl_cffi:', curl_cffi.__version__)"
if %errorlevel% neq 0 (
    echo [錯誤] 套件驗證失敗！
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   ✅ 環境安裝與驗證全部完成！
echo   下一步：請以「系統管理員身分」執行 download_and_setup_runner.ps1
echo ========================================================
pause
