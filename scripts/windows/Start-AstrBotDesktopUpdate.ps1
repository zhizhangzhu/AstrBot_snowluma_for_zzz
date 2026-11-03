# User-started channel for one pinned AstrBot Desktop Pet installer. PowerShell 5.1 compatible.
param(
  [Parameter(Mandatory=$true)][string]$TaskId,
  [Parameter(Mandatory=$true)][string]$ExpectedVersion,
  [Parameter(Mandatory=$true)][string]$ExpectedSha256,
  [string]$SshTarget = 'root@10.74.4.16',
  [string]$Project = '/home/yingjxu/workspace/astrbot-snowluma',
  [string]$InstallDirectory = ''
)
$ErrorActionPreference = 'Stop'
if ($TaskId -notmatch '^dev_[a-f0-9]{32}$' -or $ExpectedVersion -notmatch '^\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?$' -or $ExpectedSha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid pinned update identity.' }
if ($SshTarget -notmatch '^[a-zA-Z0-9_.@:-]+$' -or $Project -notmatch '^/[a-zA-Z0-9_./-]+$') { throw 'Invalid SSH target.' }
$folder = Join-Path -Path ([IO.Path]::GetTempPath()) -ChildPath ('AstrBotUpdate-' + $TaskId)
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$remote = $Project + '/.runtime/evolution-artifacts/' + $TaskId
$manifestFile = Join-Path $folder 'manifest.json'
& scp "${SshTarget}:${remote}/manifest.json" $manifestFile
if ($LASTEXITCODE -ne 0) { throw 'Could not open this update channel.' }
$manifest = Get-Content $manifestFile -Raw | ConvertFrom-Json
$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
if ($manifest.task_id -ne $TaskId -or $manifest.version -ne $ExpectedVersion -or $manifest.sha256 -ne $ExpectedSha256.ToLowerInvariant() -or $manifest.expires_at -lt $now -or $manifest.consumed_at) { throw 'The update changed, expired or was already consumed. Ask for a new, exact-version instruction.' }
$previousReceipt = Join-Path $folder 'receipt.json'
if (Test-Path $previousReceipt) {
  $previous = Get-Content $previousReceipt -Raw | ConvertFrom-Json
  if ($previous.nonce -eq $manifest.nonce) { throw 'This exact channel already ran. Renew this version in the server panel before trying again.' }
  Move-Item -LiteralPath $previousReceipt -Destination (Join-Path $folder ('receipt-previous-' + [DateTime]::UtcNow.Ticks + '.json'))
}
$installer = Join-Path $folder 'setup.exe'
& scp "${SshTarget}:${remote}/setup.exe" $installer
if ($LASTEXITCODE -ne 0 -or (Get-FileHash -Path $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedSha256.ToLowerInvariant()) { throw 'Installer transfer or checksum failed.' }
if (-not $InstallDirectory) {
  $current = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'AstrBot Desktop Pet.exe' -and $_.ExecutablePath } | Select-Object -ExpandProperty ExecutablePath -Unique)
  if ($current.Count -gt 1) { throw 'Multiple installations are running. Specify -InstallDirectory explicitly.' }
  if ($current.Count -eq 1) { $InstallDirectory = Split-Path -Parent $current[0] }
  else { $InstallDirectory = Join-Path $env:LOCALAPPDATA 'Programs\AstrBot Desktop Pet' }
}
$InstallDirectory = [IO.Path]::GetFullPath($InstallDirectory)
if ($InstallDirectory -eq [IO.Path]::GetPathRoot($InstallDirectory) -or $InstallDirectory -like "$env:WINDIR*" -or $InstallDirectory -like "$env:APPDATA\AstrBotCoopanion*") { throw 'Unsafe installation directory.' }
$exe = Join-Path $InstallDirectory 'AstrBot Desktop Pet.exe'
Write-Host "Authorized one update: $ExpectedVersion ($TaskId)." -ForegroundColor Cyan
Write-Host 'Only the selected installation will be replaced. Pairing and chat data in AppData are preserved.'
$backup = Join-Path $folder ('previous-installation-' + [DateTime]::UtcNow.Ticks)
$hadInstallation = Test-Path $exe
if ($hadInstallation) { Copy-Item -LiteralPath $InstallDirectory -Destination $backup -Recurse -Force }
Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $exe } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$receipt = @{ status='failed'; task_id=$TaskId; nonce=$manifest.nonce; sha256=$manifest.sha256; version=$ExpectedVersion; rollback=$false }
try {
  $install = Start-Process -FilePath $installer -ArgumentList @('/S',('/D=' + $InstallDirectory)) -Wait -PassThru
  if ($install.ExitCode -ne 0) { throw 'Installer reported a failure.' }
  $package = Join-Path $InstallDirectory 'resources\app\package.json'
  $actualVersion = (Get-Content $package -Raw | ConvertFrom-Json).version
  if (-not (Test-Path $exe) -or $actualVersion -ne $ExpectedVersion) { throw 'Installed version did not match the pinned artifact.' }
  Start-Process -FilePath $exe | Out-Null
  $receipt.status='installed'
  Write-Host "Installed $actualVersion. The client will reconnect using its saved pairing." -ForegroundColor Green
} catch {
  if ($hadInstallation) {
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $exe } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Move-Item -LiteralPath $InstallDirectory -Destination (Join-Path $folder ('failed-installation-' + [DateTime]::UtcNow.Ticks))
    Copy-Item -LiteralPath $backup -Destination $InstallDirectory -Recurse -Force
    Start-Process -FilePath $exe | Out-Null
    $receipt.rollback=$true
  }
  Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
  $receiptPath = Join-Path $folder 'receipt.json'
  $receipt | ConvertTo-Json -Compress | Set-Content -LiteralPath $receiptPath -Encoding UTF8
  & scp $receiptPath "${SshTarget}:${remote}/receipt.json"
  Write-Host 'This one-version update channel is now closed.'
}
if ($receipt.status -ne 'installed') { throw 'Update failed. Read the result above; no AppData was restored or replaced.' }
