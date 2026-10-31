# Prerequisites (original: RIX execute-process 96-99, and .NET 3.5 SP1 checked in installer-steps.lua step 2).
param($Config)
$cfg = $Config
$pre = $cfg.Prerequisites

# .NET Framework 4.7.2 or later (release 461808): updatemgrd, the engine, KioskShell. Windows 10 1803+ has it.
$release = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" -ErrorAction SilentlyContinue).Release
Write-Log "   .NET Framework 4.x release: $release"
if (-not $release -or $release -lt 461808) {
    $ndp = Get-PinnedFile $cfg $pre.DotNetFramework "ndp-offline.exe"
    if (-not $ndp) { throw ".NET Framework 4.7.2 or later is needed; set Prerequisites.DotNetFramework.Path to its offline installer" }
    Invoke-Program $ndp @("/q", "/norestart") -OkExitCodes 0, 1641, 3010 | Out-Null
    $cfg.PendingReboot = $true
}

# .NET 3.5 (the 2.0 runtime): HAL (halsvc-win32, HAL.PackageInstaller) targets it.
$netfx3 = Get-WindowsOptionalFeature -Online -FeatureName NetFx3 -ErrorAction SilentlyContinue
Write-Log "   NetFx3: $(if ($netfx3) { $netfx3.State } else { 'unknown' })"
if ($pre.NetFx3 -ne "Skip" -and $netfx3 -and $netfx3.State -ne "Enabled") {
    try {
        $a = @{ Online = $true; FeatureName = "NetFx3"; All = $true; NoRestart = $true }
        if ($pre.NetFx3Source) { $a.Source = $pre.NetFx3Source; $a.LimitAccess = $true }
        $r = Enable-WindowsOptionalFeature @a
        if ($r.RestartNeeded) { $cfg.PendingReboot = $true }
        Write-Log "   NetFx3 enabled"
    } catch {
        if ($pre.NetFx3 -eq "Enable") { throw "enabling .NET 3.5 failed: $($_.Exception.Message) (set Prerequisites.NetFx3Source to the Windows media's sources\sxs)" }
        Write-Log "   NetFx3 could not be enabled ($($_.Exception.Message)); HAL will need Hardware.HalAllowV4Runtime" -Level Warn
    }
}

# .NET Core 2.2.1 and ASP.NET Core 2.2.1, x86: Device Service, Kiosk Client Service, Update Client Service.
function Test-SharedRuntime($name, $version) { Test-Path "${env:ProgramFiles(x86)}\dotnet\shared\$name\$version" }
if (-not (Test-SharedRuntime "Microsoft.NETCore.App" "2.2.1")) {
    $f = Get-PinnedFile $cfg $pre.DotNetCoreRuntime "dotnet-runtime-2.2.1-win-x86.exe"
    Invoke-Program $f @("/install", "/quiet", "/norestart") -OkExitCodes 0, 1641, 3010 | Out-Null
} else { Write-Log "   .NET Core 2.2.1 x86 present" }
if (-not (Test-SharedRuntime "Microsoft.AspNetCore.App" "2.2.1")) {
    $f = Get-PinnedFile $cfg $pre.AspNetCoreRuntime "aspnetcore-runtime-2.2.1-win-x86.exe"
    Invoke-Program $f @("/install", "/quiet", "/norestart") -OkExitCodes 0, 1641, 3010 | Out-Null
} else { Write-Log "   ASP.NET Core 2.2.1 x86 present" }

# Ingenico USB driver for the card reader (original arguments).
if ($pre.IngenicoDriver -and -not $cfg.TestMode) {
    Invoke-Program $pre.IngenicoDriver @("/S", "/PORT=7", "/PID=0057:7") -AllowFailure | Out-Null
}
