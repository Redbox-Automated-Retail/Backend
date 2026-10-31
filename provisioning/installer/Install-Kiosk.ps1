<#
.SYNOPSIS
  Provision a fresh Windows 10/11 PC as a Redbox kiosk that runs against a self-hosted backend.
.DESCRIPTION
  An open replacement for Redbox's Windows 10 store installer (Software repo: docs/systems/store-installer.md).
  It does what that installer and its installer-steps.lua did, in the same order, without its Redbox-only parts
  (Kaseya, McAfee, nxlog, Blink, the Update Service "installer start" check-in):

    10-prerequisites   .NET Framework 4.7.2+, .NET 3.5 (HAL), .NET Core / ASP.NET Core 2.2.1 x86, optional Ingenico driver
    20-update-store    the data zip into REDS\Update Manager\.store\.repository, then every repository staged and
                       activated by the kiosk's own update code (update-console --eval activate-image.lua),
                       before/afteractivate.lua included
    30-engine          the Kiosk Engine's machine settings (environment, kiosk type, sync, HAL URL)
    40-backend         point every component at the backend: rb-reprovision apply <kiosk profile>
                       (hosts or DNS names, CA, store ID, URLs, update-service credentials)
    50-services        HAL (HAL.PackageInstaller), updatemgrd --register, Device / Kiosk Client / Update Client services
    60-shell           kiosk user, autologon, KioskShell as the shell (Shell Launcher, or the machine-wide Winlogon shell)
    70-hardening       PCI / kiosk settings of the original (autorun, accessibility keys, task manager, ...)
    80-hardware        optional: card reader firmware (RBAInstaller), Dell BIOS settings
    90-finish          reboot, or in test mode start everything and the engine now

  Every step is idempotent and the installer is resumable: finished steps are recorded in the state folder
  (like the original's C:\.post-install) and skipped on the next run; -Force re-runs them.
.PARAMETER Settings
  The settings file (JSON). See settings.example.json and README.md.
.PARAMETER TestMode
  For VMs and Windows Sandbox: no reboot, no Shell Launcher/autologon changes unless asked, HAL allowed on the
  .NET 4 runtime, hardware steps skipped, services and the engine started at the end. Overrides Settings.TestMode.
.PARAMETER Only
  Run only these steps (names or numbers, e.g. 20,40).
.PARAMETER Force
  Re-run steps that are recorded as done.
.EXAMPLE
  .\Install-Kiosk.ps1 -Settings D:\kiosk-12345.settings.json
.EXAMPLE
  .\Install-Kiosk.ps1 -Settings C:\rb\payload\settings.json -TestMode
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Settings,
    [switch] $TestMode,
    [string] $StateDir,     # overrides Settings.StateDir (logs and the record of finished steps)
    [string[]] $Only,
    [switch] $Force
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")

$cfg = Read-InstallerSettings -Path $Settings -TestMode:$TestMode -StateDir $StateDir
Start-InstallerLog $cfg
Write-Log "Install-Kiosk started as $([Security.Principal.WindowsIdentity]::GetCurrent().Name); settings $($cfg.SettingsPath); test mode $($cfg.TestMode)"

$steps = Get-ChildItem (Join-Path $PSScriptRoot "steps") -Filter "*.ps1" | Sort-Object Name
if ($Only) {
    $Only = @($Only | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $steps = $steps | Where-Object { $n = $_.BaseName; @($Only | Where-Object { $n -eq $_ -or $n.StartsWith("$_-") }).Count -gt 0 }
}
foreach ($step in $steps) {
    if (-not $Force -and (Test-StepDone $cfg $step.BaseName)) { Write-Log "== $($step.BaseName): done earlier, skipped (-Force re-runs it)"; continue }
    Write-Log "== $($step.BaseName)"
    $started = Get-Date
    try {
        & $step.FullName -Config $cfg
    } catch {
        Write-Log "FAILED in $($step.BaseName): $($_.Exception.Message)" -Level Error
        Write-Log ($_.ScriptStackTrace) -Level Error
        Write-Log "Fix the cause and run Install-Kiosk.ps1 again; finished steps are skipped." -Level Error
        exit 1
    }
    Set-StepDone $cfg $step.BaseName
    Write-Log ("   {0} done in {1:n0} s" -f $step.BaseName, ((Get-Date) - $started).TotalSeconds)
}
Write-Log "Install-Kiosk finished"
if ($cfg.PendingReboot) { Restart-KioskIfAllowed $cfg }
exit 0
