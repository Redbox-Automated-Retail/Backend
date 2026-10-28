<#
.SYNOPSIS
  Run every MessageType of the 2014-2016 engines' Redbox.DVD.KioskServices.dll through its own proxies
  (KioskServicesCases2016.cs) against the self-hosted MessageService, over Remoting or REST.
.PARAMETER EngineDir
  The "Kiosk Engine" folder (bin\ and Kext\) of a 2014-2016 kop-v2-engine, e.g. exported from the backend with
    cmd /c "wslc exec rb-updatesvc python -m redbox_backend admin export kop-v2-engine --edition data-10282016 > engine.tar"
  (engine 1.7.0.9, Redbox.DVD.KioskServices 1.8.0.37). The harness exe is built into its bin\ (a scratch copy).
.EXAMPLE
  & C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe -File run-kioskservices-cases-2016.ps1 -EngineDir "E:\temp\rb-engine-2016\Program Files\Redbox\REDS\Kiosk Engine" -Protocol Remoting
#>
param(
    [Parameter(Mandatory)] [string] $EngineDir,
    [string] $Url = "https://localhost/KioskServices/MessageService.rem",
    [ValidateSet("Remoting", "REST")] [string] $Protocol = "Remoting",
    [string] $Only = ""
)
$ErrorActionPreference = "Stop"
# Test data (ops market, a disc, a reservation for the harness card) for kiosk 990016, through rb-cloudapi.
$env:HARNESS_KIOSK = "990016"
$setup = Get-Content -Raw (Join-Path $PSScriptRoot "kioskservices_cases_setup.py")
$out = $setup | & "C:\Program Files\WSL\wslc.exe" exec -i -e HARNESS_KIOSK=990016 rb-cloudapi sh -c "cd /app && PYTHONPATH=/app python -"
foreach ($line in $out) { if ($line -match "^(HARNESS_\w+)=(.*)$") { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2]) } }
Write-Host "setup: $out"
$bin = Join-Path $EngineDir "bin"
$exe = Join-Path $bin "KioskServicesCases2016.exe"
$cp = New-Object System.CodeDom.Compiler.CompilerParameters
$cp.GenerateExecutable = $true
$cp.OutputAssembly = $exe
$cp.CompilerOptions = "/platform:x86"
foreach ($r in (Join-Path $bin "Redbox.Core.dll"), "System.dll", "System.Core.dll") { [void]$cp.ReferencedAssemblies.Add($r) }
$src = (Join-Path $PSScriptRoot "EngineStubs.cs"), (Join-Path $PSScriptRoot "KioskServicesCases2016.cs") | ForEach-Object { Get-Content -Raw $_ }
$res = (New-Object Microsoft.CSharp.CSharpCodeProvider).CompileAssemblyFromSource($cp, [string[]]$src)
if ($res.Errors.HasErrors) { $res.Errors | ForEach-Object { Write-Host $_ }; exit 2 }
& $exe (Join-Path $EngineDir "Kext\Redbox.DVD.KioskServices.dll") $Url $Protocol $Only
exit $LASTEXITCODE
