<#
.SYNOPSIS
  Run the real KioskServicesProxy (Redbox.KioskServices.Proxy.dll) against the self-hosted MessageService.
.PARAMETER BinDir
  A folder holding Redbox.KioskServices.Proxy.dll (e.g. the one shipped in an update-store package) and its references:
  Redbox.Core, Redbox.KioskEngine.ComponentModel, Redbox.KioskEngine.Environment, Redbox.Rental.Model, Redbox.REDS.Framework,
  Redbox.KioskServices.ComponentModel, Redbox.KioskServices.Proxy.ComponentModel, Redbox.BrokerServices.Proxy.ComponentModel,
  DeviceService.ComponentModel, Newtonsoft.Json, Redbox.Lua.
.PARAMETER Protocol
  Remoting (HttpChannel + binary formatter; the kiosk default) or REST (form POST payload=<url-encoded json>).
.EXAMPLE
  & C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe -File run-kioskservices-harness.ps1 -BinDir C:\ks -Protocol Remoting
#>
param(
    [Parameter(Mandatory)] [string] $BinDir,
    [string] $Url = "http://localhost:80/KioskServices/MessageService.rem",
    [ValidateSet("Remoting", "REST")] [string] $Protocol = "Remoting",
    [string] $Json = '{"MessageType":"HealthCheck","KioskId":990010}'
)
$ErrorActionPreference = "Stop"
Set-Location $BinDir
[Environment]::CurrentDirectory = $BinDir
$refs = @()
foreach ($n in "Redbox.Core", "Redbox.KioskEngine.ComponentModel", "Redbox.KioskServices.Proxy", "Redbox.KioskServices.ComponentModel") {
    $p = Join-Path $BinDir "$n.dll"
    [void][Reflection.Assembly]::LoadFrom($p)
    $refs += $p
}
[AppDomain]::CurrentDomain.add_AssemblyResolve({
    param($s, $e)
    $f = Join-Path $BinDir (($e.Name -split ",")[0] + ".dll")
    if (Test-Path $f) { return [Reflection.Assembly]::LoadFrom($f) }
    return $null
})
$refs += "System.Runtime.Remoting.dll", "System.Web.Extensions.dll", "System.Core.dll"
Add-Type -Path (Join-Path $PSScriptRoot "KioskServicesHarness.cs") -ReferencedAssemblies $refs
try { exit [KioskServicesHarness]::Run($Url, $Protocol, $Json) } catch { Write-Host $_.Exception.ToString(); exit 2 }
