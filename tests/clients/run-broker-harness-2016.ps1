<#
.SYNOPSIS
  Run the 2014-2016 engines' reservation broker client (Redbox.DVD.BrokerServices.dll) against rb-remoting.
.PARAMETER EngineDir
  The "Kiosk Engine" folder of a 2014-2016 kop-v2-engine (bin\ and Kext\), e.g. exported with
  `admin export kop-v2-engine --edition data-10282016` (engine 1.7.0.9, Redbox.DVD.BrokerServices 1.7.0.9).
.EXAMPLE
  & C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe -File run-broker-harness-2016.ps1 -EngineDir "E:\temp\rb-engine-2016\Program Files\Redbox\REDS\Kiosk Engine" -Seconds 240
#>
param(
    [Parameter(Mandatory)] [string] $EngineDir,
    [string] $Url = "gtcp://localhost:7890/RegistrationBroker/Default.rem",
    [int] $KioskId = 990016,
    [int] $Seconds = 180
)
$ErrorActionPreference = "Stop"
$bin = Join-Path $EngineDir "bin"
$kext = Join-Path $EngineDir "Kext"
# An x86 console exe in the (scratch copy of the) engine's bin\, so the application base is bin\ as for kioskengine.exe:
# the binary formatter binds the broker's KioskShared there by simple name.
$exe = Join-Path $bin "BrokerHarness2016.exe"
$cp = New-Object System.CodeDom.Compiler.CompilerParameters
$cp.GenerateExecutable = $true
$cp.OutputAssembly = $exe
$cp.CompilerOptions = "/platform:x86"
foreach ($r in (Join-Path $bin "Redbox.Core.dll"), (Join-Path $bin "Redbox.KioskEngine.ComponentModel.dll"), "System.dll", "System.Core.dll") {
    [void]$cp.ReferencedAssemblies.Add($r)
}
$src = (Join-Path $PSScriptRoot "EngineStubs.cs"), (Join-Path $PSScriptRoot "BrokerHarness2016.cs") | ForEach-Object { Get-Content -Raw $_ }
$res = (New-Object Microsoft.CSharp.CSharpCodeProvider).CompileAssemblyFromSource($cp, [string[]]$src)
if ($res.Errors.HasErrors) { $res.Errors | ForEach-Object { Write-Host $_ }; exit 2 }
& $exe (Join-Path $kext "Redbox.DVD.BrokerServices.dll") $Url $KioskId $Seconds
exit $LASTEXITCODE
