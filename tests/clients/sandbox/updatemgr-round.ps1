<#
.SYNOPSIS
  Inside the sandbox: one updatemgrd round against the backend for some repositories (poll, download, stage,
  activate into the real install folders), with the real Update Manager code (run-updatemgr-harness.ps1).
.DESCRIPTION
  The sandbox has no updatemgrd service; this plays it, with the kiosk's store ID, its Update Manager folder
  (REDS\Update Manager\.store) and the srcp URL the kiosk is provisioned with. Change sets are downloaded from the
  backend's UPDATE_CONTENT_BASE_URL; when that is http://localhost:7080 (development), a relay on the sandbox's
  127.0.0.1:7080 forwards to this PC. Needs an update-console build with lua51.dll and xdelta3.exe in -BinDir
  (copy one into the control folder). From the host:

    .\run-sandbox-step.ps1 -Command "& C:\rb\clients\sandbox\updatemgr-round.ps1 -Repos kioskclient -Show 'Kiosk Client\bin\KioskClientService.API.dll'"
#>
param(
    [Parameter(Mandatory)] [string[]] $Repos,
    [string] $BinDir = "C:\rb\control\um-bin",
    [string] $Root = "C:\Program Files\Redbox\REDS\Update Manager",
    [string] $Url = "srcp://updatesvc.redbox.com:7002",
    [string[]] $Show = @(),           # files under REDS\ to print the version and SHA-1 of afterwards
    [int] $Rounds = 2                 # the second poll reports the new heads back to the backend
)
$ErrorActionPreference = "Stop"
$store = (Get-Content C:\rb\control\store-id.txt -TotalCount 1).Trim()
$pcAddress = ([Net.Dns]::GetHostAddresses("updatesvc.redbox.com") | Where-Object { $_.AddressFamily -eq "InterNetwork" } |
    Select-Object -First 1).IPAddressToString
if (-not (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 7080 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process C:\rb\tools\python\python.exe -ArgumentList "-u", "C:\rb\clients\sandbox\relay.py", "--listen", "127.0.0.1",
        "--ports", "7080", "--target", $pcAddress -WindowStyle Hidden
    Start-Sleep 2
    "relay 127.0.0.1:7080 -> ${pcAddress}:7080"
}
"store $store, $Url, repositories $($Repos -join ', ')"
# 32-bit PowerShell: updatemgrd and its Redbox.UpdateManager.* assemblies are x86.
& "$env:SystemRoot\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File C:\rb\clients\run-updatemgr-harness.ps1 -BinDir $BinDir -Root $Root `
    -Url $Url -Store $store -Activate $Repos -Rounds $Rounds
$code = $LASTEXITCODE
foreach ($r in $Repos) {
    foreach ($f in ".active", ".label") {
        $p = Join-Path $Root ".store\.repository\$r\$f"
        if (Test-Path $p) { "  $r $f = $(Get-Content $p -Raw)" }
    }
}
foreach ($s in $Show) {
    $p = Join-Path "C:\Program Files\Redbox\REDS" $s
    if (Test-Path $p) {
        $v = (Get-Item $p).VersionInfo
        "  $s : FileVersion $($v.FileVersion), ProductVersion $($v.ProductVersion), SHA-1 $((Get-FileHash $p -Algorithm SHA1).Hash)"
    } else { "  $s : missing" }
}
exit $code
