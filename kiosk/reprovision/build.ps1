<#
.SYNOPSIS
  Build rb-reprovision.exe (x86, .NET 2.0/3.5 runtime, also runs on 4.x) with the C# compiler that ships with
  Windows' .NET Framework. Works on a kiosk too; nothing else is needed.
.EXAMPLE
  .\build.ps1                    # -> out\rb-reprovision.exe and out\rb-reprovision.exe.config
#>
param([string] $Out = (Join-Path $PSScriptRoot "out"))
$ErrorActionPreference = "Stop"
$fw = Join-Path $env:SystemRoot "Microsoft.NET\Framework"
# The 3.5 compiler targets the 2.0 runtime, which old kiosks (Windows 7 / XP era) have; the .config lets the same
# exe run on the 4.x runtime of current kiosks, which may not have 3.5 enabled. Fall back to the 4.x compiler.
$csc = @("v3.5\csc.exe", "v4.0.30319\csc.exe") | ForEach-Object { Join-Path $fw $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) { throw "no csc.exe under $fw" }
New-Item -ItemType Directory -Force $Out | Out-Null
$exe = Join-Path $Out "rb-reprovision.exe"
& $csc /nologo /target:exe /platform:x86 /optimize+ "/out:$exe" /r:System.ServiceProcess.dll /r:System.Core.dll `
    (Join-Path $PSScriptRoot "Reprovision.cs") (Join-Path $PSScriptRoot "Json.cs")
if ($LASTEXITCODE) { throw "csc failed ($LASTEXITCODE)" }
@'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup useLegacyV2RuntimeActivationPolicy="true">
    <supportedRuntime version="v4.0" />
    <supportedRuntime version="v2.0.50727" />
  </startup>
</configuration>
'@ | Set-Content -Encoding UTF8 "$exe.config"
"built $exe with $csc"
