<#
.SYNOPSIS
  Run the real Update Manager client code against the self-hosted Update Service.
.PARAMETER BinDir
  A folder with the decompiled Update Manager assemblies (the update-console build output, e.g.
  src\UpdateManager\update-console\bin\Release\net46) plus lua51.dll and xdelta3.exe from a kiosk image.
.PARAMETER Root
  Scratch folder that plays "REDS\Update Manager" (gets .store\.repository and .store\.data).
.EXAMPLE
  .\run-updatemgr-harness.ps1 -BinDir C:\tmp\um\bin -Root C:\tmp\um -Url rcp://localhost:7012 -Store 59394 -Activate harness-test -ServerPoll
#>
param(
    [Parameter(Mandatory)] [string] $BinDir,
    [Parameter(Mandatory)] [string] $Root,
    [string] $Url = "rcp://localhost:7012",
    [string] $Store = "59394",
    [string[]] $Activate = @(),
    [switch] $ServerPoll,
    [int] $Rounds = 3
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force (Join-Path $Root ".store\.repository"), (Join-Path $Root ".store\.data") | Out-Null
# xdelta3.exe is started from the working directory.
Set-Location $BinDir
[Environment]::CurrentDirectory = $BinDir
# updatemgrd finds xdelta3.exe next to its own exe; this host is powershell.exe, so use PATH.
$env:PATH = "$BinDir;$env:PATH"
$refs = Get-ChildItem $BinDir -Filter *.dll | Where-Object { $_.Name -like "Redbox.*" -or $_.Name -like "Common.Logging*" } |
    ForEach-Object { $_.FullName }
foreach ($r in $refs) { [void][Reflection.Assembly]::LoadFrom($r) }
$refs += "System.Core.dll", "System.Web.Extensions.dll"
Add-Type -Path (Join-Path $PSScriptRoot "UpdateManagerHarness.cs") -ReferencedAssemblies $refs
$code = [UpdateManagerHarness]::Run($Url, $Store, $Root, $Activate, [bool]$ServerPoll, $Rounds)
exit $code
