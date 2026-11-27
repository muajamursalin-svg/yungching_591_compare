# ========================================================
# 房屋庫存比對系統 - GitHub Self-hosted Runner 一鍵安裝腳本
# 請使用「系統管理員身分」開啟 PowerShell 執行本腳本
# ========================================================

$ErrorActionPreference = "Stop"

# 1. 檢查系統管理員權限
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[錯誤] 請以「系統管理員身分」執行 PowerShell 再執行此腳本！" -ForegroundColor Red
    Write-Host "操作方式：對著 PowerShell 按右鍵 -> 以系統管理員身分執行" -ForegroundColor Yellow
    pause
    exit 1
}

$RepoUrl = "https://github.com/muajamursalin-svg/yungching_591_compare"
$InstallDir = "C:\actions-runner"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  GitHub Self-hosted Runner 一鍵安裝 (目標路徑: $InstallDir)" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

# 2. 建立安裝目錄
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
}
Set-Location $InstallDir

# 3. 取得最新版 actions-runner 下載網址並下載解壓
if (-not (Test-Path "$InstallDir\config.cmd")) {
    Write-Host "[1/3] 正在取得最新版 GitHub Actions Runner 下載資訊..." -ForegroundColor Yellow
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/actions/runner/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -like "actions-runner-win-x64-*.zip" } | Select-Object -First 1

    if (-not $asset) {
        Write-Host "[錯誤] 無法取得下載網址，請手動前往 GitHub 設定頁下載！" -ForegroundColor Red
        exit 1
    }

    $zipPath = "$InstallDir\actions-runner.zip"
    Write-Host "[2/3] 正在下載 $($asset.name)..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath

    Write-Host "[3/3] 正在解壓縮安裝檔..." -ForegroundColor Yellow
    Expand-Archive -Path $zipPath -DestinationPath $InstallDir -Force
    Remove-Item $zipPath -Force
} else {
    Write-Host "[提示] 偵測到 $InstallDir 已有 Runner 安裝檔，跳過下載步驟。" -ForegroundColor Green
}

# 4. 取得使用者輸入之一次性 Token
Write-Host ""
Write-Host "請開啟瀏覽器前往以下網址取得您的專屬 Runner Token：" -ForegroundColor Cyan
Write-Host "👉 https://github.com/muajamursalin-svg/yungching_591_compare/settings/actions/runners/new" -ForegroundColor White
Write-Host "（在頁面下方的 Configure 區塊中，複製 --token 後面那串英數字）" -ForegroundColor Gray
Write-Host ""
$Token = Read-Host "請貼上您的 Runner Token 並按 Enter"

if ([string]::IsNullOrWhiteSpace($Token)) {
    Write-Host "[錯誤] Token 不可為空！" -ForegroundColor Red
    exit 1
}

# 5. 自動設定並註冊為 Windows 背景服務
Write-Host ""
Write-Host "正在綁定並註冊為 Windows 開機自動啟動背景服務..." -ForegroundColor Yellow
& "$InstallDir\config.cmd" --url $RepoUrl --token $Token.Trim() --name "tw-query-server" --unattended --runasservice --replace

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Green
    Write-Host "  ✅ 安裝與背景服務註冊成功！" -ForegroundColor Green
    Write-Host "  這台電腦現在已經成為您的台灣原生 IP 查詢伺服器。" -ForegroundColor Green
    Write-Host "  請至 GitHub Runners 頁面確認狀態已顯示為綠燈 [Idle]。" -ForegroundColor Green
    Write-Host "========================================================" -ForegroundColor Green
} else {
    Write-Host "[錯誤] Runner 設定失敗，請檢查 Token 是否過期或重複嘗試。" -ForegroundColor Red
}

pause
