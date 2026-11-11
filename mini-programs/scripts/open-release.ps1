param(
    [ValidateSet('snapshot','local','live')][string]$Mode='snapshot',
    [switch]$VerifyOnly
)
$ErrorActionPreference='Stop'
$releaseRoot=(Resolve-Path (Join-Path $PSScriptRoot '../release')).Path
$pointer=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $releaseRoot "latest-$Mode.json") | ConvertFrom-Json
if ($pointer.id -notmatch '^\d+\.\d+\.\d+-(snapshot|local|live)-(\d{8}|online)-[0-9a-f]{16}$') {throw 'Invalid release ID'}
if ($Matches[1] -ne $Mode -or $pointer.mode -ne $Mode) {throw 'Release pointer mode mismatch'}
$folder=(Resolve-Path (Join-Path $releaseRoot ('versions/'+$pointer.id))).Path
$manifest=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $folder 'manifest.json') | ConvertFrom-Json
if ($manifest.id -ne $pointer.id -or $manifest.mode -ne $Mode) {throw 'Release identity mismatch'}
foreach($hostName in @('wechat','xiaohongshu')){
    $item=$manifest.platforms.$hostName
    $archiveName=if ($hostName -eq 'wechat') {'ETF-WeChat.zip'} else {'ETF-Xiaohongshu.zip'}
    if($item.archive -ne $archiveName) {throw 'Unexpected archive name'}
    $stream=[System.IO.File]::OpenRead((Join-Path $folder $archiveName))
    $sha=[System.Security.Cryptography.SHA256]::Create()
    try {
        $actual=[System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
    if ($actual -ne $item.sha256) {throw "Damaged archive: $archiveName"}
}
if ($VerifyOnly) {
    [pscustomobject]@{
        status = 'verified'
        id = $manifest.id
        mode = $manifest.mode
        dataDate = $manifest.dataDate
        folder = $folder
        wechatSha256 = $manifest.platforms.wechat.sha256
        xiaohongshuSha256 = $manifest.platforms.xiaohongshu.sha256
    } | ConvertTo-Json
} else {
    Invoke-Item -LiteralPath $folder
}
