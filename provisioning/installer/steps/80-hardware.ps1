# Optional hardware steps; all are off by default and skipped in test mode.
# Original: "Device Service Setup" ran RBAInstaller.exe (Ingenico reader firmware); KioskAdmin's dellbios.lua
# (pushed later as a remote script) scheduled Dell's BIOS configuration package (dellbios.exe).
param($Config)
$cfg = $Config
$hw = $cfg.Hardware

if ($hw.CardReaderFirmware) {
    $rba = Join-Path $Reds "DeviceService\bin\RBAInstaller.exe"
    if (Test-Path $rba) {
        $svc = Get-Service 'device$service' -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq "Running") { Stop-Service 'device$service' -Force; Start-Sleep 20 }
        Invoke-Program $rba -WorkingDirectory (Split-Path $rba) -TimeoutSeconds 900 -AllowFailure | Out-Null
    } else { Write-Log "   no RBAInstaller.exe in the Device Service package" -Level Warn }
}

if ($hw.DellBios.Enabled) {
    $mfr = (Get-CimInstance Win32_ComputerSystem).Manufacturer
    if ($mfr -notmatch "Dell") { Write-Log "   not a Dell ($mfr); BIOS step skipped" }
    elseif (-not $hw.DellBios.Command) { throw "Hardware.DellBios.Command is not set (e.g. Dell Command | Configure's cctk.exe)" }
    else {
        $cmd = Resolve-SettingPath $cfg $hw.DellBios.Command
        $a = if ($hw.DellBios.Arguments) { @($hw.DellBios.Arguments) } else { @() }
        Invoke-Program $cmd $a -WorkingDirectory (Split-Path $cmd) | Out-Null
        $cfg.PendingReboot = $true
    }
}
if (-not $hw.CardReaderFirmware -and -not $hw.DellBios.Enabled) { Write-Log "   nothing to do" }
