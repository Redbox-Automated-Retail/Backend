<#
.SYNOPSIS
  Run every MessageType of the real 1.80 Redbox.KioskServices.Proxy through the proxy's own service methods
  (KioskServicesCases.cs) against the self-hosted MessageService, over Remoting or REST.
.PARAMETER BinDir
  The Kiosk Engine bin folder of a kop-v2-engine build with Redbox.KioskServices.Proxy 1.80.0.12 (revision
  "kop-v2-engine-Ultima" of the imported seeds). The proxy and the engine assemblies it was built against are
  loaded from there; nothing is installed. Export it from the backend:
    cmd /c "wslc exec rb-updatesvc python -m redbox_backend admin export kop-v2-engine --revision kop-v2-engine-Ultima > engine.tar"
    tar xf engine.tar
  (cmd's redirection keeps the tar binary; a PowerShell 5.1 pipe would not). Requires the stack running; the test
  data is created first (kioskservices_cases_setup.py).
.EXAMPLE
  & C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe -File run-kioskservices-cases.ps1 -BinDir "E:\temp\rb-engine-1.80\Program Files\Redbox\REDS\Kiosk Engine\bin" -Protocol REST
#>
param(
    [Parameter(Mandatory)] [string] $BinDir,
    [string] $Url = "https://localhost/KioskServices/MessageService.rem",
    [ValidateSet("Remoting", "REST")] [string] $Protocol = "Remoting",
    [string] $Only = ""
)
$ErrorActionPreference = "Stop"
# Test data (ops market, a disc, a reservation for the harness card) in the running stack, through rb-cloudapi.
$setup = Get-Content -Raw (Join-Path $PSScriptRoot "kioskservices_cases_setup.py")
$out = $setup | & "C:\Program Files\WSL\wslc.exe" exec -i rb-cloudapi sh -c "cd /app && PYTHONPATH=/app python -"
foreach ($line in $out) { if ($line -match "^(HARNESS_\w+)=(.*)$") { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2]) } }
Write-Host "setup: $out"
Set-Location $BinDir
[Environment]::CurrentDirectory = $BinDir
[AppDomain]::CurrentDomain.add_AssemblyResolve({
    param($s, $e)
    $f = Join-Path $BinDir (($e.Name -split ",")[0] + ".dll")
    if (Test-Path $f) { return [Reflection.Assembly]::LoadFrom($f) }
    return $null
})
$refs = @()
foreach ($n in "Redbox.Core", "Redbox.KioskEngine.ComponentModel", "Redbox.Rental.Model", "Redbox.KioskServices.Proxy",
               "Redbox.KioskServices.Proxy.ComponentModel", "Redbox.KioskServices.ComponentModel",
               "Redbox.BrokerServices.Proxy.ComponentModel", "Redbox.Lua") {
    $p = Join-Path $BinDir "$n.dll"
    [void][Reflection.Assembly]::LoadFrom($p)
    $refs += $p
}
$refs += "System.Runtime.Remoting.dll", "System.Web.Extensions.dll", "System.Core.dll", "Microsoft.CSharp.dll"
Add-Type -Path (Join-Path $PSScriptRoot "EngineStubs.cs"), (Join-Path $PSScriptRoot "KioskServicesCases.cs") -ReferencedAssemblies $refs
try { exit [KioskServicesCases]::RunAll($Url, $Protocol, $Only) } catch { Write-Host $_.Exception.ToString(); exit 2 }
