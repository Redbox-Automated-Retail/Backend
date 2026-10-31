# The Windows Sandbox's LogonCommand (written by tests\sandbox.ps1): optionally start the hardware emulator
# (tests\clients\sandbox steps 10 and 20), run Install-Kiosk.ps1 in test mode on the mapped payload, then keep
# serving C:\rb\control\inbox (tests\clients\sandbox\loop.ps1) so the host can look around.
Set-ExecutionPolicy -Scope Process Bypass -Force
$control = "C:\rb\control"
$log = Join-Path $control "outbox\logon.log"
New-Item -ItemType Directory -Force (Split-Path $log), "C:\Program Files\Redbox\KioskLogs" | Out-Null
"logon $(Get-Date -Format o) as $(whoami)" | Set-Content $log
if (Test-Path C:\rb\tools\python\python.exe) {
    foreach ($s in "10-com0com.ps1", "20-hw-emu.ps1") {
        "=== $s" | Add-Content $log
        try { & "C:\rb\clients\sandbox\steps\$s" *>&1 | ForEach-Object { ($_ | Out-String -Width 300).TrimEnd() | Add-Content $log } }
        catch { "ERROR in ${s}: $_" | Add-Content $log }
    }
}
"=== Install-Kiosk.ps1 -TestMode $(Get-Date -Format T)" | Add-Content $log
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\rb\payload\installer\Install-Kiosk.ps1 `
    -Settings C:\rb\payload\settings.json -TestMode -StateDir (Join-Path $control "state") *>&1 |
    ForEach-Object { ($_ | Out-String -Width 300).TrimEnd() | Add-Content $log }
"=== Install-Kiosk.ps1 exit $LASTEXITCODE $(Get-Date -Format T)" | Add-Content $log
& C:\rb\clients\sandbox\loop.ps1
