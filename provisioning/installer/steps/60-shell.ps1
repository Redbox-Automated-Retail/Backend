# Kiosk user, autologon and KioskShell as the shell (original: "Install Redbox Shell", "Install lock screen
# wallpaper"; the auto-logon user came with Redbox's Windows image and was later demoted by DemoteUser.lua).
param($Config)
$cfg = $Config
$u = $cfg.User
$shellDir = Join-Path $RedboxProgramFiles "KioskShell"
$shellExe = Join-Path $shellDir "KioskShell.exe"

# KioskShell (Software: src/Tools/KioskShell; Redbox shipped it as kioskshell.zip inside the installer).
if ($cfg.Shell.KioskShell) {
    New-Item -ItemType Directory -Force $shellDir | Out-Null
    if ($cfg.Shell.KioskShell -like "*.zip") { Expand-ZipTo $cfg.Shell.KioskShell $shellDir }
    else { Copy-Item (Join-Path $cfg.Shell.KioskShell "*") $shellDir -Recurse -Force }
    if (-not (Test-Path $shellExe)) { throw "no KioskShell.exe in $($cfg.Shell.KioskShell)" }
    Write-Log "   KioskShell in $shellDir"
} elseif ($cfg.Shell.Mode -ne "None") { throw "Shell.KioskShell is needed for Shell.Mode $($cfg.Shell.Mode)" }

New-Item -ItemType Directory -Force "C:\Redbox" | Out-Null
if ($cfg.Shell.Wallpaper) { Copy-Item $cfg.Shell.Wallpaper "C:\Redbox\background.bmp" -Force }   # KioskShell's Wallpaper setting
if ($cfg.Shell.LockScreen) {
    $bg = "$env:SystemRoot\System32\oobe\info\backgrounds"
    New-Item -ItemType Directory -Force $bg | Out-Null
    Copy-Item $cfg.Shell.LockScreen "$bg\backgrounddefault.jpg" -Force
    Set-MachineValue "SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI\Background" "OEMBackground" 1 DWord -View64
}

# The kiosk user.
$password = $null
if ($u.Name) {
    if ($u.PasswordFile) { $password = (Get-Content -Raw $u.PasswordFile).Trim() }
    $existing = Get-LocalUser -Name $u.Name -ErrorAction SilentlyContinue
    if (-not $existing -and $u.Create) {
        if (-not $password) {
            # Never written anywhere but the LSA secret below: nobody needs to type it on a kiosk.
            $bytes = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
            $password = [Convert]::ToBase64String($bytes) + "!a1"
        }
        New-LocalUser -Name $u.Name -Password (ConvertTo-SecureString $password -AsPlainText -Force) -PasswordNeverExpires `
            -AccountNeverExpires -Description "Redbox kiosk user" | Out-Null
        Write-Log "   created local user $($u.Name)"
    } elseif ($existing -and $password) {
        Set-LocalUser -Name $u.Name -Password (ConvertTo-SecureString $password -AsPlainText -Force)
    } elseif (-not $existing) { throw "user $($u.Name) does not exist and User.Create is false" }
    $usersGroup = (New-Object Security.Principal.SecurityIdentifier "S-1-5-32-545").Translate([Security.Principal.NTAccount]).Value.Split('\')[-1]
    $adminsGroup = (New-Object Security.Principal.SecurityIdentifier "S-1-5-32-544").Translate([Security.Principal.NTAccount]).Value.Split('\')[-1]
    Add-LocalGroupMember -Group $usersGroup -Member $u.Name -ErrorAction SilentlyContinue
    if ($u.Administrator) { Add-LocalGroupMember -Group $adminsGroup -Member $u.Name -ErrorAction SilentlyContinue }
    else { Remove-LocalGroupMember -Group $adminsGroup -Member $u.Name -ErrorAction SilentlyContinue }
}

# Autologon, the way Sysinternals Autologon (which Redbox used) does it: the password in the LSA secret
# DefaultPassword, not in the registry.
if ($u.AutoLogon) {
    if (-not $u.Name) { throw "User.AutoLogon needs User.Name" }
    if (-not $password) { throw "User.AutoLogon for an existing user needs User.PasswordFile" }
    Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public static class RbLsa {
    [StructLayout(LayoutKind.Sequential)] struct LSA_UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
    [StructLayout(LayoutKind.Sequential)] struct LSA_OBJECT_ATTRIBUTES { public int Length; public IntPtr RootDirectory; public IntPtr ObjectName; public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService; }
    [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr system, ref LSA_OBJECT_ATTRIBUTES attr, uint access, out IntPtr handle);
    [DllImport("advapi32.dll")] static extern uint LsaStorePrivateData(IntPtr handle, ref LSA_UNICODE_STRING key, ref LSA_UNICODE_STRING data);
    [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr handle);
    [DllImport("advapi32.dll")] static extern int LsaNtStatusToWinError(uint status);
    static LSA_UNICODE_STRING Str(string s) { var u = new LSA_UNICODE_STRING(); u.Buffer = Marshal.StringToHGlobalUni(s); u.Length = (ushort)(s.Length * 2); u.MaximumLength = (ushort)(s.Length * 2 + 2); return u; }
    public static void Store(string key, string value) {
        var attr = new LSA_OBJECT_ATTRIBUTES(); IntPtr h;
        uint st = LsaOpenPolicy(IntPtr.Zero, ref attr, 0x00000020 /* POLICY_CREATE_SECRET */, out h);
        if (st != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(st));
        var k = Str(key); var d = Str(value);
        try { st = LsaStorePrivateData(h, ref k, ref d); if (st != 0) throw new System.ComponentModel.Win32Exception(LsaNtStatusToWinError(st)); }
        finally { Marshal.FreeHGlobal(k.Buffer); Marshal.FreeHGlobal(d.Buffer); LsaClose(h); }
    }
}
'@
    $winlogon = "SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
    Set-MachineValue $winlogon "AutoAdminLogon" "1" -View64
    Set-MachineValue $winlogon "DefaultUserName" $u.Name -View64
    Set-MachineValue $winlogon "DefaultDomainName" $env:COMPUTERNAME -View64
    $k = Open-LocalMachineKey $winlogon -View64; $k.DeleteValue("DefaultPassword", $false); $k.Close()
    [RbLsa]::Store("DefaultPassword", $password)
    Write-Log "   autologon as $($u.Name) (password in the LSA secret)"
}

# The shell. The original used Shell Launcher (kernel.replaceshellwith) for every user, Administrators included.
$mode = $cfg.Shell.Mode
$launcher = Get-WindowsOptionalFeature -Online -FeatureName Client-EmbeddedShellLauncher -ErrorAction SilentlyContinue
if ($mode -eq "Auto") { $mode = if ($launcher) { "ShellLauncher" } else { "Winlogon" } }
switch ($mode) {
    "ShellLauncher" {
        if (-not $launcher) { throw "Shell Launcher is not available in this Windows edition (Enterprise, Education or IoT Enterprise); use Shell.Mode Winlogon" }
        if ($launcher.State -ne "Enabled") {
            $r = Enable-WindowsOptionalFeature -Online -FeatureName Client-EmbeddedShellLauncher -All -NoRestart
            if ($r.RestartNeeded) { $cfg.PendingReboot = $true }
        }
        # The same three calls as ShellLauncherFunctions.ReplaceShellWith (DefaultAction 0 = restart the shell).
        $wesl = Get-CimClass -Namespace "root\StandardCimv2\Embedded" -ClassName WESL_UserSetting
        Invoke-CimMethod -CimClass $wesl -MethodName SetDefaultShell -Arguments @{ Shell = $shellExe; DefaultAction = [int]0 } | Out-Null
        Invoke-CimMethod -CimClass $wesl -MethodName SetCustomShell -Arguments @{ Sid = "S-1-5-32-544"; Shell = $shellExe; DefaultAction = [int]0 } | Out-Null
        Invoke-CimMethod -CimClass $wesl -MethodName SetEnabled -Arguments @{ Enabled = $true } | Out-Null
        Write-Log "   Shell Launcher: KioskShell for every user"
    }
    "Winlogon" {
        # Without Shell Launcher (Windows Pro): the machine-wide Winlogon shell, which also applies to everyone.
        Set-MachineValue "SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" "Shell" "`"$shellExe`"" -View64
    }
    "None" { Write-Log "   shell left as it is" }
}
Get-ScheduledTask -TaskName "Bootstrap Store" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
