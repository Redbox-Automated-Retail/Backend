# Shared helpers for Install-Kiosk.ps1 and its steps (Windows PowerShell 5.1).

$script:InstallerRoot = Split-Path $PSScriptRoot
$script:Reds = "C:\Program Files\Redbox\REDS"          # fixed: the update store's revlogs hold absolute C:\ paths
$script:RedboxProgramFiles = "C:\Program Files\Redbox"
$script:UpdateManagerBin = Join-Path $Reds "Update Manager\bin"
$script:RepositoryRoot = Join-Path $Reds "Update Manager\.store\.repository"
$script:EngineKey = "SOFTWARE\Redbox\REDS\Kiosk Engine"   # in the 32-bit registry view, where the x86 kiosk programs read

# Settings ---------------------------------------------------------------------------------------------------------

function Get-DefaultSettings {
    @{
        StoreId      = $null          # the kiosk ID / store number (required)
        DataZip      = $null          # a store-format seed: data-<date>.zip from Redbox or the backend's builder (required)
        KioskType    = "R630"         # R630 (90 slots per deck) or R504 (72)
        Environment  = "Production"   # Kiosk Engine\Store\Environment
        TestMode     = $false
        Reboot       = $true
        StateDir     = "C:\ProgramData\Redbox\installer"
        Backend = @{
            ProfileFile = $null       # kiosk profile from the backend (admin kiosk-profile / dashboard); null = skip step 40
            Address     = $null       # overrides the profile's hosts address (rb-reprovision --address)
            Reprovision = $null       # rb-reprovision.exe (kiosk/reprovision/build.ps1); default: payload\rb-reprovision\rb-reprovision.exe
        }
        Prerequisites = @{
            DotNetCoreRuntime = @{ Path = $null; Url = "https://builds.dotnet.microsoft.com/dotnet/Runtime/2.2.1/dotnet-runtime-2.2.1-win-x86.exe"
                                   Sha256 = "E4FB69F25185A22DB868F2514D7D60183874DFEE4E50E98AAD2E5DEE14202753" }
            AspNetCoreRuntime = @{ Path = $null; Url = "https://builds.dotnet.microsoft.com/dotnet/aspnetcore/Runtime/2.2.1/aspnetcore-runtime-2.2.1-win-x86.exe"
                                   Sha256 = "E5A1899DC9719548C94F03EF164056CBFEB2F6AC09CADF472970B64BE8A176C1" }
            DotNetFramework   = @{ Path = $null }   # NDP472 or later offline installer, only if the OS has < 4.7.2
            NetFx3            = "Enable"            # Enable | IfAvailable | Skip  (.NET 3.5, for HAL's 2.0 runtime)
            NetFx3Source      = $null               # DISM /Source for NetFx3 (sources\sxs of the Windows media)
            IngenicoDriver    = $null               # IngenicoUSBDrivers_3.14_setup.exe, run as /S /PORT=7 /PID=0057:7
        }
        User = @{
            Name          = "kiosk"     # the auto-logon user the shell and the engine run as; null = the current user
            Create        = $true
            PasswordFile  = $null       # file holding the password; null = a random one (stored only in the LSA secret)
            Administrator = $false      # Redbox demoted this user to Power Users (DemoteUser.lua)
            AutoLogon     = $true
        }
        Shell = @{
            KioskShell    = $null       # folder or zip with KioskShell.exe (Software: src/Tools/KioskShell)
            Mode          = "Auto"      # Auto (Shell Launcher if available, else Winlogon) | ShellLauncher | Winlogon | None
            Wallpaper     = $null       # copied to C:\Redbox\background.bmp (KioskShell's wallpaper)
            LockScreen    = $null       # copied to oobe\info\backgrounds\backgrounddefault.jpg
        }
        Hardening = @{
            DisableAutorun        = $true
            DisableAccessibilityHotkeys = $true
            DisableTaskManager    = $true
            DisableErrorDebugger  = $true
            CrashAutoReboot       = $true
            ServicesPipeTimeoutMs = 60000
            BrowserEmulationIE8   = $true
            MuteSound             = $true
            QuoteServicePaths     = $true
            AuditPolicy           = $false   # the original enabled success+failure auditing of every category
        }
        Hardware = @{
            Hal                 = $true     # run HAL.PackageInstaller (registers halsvc$default)
            HalConfig           = $null     # a kiosk's hal.xml to install before the package installer runs
            HalAllowV4Runtime   = $false    # let HAL (built for .NET 2.0) run on .NET 4 where 3.5 is missing
            CardReaderFirmware  = $false    # run DeviceService\bin\RBAInstaller.exe (updates the Ingenico reader)
            SimulateCardReader  = $null     # the engine's own card-reader simulator (no reader hardware); default: test mode
            DellBios            = @{ Enabled = $false; Command = $null; Arguments = $null }   # e.g. Dell Command | Configure cctk.exe
        }
        StartAfterInstall = $null       # start services and the engine at the end (default: only in test mode)
    }
}

function ConvertTo-Hashtable($o) {
    if ($null -eq $o) { return $null }
    if ($o -is [System.Management.Automation.PSCustomObject]) {
        $h = @{}; foreach ($p in $o.PSObject.Properties) { $h[$p.Name] = ConvertTo-Hashtable $p.Value }; return $h
    }
    if ($o -is [Array]) { return ,@($o | ForEach-Object { ConvertTo-Hashtable $_ }) }
    return $o
}

function Merge-Settings([hashtable] $defaults, [hashtable] $given, [string] $path = "") {
    foreach ($k in $given.Keys) {
        if (-not $defaults.ContainsKey($k)) { throw "unknown setting '$path$k'" }
        if ($defaults[$k] -is [hashtable] -and $given[$k] -is [hashtable]) { Merge-Settings $defaults[$k] $given[$k] "$path$k." | Out-Null }
        else { $defaults[$k] = $given[$k] }
    }
    return $defaults
}

function Resolve-SettingPath($cfg, $p) {
    if (-not $p) { return $null }
    $p = [Environment]::ExpandEnvironmentVariables($p)
    if (-not [IO.Path]::IsPathRooted($p)) { $p = Join-Path $cfg.SettingsDir $p }
    return [IO.Path]::GetFullPath($p)
}

function Read-InstallerSettings([string] $Path, [switch] $TestMode, [string] $StateDir) {
    $Path = (Resolve-Path $Path).Path
    $given = ConvertTo-Hashtable (Get-Content -Raw $Path | ConvertFrom-Json)
    $given.Remove('$comment') | Out-Null
    $cfg = Merge-Settings (Get-DefaultSettings) $given
    $cfg.SettingsPath = $Path
    $cfg.SettingsDir = Split-Path $Path
    $cfg.PendingReboot = $false
    if ($TestMode) { $cfg.TestMode = $true }
    if ($StateDir) { $cfg.StateDir = $StateDir }
    if ($cfg.TestMode) {
        # A VM or Windows Sandbox: nothing that needs hardware, no reboot, keep the desktop usable.
        $cfg.Reboot = $false
        if ($cfg.Prerequisites.NetFx3 -eq "Enable") { $cfg.Prerequisites.NetFx3 = "IfAvailable" }
        $cfg.Hardware.HalAllowV4Runtime = $true
        $cfg.Hardware.CardReaderFirmware = $false
        $cfg.Hardware.DellBios.Enabled = $false
        if ($null -eq $cfg.Hardware.SimulateCardReader) { $cfg.Hardware.SimulateCardReader = $true }
        if ($cfg.Shell.Mode -eq "Auto") { $cfg.Shell.Mode = "None" }
        $cfg.User.AutoLogon = $false
        if ($null -eq $cfg.StartAfterInstall) { $cfg.StartAfterInstall = $true }
    }
    if ($null -eq $cfg.StartAfterInstall) { $cfg.StartAfterInstall = $false }

    if (-not $cfg.StoreId -or "$($cfg.StoreId)" -notmatch '^\d+$') { throw "StoreId must be the kiosk's numeric ID" }
    if ($cfg.KioskType -notin "R630", "R504") { throw "KioskType must be R630 or R504" }
    if ($cfg.Shell.Mode -notin "Auto", "ShellLauncher", "Winlogon", "None") { throw "Shell.Mode must be Auto, ShellLauncher, Winlogon or None" }
    if ($cfg.Prerequisites.NetFx3 -notin "Enable", "IfAvailable", "Skip") { throw "Prerequisites.NetFx3 must be Enable, IfAvailable or Skip" }
    foreach ($k in "DataZip") { $cfg[$k] = Resolve-SettingPath $cfg $cfg[$k] }
    if (-not $cfg.DataZip -or -not (Test-Path $cfg.DataZip)) { throw "DataZip not found: $($cfg.DataZip)" }
    $cfg.Backend.ProfileFile = Resolve-SettingPath $cfg $cfg.Backend.ProfileFile
    $cfg.Backend.Reprovision = Resolve-SettingPath $cfg $(if ($cfg.Backend.Reprovision) { $cfg.Backend.Reprovision } else { "rb-reprovision\rb-reprovision.exe" })
    $cfg.Shell.KioskShell = Resolve-SettingPath $cfg $cfg.Shell.KioskShell
    $cfg.Shell.Wallpaper = Resolve-SettingPath $cfg $cfg.Shell.Wallpaper
    $cfg.Shell.LockScreen = Resolve-SettingPath $cfg $cfg.Shell.LockScreen
    $cfg.User.PasswordFile = Resolve-SettingPath $cfg $cfg.User.PasswordFile
    $cfg.Hardware.HalConfig = Resolve-SettingPath $cfg $cfg.Hardware.HalConfig
    foreach ($k in "DotNetCoreRuntime", "AspNetCoreRuntime", "DotNetFramework") {
        $cfg.Prerequisites[$k].Path = Resolve-SettingPath $cfg $cfg.Prerequisites[$k].Path
    }
    $cfg.Prerequisites.IngenicoDriver = Resolve-SettingPath $cfg $cfg.Prerequisites.IngenicoDriver
    New-Item -ItemType Directory -Force $cfg.StateDir | Out-Null
    return $cfg
}

# Logging and state ------------------------------------------------------------------------------------------------

function Start-InstallerLog($cfg) { $global:RbInstallerLog = Join-Path $cfg.StateDir "install.log" }

function Write-Log([string] $Message, [ValidateSet("Info", "Warn", "Error")] [string] $Level = "Info") {
    $line = "{0:yyyy-MM-dd HH:mm:ss}|{1,-5}|{2}" -f (Get-Date), $Level, $Message
    $log = Get-Variable RbInstallerLog -Scope Global -ValueOnly -ErrorAction SilentlyContinue
    # Retried: the log may be read by someone else at the same moment (e.g. a host watching a mapped folder).
    if ($log) { for ($i = 0; $i -lt 20; $i++) { try { [IO.File]::AppendAllText($log, $line + "`r`n"); break } catch { Start-Sleep -Milliseconds 100 } } }
    switch ($Level) { "Error" { Write-Host $line -ForegroundColor Red } "Warn" { Write-Host $line -ForegroundColor Yellow } default { Write-Host $line } }
}

function Get-StateFile($cfg) { Join-Path $cfg.StateDir "steps.json" }

function Test-StepDone($cfg, $name) {
    $f = Get-StateFile $cfg
    if (-not (Test-Path $f)) { return $false }
    return [bool]((Get-Content -Raw $f | ConvertFrom-Json).PSObject.Properties.Name -contains $name)
}

function Set-StepDone($cfg, $name) {
    $f = Get-StateFile $cfg
    $h = @{}; if (Test-Path $f) { $h = ConvertTo-Hashtable (Get-Content -Raw $f | ConvertFrom-Json) }
    $h[$name] = (Get-Date).ToString("o")
    $h | ConvertTo-Json | Set-Content -Path $f -Encoding UTF8
}

# Processes, files, downloads --------------------------------------------------------------------------------------

function Invoke-Program([string] $FilePath, [string[]] $Arguments = @(), [string] $WorkingDirectory = $null,
                        [int[]] $OkExitCodes = @(0), [int] $TimeoutSeconds = 3600, [switch] $AllowFailure) {
    $quoted = $Arguments | ForEach-Object { if ($_ -match '[\s"]' -and $_ -notmatch '^".*"$') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }
    Write-Log "   run: $FilePath $($quoted -join ' ')"
    $psi = New-Object Diagnostics.ProcessStartInfo $FilePath, ($quoted -join ' ')
    $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.CreateNoWindow = $true
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
    $p = [Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEndAsync(); $err = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($TimeoutSeconds * 1000)) { $p.Kill(); throw "$FilePath did not finish in $TimeoutSeconds s" }
    $p.WaitForExit()
    # The full output goes to its own file; install.log gets the last lines.
    $lines = @(($out.Result + $err.Result) -split "`r?`n" | Where-Object { $_.Trim() })
    $logs = Join-Path (Split-Path (Get-Variable RbInstallerLog -Scope Global -ValueOnly)) "logs"
    New-Item -ItemType Directory -Force $logs | Out-Null
    $file = Join-Path $logs ("{0:yyyyMMdd-HHmmss}-{1}.log" -f (Get-Date), [IO.Path]::GetFileNameWithoutExtension($FilePath))
    [IO.File]::WriteAllLines($file, [string[]]$lines)
    if ($lines.Count -gt 15) { Write-Log "   | ... $($lines.Count) lines in $file" }
    foreach ($l in ($lines | Select-Object -Last 15)) { Write-Log "   | $l" }
    if ($OkExitCodes -notcontains $p.ExitCode) {
        if ($AllowFailure) { Write-Log "   exit code $($p.ExitCode) (ignored)" -Level Warn } else { throw "$FilePath exited with $($p.ExitCode)" }
    }
    return $p.ExitCode
}

function Get-PinnedFile($cfg, [hashtable] $spec, [string] $name) {
    # A local Path wins; otherwise download Url into the state folder. Either way the SHA-256 must match if given.
    $file = $spec.Path
    if (-not $file) {
        if (-not $spec.Url) { return $null }
        $dir = Join-Path $cfg.StateDir "downloads"; New-Item -ItemType Directory -Force $dir | Out-Null
        $file = Join-Path $dir $name
        if (-not (Test-Path $file) -or ($spec.Sha256 -and (Get-FileHash $file -Algorithm SHA256).Hash -ne $spec.Sha256)) {
            Write-Log "   download $($spec.Url)"
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            (New-Object Net.WebClient).DownloadFile($spec.Url, $file)
        }
    }
    if (-not (Test-Path $file)) { throw "$name not found: $file" }
    if ($spec.Sha256) {
        $h = (Get-FileHash $file -Algorithm SHA256).Hash
        if ($h -ne $spec.Sha256) { throw "$file has SHA-256 $h, expected $($spec.Sha256)" }
    }
    return $file
}

function Expand-ZipTo([string] $Zip, [string] $Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    New-Item -ItemType Directory -Force $Destination | Out-Null
    $z = [IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        foreach ($e in $z.Entries) {
            $target = [IO.Path]::GetFullPath((Join-Path $Destination $e.FullName))
            if (-not $target.StartsWith([IO.Path]::GetFullPath($Destination), [StringComparison]::OrdinalIgnoreCase)) { throw "zip entry outside the destination: $($e.FullName)" }
            if ($e.FullName.EndsWith("/") -or $e.FullName.EndsWith("\")) { New-Item -ItemType Directory -Force $target | Out-Null; continue }
            New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $target, $true)
        }
    } finally { $z.Dispose() }
}

# Registry ---------------------------------------------------------------------------------------------------------

function Open-LocalMachineKey([string] $SubKey, [switch] $View64) {
    $view = if ($View64) { [Microsoft.Win32.RegistryView]::Registry64 } else { [Microsoft.Win32.RegistryView]::Registry32 }
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
    return $base.CreateSubKey($SubKey)
}

function Set-MachineValue([string] $SubKey, [string] $Name, $Value, [string] $Kind = "String", [switch] $View64) {
    # Default: the 32-bit view (WOW6432Node), as the x86 installer front end and the kiosk programs see HKLM\SOFTWARE.
    $k = Open-LocalMachineKey $SubKey -View64:$View64
    try { $k.SetValue($Name, $Value, [Microsoft.Win32.RegistryValueKind]$Kind) } finally { $k.Close() }
    Write-Log ("   HKLM{0}\{1}\{2} = {3}" -f $(if ($View64) { "" } else { "(32)" }), $SubKey, $Name, $(if ($Value -is [Array]) { $Value -join ';' } else { $Value }))
}

function Invoke-WithUserHive($cfg, [scriptblock] $Action) {
    # HKCU settings of the original installer went to the user running it, which on a kiosk was the auto-logon user.
    # Here they go to the kiosk user's hive (loaded if needed) and the Default profile, so a later first logon gets them.
    $targets = @()
    $sid = Get-KioskUserSid $cfg
    if ($sid -and (Test-Path "Registry::HKEY_USERS\$sid")) { $targets += "HKEY_USERS\$sid" }
    elseif ($sid) {
        $profile = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid" -ErrorAction SilentlyContinue).ProfileImagePath
        if ($profile -and (Test-Path "$profile\NTUSER.DAT")) { $targets += @{ Load = "$profile\NTUSER.DAT"; Name = "RBKioskUser" } }
    }
    if (-not $cfg.User.Name) { $targets += "HKEY_CURRENT_USER" }
    $targets += @{ Load = "C:\Users\Default\NTUSER.DAT"; Name = "RBDefaultUser" }
    foreach ($t in $targets) {
        $root = $t
        if ($t -is [hashtable]) {
            & reg.exe load "HKU\$($t.Name)" $t.Load | Out-Null
            if ($LASTEXITCODE) { Write-Log "   could not load $($t.Load)" -Level Warn; continue }
            $root = "HKEY_USERS\$($t.Name)"
        }
        try { & $Action "Registry::$root" } finally {
            if ($t -is [hashtable]) { [GC]::Collect(); [GC]::WaitForPendingFinalizers(); & reg.exe unload "HKU\$($t.Name)" | Out-Null }
        }
    }
}

function Set-UserValue([string] $Root, [string] $SubKey, [string] $Name, $Value, [string] $Kind = "String") {
    $path = "$Root\$SubKey"
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    New-ItemProperty -Path $path -Name $Name -Value $Value -PropertyType $Kind -Force | Out-Null
}

# Users and services -----------------------------------------------------------------------------------------------

function Get-KioskUserSid($cfg) {
    if (-not $cfg.User.Name) { return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
    try { return (New-Object Security.Principal.NTAccount $cfg.User.Name).Translate([Security.Principal.SecurityIdentifier]).Value } catch { return $null }
}

function Set-ServiceDefinition([string] $Name, [string] $DisplayName, [string] $BinaryPath, [string] $Start = "delayed-auto") {
    if (Get-Service $Name -ErrorAction SilentlyContinue) {
        Invoke-Program "$env:SystemRoot\System32\sc.exe" @("config", $Name, "binPath=", "`"$BinaryPath`"", "start=", $Start) | Out-Null
    } else {
        Invoke-Program "$env:SystemRoot\System32\sc.exe" @("create", $Name, "displayname=", $DisplayName, "start=", $Start, "binPath=", "`"$BinaryPath`"") | Out-Null
    }
}

function Restart-KioskIfAllowed($cfg) {
    if ($cfg.Reboot) { Write-Log "rebooting in 15 s"; & shutdown.exe /r /t 15 /c "Redbox kiosk installation finished" }
    else { Write-Log "a reboot is needed to finish (Reboot is off)" -Level Warn }
}
