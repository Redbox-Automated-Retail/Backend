# LogonCommand for running Redbox's ORIGINAL store installer in Windows Sandbox, for comparison with
# Install-Kiosk.ps1 (tests\sandbox.ps1 up -Original <setup.exe> -DataZip <zip>). Never run it on a real PC: it
# replaces the shell, changes passwords policy-wide and reboots.
# Sandbox workarounds, all inside the disposable sandbox:
#  - .NET 3.5 cannot be enabled in Windows Sandbox. The RIX host only checks that the v3.5 folder exists, and
#    installer-steps.lua step 2 reads NDP\v3.5 Version/SP from the registry; both are faked, and the host gets a
#    .config that lets it run on the 4.x runtime. (Its JScript wizard pages may not work on 4.x, so --silent.)
#  - The front end must reach srcp://updatesvc.redbox.com:7002 ("installer start"): the kiosk profile's hosts
#    entries and CA are applied with rb-reprovision first.
#  - RIX reboots at the end (reboot="Always", silent): SeShutdownPrivilege is taken from Administrators, and
#    XPTo7\silent.txt stops installer-steps.lua's own reboot and its message boxes.
Set-ExecutionPolicy -Scope Process Bypass -Force
$control = "C:\rb\control"; $out = Join-Path $control "original"
New-Item -ItemType Directory -Force $out, (Join-Path $control "outbox") | Out-Null
$log = Join-Path $out "harness.log"
function L($m) { "$(Get-Date -Format T) $m" | Add-Content $log }
L "start as $(whoami)"
if (Test-Path C:\rb\tools\python\python.exe) {
    foreach ($s in "10-com0com.ps1", "20-hw-emu.ps1") { try { & "C:\rb\clients\sandbox\steps\$s" *>&1 | Out-String | Add-Content $log } catch { L "ERROR $s $_" } }
}

$setup = "C:\rbsetup"
New-Item -ItemType Directory -Force $setup | Out-Null
Copy-Item (Get-ChildItem C:\rb\original -Filter *.exe | Select-Object -First 1).FullName "$setup\setup.exe"
Copy-Item (Get-ChildItem C:\rb\original -Filter *.zip | Select-Object -First 1).FullName "$setup\data.zip"
@'
<?xml version="1.0" encoding="utf-8"?>
<configuration><startup useLegacyV2RuntimeActivationPolicy="true"><supportedRuntime version="v4.0"/><supportedRuntime version="v2.0.50727"/></startup></configuration>
'@ | Set-Content -Encoding UTF8 "$setup\setup.exe.config"

# Backend: hosts entries and CA only matter at this point (nothing of REDS exists yet).
$prof = Get-ChildItem C:\rb\payload -Filter "kiosk-*.json" | Select-Object -First 1
New-Item -ItemType Directory -Force "C:\Program Files\Redbox\REDS" | Out-Null   # rb-reprovision looks for it
& C:\rb\payload\rb-reprovision\rb-reprovision.exe apply $prof.FullName --no-restart *>&1 | Out-String | Add-Content $log

# Fake .NET 3.5 for the host's folder check and the Lua registry check.
foreach ($fw in "$env:windir\Microsoft.NET\Framework\v3.5", "$env:windir\Microsoft.NET\Framework64\v3.5") {
    if (-not (Test-Path $fw)) { & takeown.exe /f (Split-Path $fw) | Out-Null; & icacls.exe (Split-Path $fw) /grant "Administrators:F" | Out-Null; New-Item -ItemType Directory $fw | Out-Null }
}
foreach ($view in "SOFTWARE", "SOFTWARE\WOW6432Node") {
    $k = "HKLM:\$view\Microsoft\NET Framework Setup\NDP\v3.5"
    New-Item $k -Force | Out-Null
    New-ItemProperty $k -Name Install -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty $k -Name SP -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty $k -Name Version -Value "3.5.30729.4926" -Force | Out-Null
}
# No reboots.
New-Item -ItemType Directory -Force "C:\Program Files\Redbox\XPTo7" | Out-Null
Set-Content "C:\Program Files\Redbox\XPTo7\silent.txt" "sandbox"
& secedit.exe /export /cfg C:\rbsetup\sec.inf /areas USER_RIGHTS | Out-Null
(Get-Content C:\rbsetup\sec.inf) -replace '^SeShutdownPrivilege\s*=.*$', 'SeShutdownPrivilege = *S-1-5-32-545' -replace '^SeRemoteShutdownPrivilege\s*=.*$', 'SeRemoteShutdownPrivilege = *S-1-5-32-545' |
    Set-Content -Encoding Unicode C:\rbsetup\sec2.inf
& secedit.exe /configure /db C:\rbsetup\sec.sdb /cfg C:\rbsetup\sec2.inf /areas USER_RIGHTS | Out-Null
L "shutdown privilege removed from Administrators (takes effect for new logons and new tokens)"

# Copy the logs out every few seconds (the front end logs to REDS\Update Manager\logs).
Start-Job -ScriptBlock {
    while ($true) {
        foreach ($p in "C:\Program Files\Redbox\REDS\Update Manager\logs", "C:\Program Files\Redbox\REDS\Update Manager\installGuid.txt") {
            if (Test-Path $p) { Copy-Item $p "C:\rb\control\original\" -Recurse -Force -ErrorAction SilentlyContinue }
        }
        Start-Sleep 5
    }
} | Out-Null

L "running setup.exe --silent"
$p = Start-Process "$setup\setup.exe" -ArgumentList "--silent", "--nosplash", "--log:$out\rix.log" -WorkingDirectory $setup -PassThru
$p.WaitForExit()
L "setup.exe exit $($p.ExitCode)"
Start-Sleep 10
foreach ($p in "C:\Program Files\Redbox\REDS\Update Manager\logs", "C:\Program Files\Redbox\REDS\Update Manager\installGuid.txt", "C:\.post-install") {
    if (Test-Path $p) { Copy-Item $p $out -Recurse -Force -ErrorAction SilentlyContinue }
}
Get-Service | Where-Object { $_.Name -match '\$|halsvc' } | Format-Table -AutoSize Status, StartType, Name | Out-String | Add-Content $log
L "done"
& C:\rb\clients\sandbox\loop.ps1
