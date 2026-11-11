param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$appUrl = 'http://127.0.0.1:4173'
$nodePath = (Get-Command node.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$serverPath = Join-Path $PSScriptRoot 'local_web.mjs'
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$beijingNow = [DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(8))
$logDir = Join-Path (Join-Path $desktop 'ETF更新日志') $beijingNow.ToString('yyyy-MM-dd')
$null = New-Item -ItemType Directory -Path $logDir -Force
$healthy = $false
try {
    $status = Invoke-RestMethod -Uri "$appUrl/local/status" -TimeoutSec 2
    if ($status.app_id -ne 'etf-flow-local') { throw '端口已被其他程序使用。' }
    $healthy = $true
} catch {
    if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 4173 -State Listen -ErrorAction SilentlyContinue) { throw '4173端口已有程序，未停止或替换它。请查看本地数据配置。' }
}
if (-not $healthy) {
    $runStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $process = Start-Process -FilePath $nodePath -ArgumentList @('"' + $serverPath + '"') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "etf-local-$runStamp.log") -RedirectStandardError (Join-Path $logDir "etf-local-$runStamp-error.log")
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        Start-Sleep -Milliseconds 200
        try { $status = Invoke-RestMethod -Uri "$appUrl/local/status" -TimeoutSec 1; $healthy = $status.app_id -eq 'etf-flow-local'; if ($healthy) { break } } catch {}
        if ($process.HasExited) { break }
    }
    if (-not $healthy) { throw '本地网页启动失败，请查看桌面ETF更新日志。' }
}
Write-Output "ETF本机网页：$appUrl"
if (-not $NoBrowser) { Start-Process $appUrl }
