# 台灣本地查詢伺服器 (Self-hosted Runner) 一鍵安裝包

將本 `runner` 資料夾複製到您要作為查詢伺服器的台灣 Windows 電腦（或直接 `git pull` 下來），依序執行以下兩步驟，**3 分鐘內即可完成建置**！

---

## 📁 檔案說明
* `requirements.txt`：專案必備的 Python 爬蟲套件清單（`requests`, `curl_cffi`, `python-dotenv`）。
* `setup_env.bat`：一鍵檢查並安裝 Python 與所有爬蟲套件。
* `download_and_setup_runner.ps1`：一鍵下載最新版 GitHub Actions Runner，並自動註冊為 Windows 開機背景服務。

---

## 🚀 安裝步驟（在目標查詢電腦執行）

### 步驟 1：安裝 Python 與爬蟲套件
1. 在目標電腦上，雙擊執行 **`setup_env.bat`**。
2. 腳本會自動檢查 Python（若未安裝會嘗試自動安裝），並裝好 `requests` 與 `curl_cffi`。
3. 看到 `✅ 環境安裝與驗證全部完成！` 即可關閉視窗。

### 步驟 2：註冊 GitHub Runner 背景服務
1. 按下 `Win` 鍵搜尋 `PowerShell`，對它按右鍵 ➔ **「以系統管理員身分執行」**。
2. 切換到本 `runner` 資料夾路徑並執行安裝腳本（以下假設放在 `D:\runner`）：
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   cd D:\runner
   .\download_and_setup_runner.ps1
   ```
3. 腳本會自動下載並解壓官方 Runner 至 `C:\actions-runner`，接著請您打開以下網址複製 `--token` 後方的一串英數字貼上：
   👉 https://github.com/muajamursalin-svg/yungching_591_compare/settings/actions/runners/new
4. 貼上按 Enter 後，腳本會自動綁定並**註冊為 Windows 系統背景服務**（電腦開機即自動待命，不需開啟任何黑框視窗）。

### 步驟 3：確認綠燈上線
前往以下網址，確認清單出現 `tw-query-server` 且狀態顯示為綠燈 **`Idle`**：
👉 https://github.com/muajamursalin-svg/yungching_591_compare/settings/actions/runners

看到綠燈後通知系統切換工作流，未來所有的 591 / 5168 / 樂屋網查詢與分店同步就會全部透過這台台灣原生 IP 電腦秒速執行！
