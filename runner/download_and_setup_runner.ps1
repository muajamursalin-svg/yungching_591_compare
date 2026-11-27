# ========================================================
# House Inventory Compare - GitHub Self-hosted Runner Setup
# Please run PowerShell as Administrator to execute this script.
# ========================================================

$ErrorActionPreference = "Stop"

# 1. Check Administrator privileges
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[Error] Please run PowerShell as Administrator!" -ForegroundColor Red
    Write-Host "Right-click PowerShell -> Run as Administrator" -ForegroundColor Yellow
    pause
    exit 1
}

$RepoUrl = "https://github.com/muajamursalin-svg/yungching_591_compare"
$InstallDir = "C:\actions-runner"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  GitHub Self-hosted Runner Setup (Path: $InstallDir)" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

# 2. Create install directory
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
}
Set-Location $InstallDir

# 3. Download and extract latest actions-runner
if (-not (Test-Path "$InstallDir\config.cmd")) {
    Write-Host "[1/3] Fetching latest GitHub Actions Runner release info..." -ForegroundColor Yellow
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/actions/runner/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -like "actions-runner-win-x64-*.zip" } | Select-Object -First 1

    if (-not $asset) {
        Write-Host "[Error] Failed to get download URL. Please download manually from GitHub." -ForegroundColor Red
        exit 1
    }

    $zipPath = "$InstallDir\actions-runner.zip"
    Write-Host "[2/3] Downloading $($asset.name)..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath

    Write-Host "[3/3] Extracting archive..." -ForegroundColor Yellow
    Expand-Archive -Path $zipPath -DestinationPath $InstallDir -Force
    Remove-Item $zipPath -Force
} else {
    Write-Host "[Info] Runner already exists in $InstallDir, skipping download." -ForegroundColor Green
}

# 4. Prompt for Runner Token
Write-Host ""
Write-Host "Please open this URL in your browser to get your Runner Token:" -ForegroundColor Cyan
Write-Host "  https://github.com/muajamursalin-svg/yungching_591_compare/settings/actions/runners/new" -ForegroundColor White
Write-Host "(Copy the string after --token in the Configure section)" -ForegroundColor Gray
Write-Host ""
$Token = Read-Host "Paste your Runner Token and press Enter"

if ([string]::IsNullOrWhiteSpace($Token)) {
    Write-Host "[Error] Token cannot be empty!" -ForegroundColor Red
    exit 1
}

# 5. Configure and install as Windows Service
Write-Host ""
Write-Host "Configuring and registering Windows background service..." -ForegroundColor Yellow
& "$InstallDir\config.cmd" --url $RepoUrl --token $Token.Trim() --name "tw-query-server" --unattended --runasservice --replace

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Green
    Write-Host "  [OK] Runner setup and service registration succeeded!" -ForegroundColor Green
    Write-Host "  This PC is now registered as your Taiwan query server." -ForegroundColor Green
    Write-Host "  Check GitHub Runners page to confirm [Idle] status." -ForegroundColor Green
    Write-Host "========================================================" -ForegroundColor Green
} else {
    Write-Host "[Error] Runner configuration failed. Check if your Token is expired." -ForegroundColor Red
}

pause
