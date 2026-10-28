<#
.SYNOPSIS
  Run the real decompiled reservation-broker client (Redbox.BrokerServices.Proxy + GenuineChannels) against rb-remoting.
.PARAMETER BinDir
  Build output of src\KioskServices\Redbox.BrokerServices.Proxy (bin\Release\net472): the proxy and its references.
.EXAMPLE
  & C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe -File run-broker-harness.ps1 -BinDir ...\net472 -KioskId 990010 -Seconds 240
#>
param(
    [Parameter(Mandatory)] [string] $BinDir,
    [string] $Url = "gtcp://localhost:7890/RegistrationBroker/Default.rem",
    [int] $KioskId = 990010,
    [int] $Seconds = 180
)
$ErrorActionPreference = "Stop"
Set-Location $BinDir
[Environment]::CurrentDirectory = $BinDir
$refs = @()
foreach ($n in "Redbox.Core", "Redbox.KioskEngine.ComponentModel", "Redbox.Rental.Model", "Redbox.BrokerServices.Proxy",
               "Redbox.BrokerServices.Proxy.ComponentModel", "Redbox.Services.KioskBrokerServices.KioskShared", "GenuineChannels") {
    $p = Join-Path $BinDir "$n.dll"
    [void][Reflection.Assembly]::LoadFrom($p)
    $refs += $p
}
# Resolve the rest of the proxy's references from the same folder.
[AppDomain]::CurrentDomain.add_AssemblyResolve({
    param($s, $e)
    $f = Join-Path $BinDir (($e.Name -split ",")[0] + ".dll")
    if (Test-Path $f) { return [Reflection.Assembly]::LoadFrom($f) }
    return $null
})
$refs += "System.Runtime.Remoting.dll", "System.Core.dll"
Add-Type -Path (Join-Path $PSScriptRoot "BrokerHarness.cs") -ReferencedAssemblies $refs
try { exit [BrokerHarness]::Run($Url, $KioskId, $Seconds) } catch { Write-Host $_.Exception.InnerException.ToString(); exit 2 }
