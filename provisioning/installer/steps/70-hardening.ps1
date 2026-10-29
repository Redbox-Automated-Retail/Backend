# Kiosk and PCI settings of the original installer-steps.lua, each switchable in Settings.Hardening.
# Left out on purpose: Redbox's card-reader list (HKLM\SOFTWARE\Redbox\CRList), Blink removal, nxlog/Kaseya/McAfee.
param($Config)
$cfg = $Config
$h = $cfg.Hardening

if ($h.DisableAutorun) {      # "Turn off Autorun"
    Set-MachineValue "SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer" "NoDriveTypeAutoRun" 255 DWord -View64
}
if ($h.DisableErrorDebugger) {    # "Install Redbox Shell": no just-in-time debugger dialog
    Set-MachineValue "SOFTWARE\Microsoft\Windows NT\CurrentVersion\AeDebug" "Auto" "0" -View64
}
if ($h.CrashAutoReboot) {     # "Setup Card Reader"
    Set-MachineValue "SYSTEM\CurrentControlSet\Control\CrashControl" "AutoReboot" 1 DWord -View64
}
if ($h.ServicesPipeTimeoutMs) {   # "Kiosk Engine Configuration": slow service starts
    Set-MachineValue "SYSTEM\CurrentControlSet\Control" "ServicesPipeTimeout" ([int]$h.ServicesPipeTimeoutMs) DWord -View64
}
if ($h.BrowserEmulationIE8) {     # the engine's embedded browser in IE8 mode (router configuration pages)
    foreach ($view64 in $true, $false) {
        Set-MachineValue "SOFTWARE\Microsoft\Internet Explorer\Main\FeatureControl\FEATURE_BROWSER_EMULATION" "KioskEngine.exe" 8888 DWord -View64:$view64
    }
}
if ($h.AuditPolicy) {         # AuditPol.exe /logon:all /sam:all ... (XP-era tool); the same with today's auditpol
    Invoke-Program "$env:SystemRoot\System32\auditpol.exe" @("/set", "/category:*", "/success:enable", "/failure:enable") | Out-Null
}
if ($h.QuoteServicePaths) {   # "Quoted Strings" (PCI): unquoted service paths with spaces
    foreach ($svc in Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Services") {
        $p = (Get-ItemProperty $svc.PSPath -Name ImagePath -ErrorAction SilentlyContinue).ImagePath
        if ($p -and $p -notmatch '^"' -and $p -match '^(?<exe>[A-Za-z]:\\[^"]*?\.exe)(?<rest>.*)$' -and $Matches.exe -match ' ' -and $Matches.exe -match 'Redbox|Kaseya|MICROTOUCH') {
            Set-ItemProperty $svc.PSPath -Name ImagePath -Value ('"{0}"{1}' -f $Matches.exe, $Matches.rest)
            Write-Log "   quoted ImagePath of $($svc.PSChildName)"
        }
    }
}

# Per-user settings (the original wrote them to HKCU of the auto-logon user it ran as).
Invoke-WithUserHive $cfg {
    param($root)
    if ($h.DisableTaskManager) { Set-UserValue $root "Software\Microsoft\Windows\CurrentVersion\Policies\System" "DisableTaskMgr" 1 DWord }
    if ($h.DisableAccessibilityHotkeys) {   # "Disable Sticky Keys" (PCI), the original values
        $a = "Control Panel\Accessibility"
        Set-UserValue $root "$a\StickyKeys" "Flags" "506"
        Set-UserValue $root "$a\ToggleKeys" "Flags" "58"
        Set-UserValue $root "$a\Keyboard Response" "Flags" "122"
        Set-UserValue $root "$a\HighContrast" "Flags" "4218"
        Set-UserValue $root "$a\MouseKeys" "Flags" "58"
    }
    # "Setup Card Reader": wide scroll bars for the touch screen.
    Set-UserValue $root "Control Panel\Desktop\WindowsMetrics" "ScrollWidth" "-375"
    Set-UserValue $root "Control Panel\Desktop\WindowsMetrics" "ScrollHeight" "-375"
    Write-Log "   per-user settings in $root"
}

if ($h.MuteSound) {    # "Mute PC sound": the default render device's master volume muted
    Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator { }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int NotImpl1(); [PreserveSig] int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice dev); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { [PreserveSig] int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume { int a(); int b(); int c(); int d(); int e(); int f(); int g(); int h(); int i(); int j(); int k();
    [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid ctx); }
public static class RbAudio {
    public static bool Mute() {
        var en = (IMMDeviceEnumerator)new MMDeviceEnumerator(); IMMDevice dev;
        if (en.GetDefaultAudioEndpoint(0, 1, out dev) != 0 || dev == null) return false;
        var iid = typeof(IAudioEndpointVolume).GUID; object o;
        if (dev.Activate(ref iid, 23, IntPtr.Zero, out o) != 0) return false;
        var g = Guid.Empty; return ((IAudioEndpointVolume)o).SetMute(true, ref g) == 0;
    }
}
'@
    try { Write-Log "   sound muted: $([RbAudio]::Mute())" } catch { Write-Log "   no audio device to mute" -Level Warn }
}
