# scripts/windows_maintenance.py: user-started, temporary SSH maintenance agent.
param(
  [string]$SshTarget = 'yingjxu@10.74.4.16',
  [string]$Project = '/home/yingjxu/workspace/astrbot-snowluma'
)
$ErrorActionPreference = 'Stop'
function ConvertTo-MaintenanceJson($Value) {
  $json = ConvertTo-Json -InputObject $Value -Compress -Depth 8
  return [regex]::Replace($json, '[^\u0000-\u007f]', { param($match) '\u{0:x4}' -f [int][char]$match.Value })
}
if ($SshTarget -notmatch '^[a-zA-Z0-9_.@:-]+$' -or $Project -notmatch '^/[a-zA-Z0-9_./-]+$') { throw 'Invalid SSH target or project directory.' }
Write-Host 'Temporary AstrBot maintenance. Commands run as your current Windows user.' -ForegroundColor Cyan
Write-Host 'Keep this window visible. Close it or press Ctrl+C to end the session.'
$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = 'ssh.exe'
$info.Arguments = "-T -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 $SshTarget /usr/bin/python3 $Project/scripts/windows_maintenance.py join"
$info.UseShellExecute = $false
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true
$info.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
$info.CreateNoWindow = $false
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $info
try {
  if (-not $process.Start()) { throw 'SSH did not start.' }
  $hello = @{ protocol=2; host=$env:COMPUTERNAME; user=$env:USERNAME; os=[Environment]::OSVersion.VersionString; powershell=$PSVersionTable.PSVersion.ToString() }
  $process.StandardInput.WriteLine((ConvertTo-MaintenanceJson $hello)); $process.StandardInput.Flush()
  Write-Host 'Waiting for the server to confirm the SSH maintenance connection...'
  $ready = $process.StandardOutput.ReadLineAsync()
  if (-not $ready.Wait(30000)) { throw 'The server did not confirm the connection within 30 seconds.' }
  if ([string]::IsNullOrWhiteSpace($ready.Result)) { throw 'SSH ended before the maintenance connection was confirmed.' }
  $confirmation = $ready.Result | ConvertFrom-Json
  if (-not $confirmation.ready) { throw 'The server rejected the maintenance connection.' }
  Write-Host 'Connected. Waiting for maintenance commands.' -ForegroundColor Green
  while (-not $process.HasExited) {
    $line = $process.StandardOutput.ReadLine()
    if ($null -eq $line) { break }
    $request = $line | ConvertFrom-Json
    Write-Host "`n--- Command $($request.id) ---" -ForegroundColor Yellow
    Write-Host $request.script
    $child = New-Object System.Diagnostics.Process
    $childStarted = $false
    $commandFile = $null
    try {
      $command = "`$ErrorActionPreference='Stop';`$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=New-Object Text.UTF8Encoding(`$false);`$OutputEncoding=[Console]::OutputEncoding;try { & {`n" + $request.script + "`n} | Out-String -Width 200 | Write-Output;exit 0 } catch { [Console]::Error.WriteLine([string]`$_);exit 1 }"
      $commandFile = [IO.Path]::Combine([IO.Path]::GetTempPath(), 'AstrBotMaintenanceCommand-'+[guid]::NewGuid().ToString('N')+'.ps1')
      [IO.File]::WriteAllText($commandFile,$command,(New-Object Text.UTF8Encoding($true)))
      $child.StartInfo = New-Object Diagnostics.ProcessStartInfo
      $child.StartInfo.FileName = [IO.Path]::Combine($PSHOME,'powershell.exe')
      $child.StartInfo.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File `"$commandFile`""
      $child.StartInfo.UseShellExecute = $false
      $child.StartInfo.CreateNoWindow = $true
      $child.StartInfo.RedirectStandardInput = $true
      $child.StartInfo.RedirectStandardOutput = $true
      $child.StartInfo.RedirectStandardError = $true
      $child.StartInfo.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
      $child.StartInfo.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
      if (-not $child.Start()) { throw 'Command process did not start.' }
      $childStarted = $true
      $child.StandardInput.Close()
      $stdout = $child.StandardOutput.ReadToEndAsync()
      $stderr = $child.StandardError.ReadToEndAsync()
      if (-not $child.WaitForExit([int]$request.timeout * 1000)) {
        # Kill this command's tree only; do not stop the SSH transport or unrelated apps.
        $killInfo = New-Object Diagnostics.ProcessStartInfo
        $killInfo.FileName = [IO.Path]::Combine($env:SystemRoot,'System32\taskkill.exe')
        $killInfo.Arguments = "/PID $($child.Id) /T /F"
        $killInfo.UseShellExecute = $false
        $killInfo.CreateNoWindow = $true
        $killer = [Diagnostics.Process]::Start($killInfo)
        [void]$killer.WaitForExit(3000)
        $killer.Dispose()
        if (-not $child.HasExited) { $child.Kill() }
        $response = @{ id=$request.id; status='unknown'; error='Command timed out; termination was requested for its process tree. Check any partial changes before retrying.' }
      } else {
        $output = if ($stdout.Wait(2000)) { $stdout.Result } else { '[Output pipe remains open after command exit.]' }
        $detail = if ($stderr.Wait(2000)) { $stderr.Result } else { '[Error pipe remains open after command exit.]' }
        if ($output.Length -gt 16384) { $output=$output.Substring(0,16384) }
        $response = @{ id=$request.id; status='completed'; output=$output }
        if ($child.ExitCode -ne 0) { $response.status='failed'; $response.error=$detail.Substring(0,[Math]::Min(16384,$detail.Length)) }
      }
    } catch { $response = @{ id=$request.id; status='failed'; error=$_.Exception.Message } }
    finally {
      if ($null -ne $child) { if ($childStarted -and -not $child.HasExited) { $child.Kill() }; $child.Dispose() }
      if ($commandFile -and [IO.File]::Exists($commandFile)) { [IO.File]::Delete($commandFile) }
    }
    $process.StandardInput.WriteLine((ConvertTo-MaintenanceJson $response)); $process.StandardInput.Flush()
  }
} finally {
  if ($null -ne $process -and -not $process.HasExited) { $process.Kill() }
  $process.Dispose()
  Write-Host 'Maintenance session ended.'
}
