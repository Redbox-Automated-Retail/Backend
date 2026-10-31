<#
.SYNOPSIS
  Let every .NET 2.0 executable under the given folders run on .NET Framework 4.8, so that the kiosk needs no
  .NET 3.5. Called by installer-steps.lua ("Install HAL") after the seed is activated.
.DESCRIPTION
  The original installer required .NET 3.5 SP1 (step 2) because HAL (halsvc-win32.exe, HAL.PackageInstaller.exe)
  and other parts of the seed are built for the 2.0 runtime. For each such exe this adds to its .config
      <startup useLegacyV2RuntimeActivationPolicy="true"><supportedRuntime version="v4.0" /> ...
  ahead of any v2.0 entry, so it runs on 4.x whether or not 3.5 is there. The same runtime policy is also set
  machine-wide (HKLM\SOFTWARE\Microsoft\.NETFramework OnlyUseLatestCLR = 1, both registry views), for executables
  that a later package update brings with a .config of its own.
  Exits 1 when a config could not be written.
#>
param([Parameter(Mandatory)] [string[]] $Root)
$ErrorActionPreference = "Stop"
$failed = 0
$retargeted = 0
foreach ($dir in $Root) {
    if (-not (Test-Path $dir)) { "skip $dir (missing)"; continue }
    foreach ($exe in Get-ChildItem $dir -Recurse -Filter *.exe -File -ErrorAction SilentlyContinue) {
        try { $runtime = [Reflection.Assembly]::ReflectionOnlyLoadFrom($exe.FullName).ImageRuntimeVersion } catch { continue }   # native
        if ($runtime -notlike "v2.*") { continue }
        $conf = "$($exe.FullName).config"
        try {
            if (-not (Test-Path $conf)) { Set-Content -Path $conf -Encoding UTF8 -Value '<?xml version="1.0" encoding="utf-8"?><configuration></configuration>' }
            [xml]$x = Get-Content -Raw $conf
            $root_ = $x.DocumentElement
            $startup = $root_.SelectSingleNode("startup")
            if (-not $startup) { $startup = $x.CreateElement("startup"); $root_.PrependChild($startup) | Out-Null }
            $startup.SetAttribute("useLegacyV2RuntimeActivationPolicy", "true")
            if (-not $startup.SelectSingleNode("supportedRuntime[@version='v4.0']")) {
                $v4 = $x.CreateElement("supportedRuntime"); $v4.SetAttribute("version", "v4.0")
                $startup.PrependChild($v4) | Out-Null
            }
            $x.Save($conf)
            $retargeted++
            "v4.0 runtime for $($exe.FullName) ($runtime)"
        } catch {
            $failed++
            [Console]::Error.WriteLine("retarget-netfx2: $conf`: $($_.Exception.Message)")
        }
    }
}
foreach ($view in "Registry64", "Registry32") {
    $k = [Microsoft.Win32.RegistryKey]::OpenBaseKey("LocalMachine", $view).CreateSubKey("SOFTWARE\Microsoft\.NETFramework")
    $k.SetValue("OnlyUseLatestCLR", 1, "DWord"); $k.Close()
}
"OnlyUseLatestCLR = 1; $retargeted executables retargeted, $failed failed"
exit $(if ($failed) { 1 } else { 0 })
