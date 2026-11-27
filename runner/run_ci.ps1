param(
    [string]$ScriptName = "run_compare_task.py"
)

$ErrorActionPreference = "Stop"

try {
    $baseTool = if ($env:RUNNER_TOOL_CACHE) { $env:RUNNER_TOOL_CACHE } else { "C:\actions-runner\_work\_tool" }
    $pyRoot = Join-Path $baseTool "py312"
    $pyExe = Join-Path $pyRoot "tools\python.exe"

    Write-Host "[CI] Tool cache base: $baseTool"
    Write-Host "[CI] Target python: $pyExe"

    if (-not (Test-Path $pyExe)) {
        Write-Host "[CI] Installing standalone Python 3.12.8 to $pyRoot..."
        New-Item -ItemType Directory -Path $pyRoot -Force | Out-Null
        $zip = Join-Path $pyRoot "py.zip"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri "https://www.nuget.org/api/v2/package/python/3.12.8" -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $pyRoot -Force
        Remove-Item $zip -Force
    }

    $ErrorActionPreference = "Continue"
    & $pyExe -c "import requests, curl_cffi" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[CI] Installing dependencies from runner/requirements.txt..."
        $pipOut = & $pyExe -m pip install --no-warn-script-location -r runner/requirements.txt 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            throw "PIP_FAIL: $pipOut"
        }
    }

    Write-Host "[CI] Running $ScriptName..."
    $runOut = & $pyExe $ScriptName 2>&1 | Out-String
    Write-Host $runOut
    if ($LASTEXITCODE -ne 0) {
        throw "PY_RUN_FAIL: $runOut"
    }
}
catch {
    $errMsg = $_.Exception.Message
    if (-not $errMsg) { $errMsg = $_.ToString() }
    if ($errMsg.Length -gt 800) { $errMsg = $errMsg.Substring($errMsg.Length - 800) }
    Write-Host "[CI ERROR] $errMsg"
    if ($env:TURSO_DATABASE_URL -and $env:TURSO_AUTH_TOKEN) {
        try {
            $dbUrl = $env:TURSO_DATABASE_URL.Replace("libsql://", "https://").TrimEnd("/") + "/v2/pipeline"
            $body = @{
                requests = @(
                    @{
                        type = "execute"
                        stmt = @{
                            sql = "INSERT INTO system_locks (lock_key, is_locked, locked_at, updated_at, message) VALUES ('last_runner_error', 0, datetime('now'), datetime('now'), ?) ON CONFLICT(lock_key) DO UPDATE SET updated_at = excluded.updated_at, message = excluded.message;"
                            args = @(@{ type = "text"; value = $errMsg })
                        }
                    },
                    @{ type = "close" }
                )
            } | ConvertTo-Json -Depth 10 -Compress
            Invoke-RestMethod -Uri $dbUrl -Method Post -Headers @{ Authorization = "Bearer $($env:TURSO_AUTH_TOKEN)"; "Content-Type" = "application/json" } -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | Out-Null
        } catch {}
    }
    exit 1
}
