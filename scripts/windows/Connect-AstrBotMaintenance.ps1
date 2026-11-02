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
    $engine = [PowerShell]::Create()
    try {
      [void]$engine.AddScript("`$ErrorActionPreference='Stop';`n" + $request.script)
      $running = $engine.BeginInvoke()
      if (-not $running.AsyncWaitHandle.WaitOne([int]$request.timeout * 1000)) {
        $engine.Stop()
        $response = @{ id=$request.id; status='unknown'; error='Execution timed out; external processes may still be running.' }
      } else {
        $output = $engine.EndInvoke($running) | Out-String -Width 200
        if ($output.Length -gt 16384) { $output=$output.Substring(0,16384) }
        $response = @{ id=$request.id; status='completed'; output=$output }
        if ($engine.HadErrors) { $response.status='failed'; $detail=($engine.Streams.Error | Out-String); $response.error=$detail.Substring(0,[Math]::Min(16384,$detail.Length)) }
      }
    } catch { $response = @{ id=$request.id; status='failed'; error=$_.Exception.Message } }
    finally { $engine.Dispose() }
    $process.StandardInput.WriteLine((ConvertTo-MaintenanceJson $response)); $process.StandardInput.Flush()
  }
} finally {
  if ($null -ne $process -and -not $process.HasExited) { $process.Kill() }
  $process.Dispose()
  Write-Host 'Maintenance session ended.'
}
