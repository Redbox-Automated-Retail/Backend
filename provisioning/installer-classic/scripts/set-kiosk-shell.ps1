<#
.SYNOPSIS
  Make KioskShell the shell for every user. Called by installer-steps.lua ("Install Redbox Shell").
.DESCRIPTION
  Replaces the update manager kernel's kernel.replaceshellwith, which ran wmic.exe (gone from Windows 11) and
  ignored its failure. Same effect, as provisioning/installer/steps/60-shell.ps1 does it:
    ShellLauncher  Shell Launcher (Enterprise, Education, IoT Enterprise): the WESL_UserSetting calls of the original,
                   the default shell and the one for Administrators (S-1-5-32-544), DefaultAction 0 (restart it)
    Winlogon       without Shell Launcher (Pro): HKLM Winlogon\Shell, which also applies to every user
    Auto           ShellLauncher where the edition has it, otherwise Winlogon
    None           leave the shell alone
  Exits 1, with the reason on stderr, when the shell could not be set, so the step fails.
#>
param(
    [Parameter(Mandatory)] [string] $Shell,
    [ValidateSet("Auto", "ShellLauncher", "Winlogon", "None")] [string] $Mode = "Auto"
)
$ErrorActionPreference = "Stop"
try {
    if (-not [Environment]::Is64BitProcess -and [Environment]::Is64BitOperatingSystem) { throw "run me with the 64-bit PowerShell (Sysnative)" }
    if ($Mode -eq "None") { "shell left as it is"; exit 0 }
    if (-not (Test-Path $Shell)) { throw "$Shell not found" }

    $launcher = Get-WindowsOptionalFeature -Online -FeatureName Client-EmbeddedShellLauncher -ErrorAction SilentlyContinue
    $auto = $Mode -eq "Auto"
    if ($auto) { $Mode = if ($launcher) { "ShellLauncher" } else { "Winlogon" } }
    if ($Mode -eq "ShellLauncher") {
        if (-not $launcher) { throw "Shell Launcher is not part of this Windows edition (Enterprise, Education or IoT Enterprise); use Winlogon" }
        if ($launcher.State -ne "Enabled") {
            try {
                $r = Enable-WindowsOptionalFeature -Online -FeatureName Client-EmbeddedShellLauncher -All -NoRestart
                "enabled Client-EmbeddedShellLauncher (restart needed: $($r.RestartNeeded))"
            } catch {
                # Auto only: e.g. Windows Sandbox lists the feature but cannot install it.
                if (-not $auto) { throw }
                "Client-EmbeddedShellLauncher could not be enabled ($($_.Exception.Message)); using the Winlogon shell"
                $Mode = "Winlogon"
            }
        }
    }
    "mode $Mode"
    if ($Mode -eq "ShellLauncher") {
        $wesl = Get-CimClass -Namespace "root\StandardCimv2\Embedded" -ClassName WESL_UserSetting
        # A rerun: SetCustomShell refuses a SID that already has one.
        Invoke-CimMethod -CimClass $wesl -MethodName RemoveCustomShell -Arguments @{ Sid = "S-1-5-32-544" } -ErrorAction SilentlyContinue | Out-Null
        foreach ($call in @(
                @{ Method = "SetDefaultShell"; Arguments = @{ Shell = $Shell; DefaultAction = [int]0 } },
                @{ Method = "SetCustomShell"; Arguments = @{ Sid = "S-1-5-32-544"; Shell = $Shell; DefaultAction = [int]0 } },
                @{ Method = "SetEnabled"; Arguments = @{ Enabled = $true } })) {
            $r = Invoke-CimMethod -CimClass $wesl -MethodName $call.Method -Arguments $call.Arguments
            if ($r.ReturnValue -ne 0) { throw "WESL_UserSetting.$($call.Method) returned $($r.ReturnValue)" }
            "WESL_UserSetting.$($call.Method): ok"
        }
        $enabled = (Invoke-CimMethod -CimClass $wesl -MethodName IsEnabled).Enabled
        if (-not $enabled) { throw "Shell Launcher is not enabled after SetEnabled" }
    } else {
        $key = [Microsoft.Win32.RegistryKey]::OpenBaseKey("LocalMachine", "Registry64").OpenSubKey("SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", $true)
        $key.SetValue("Shell", "`"$Shell`"", "String")
        if ($key.GetValue("Shell") -ne "`"$Shell`"") { throw "Winlogon\Shell did not take the new value" }
        $key.Close()
    }
    "KioskShell is the shell for every user: $Shell"
    exit 0
} catch {
    [Console]::Error.WriteLine("set-kiosk-shell: $($_.Exception.Message)")
    exit 1
}
