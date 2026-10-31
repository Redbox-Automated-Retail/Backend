# Hand over to the Update Manager. Normally: reboot (the original rebooted unconditionally), and updatemgrd,
# the services and, through KioskShell or updatemgrd's watchdog, the engine start by themselves.
# With StartAfterInstall (test mode): start them now and launch the engine in this session.
param($Config)
$cfg = $Config
if (-not $cfg.StartAfterInstall) { $cfg.PendingReboot = $true; return }

foreach ($s in 'halsvc$default', 'updatemgrd$default', 'device$service', 'kioskclient$service', 'updateclient$service') {
    if (-not (Get-Service $s -ErrorAction SilentlyContinue)) { continue }
    try { Start-Service $s; Write-Log "   started $s" } catch { Write-Log "   $s did not start: $($_.Exception.Message)" -Level Warn }
}
$engine = Join-Path $Reds "Kiosk Engine\bin\kioskengine.exe"
if (Test-Path $engine) {
    Get-Process kioskengine -ErrorAction SilentlyContinue | Stop-Process -Force
    $p = Start-Process $engine -WorkingDirectory (Split-Path $engine) -PassThru
    Start-Sleep 30
    Write-Log "   kioskengine.exe pid $($p.Id), running: $(-not $p.HasExited)"
}
Get-Service 'halsvc$default', 'updatemgrd$default', 'device$service', 'kioskclient$service', 'updateclient$service' -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Log ("   {0,-22} {1}" -f $_.Name, $_.Status) }
