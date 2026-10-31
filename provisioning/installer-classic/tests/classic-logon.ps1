# Windows Sandbox LogonCommand for the classic installer (provisioning\installer\tests\sandbox.ps1 up -Classic <dir>).
# Starts the hardware emulator if the tools are mapped, runs setup.exe from a copy of the mapped installer folder and
# goes through its wizard the way a technician would (UI Automation: Next on every page, Finish at the end, a
# screenshot of each page), then writes the kiosk checks and keeps serving C:\rb\control\inbox (loop.ps1).
# Nothing here works around the installer: no .NET 3.5 faking, no blocked reboot (the test-mode build has
# reboot="Never" and starts everything instead), no hosts or CA set up beforehand.
Set-ExecutionPolicy -Scope Process Bypass -Force
$control = "C:\rb\control"; $out = Join-Path $control "classic"
New-Item -ItemType Directory -Force $out, (Join-Path $control "outbox"), "C:\Program Files\Redbox\KioskLogs" | Out-Null
$log = Join-Path $out "harness.log"
function L($m) { "$(Get-Date -Format T) $m" | Add-Content $log }
L "start as $(whoami)"
if (Test-Path C:\rb\tools\python\python.exe) {
    foreach ($s in "10-com0com.ps1", "20-hw-emu.ps1") { try { & "C:\rb\clients\sandbox\steps\$s" *>&1 | Out-String | Add-Content $log } catch { L "ERROR $s $_" } }
}

$setup = "C:\rbsetup"
New-Item -ItemType Directory -Force $setup | Out-Null
Copy-Item C:\rb\classic\* $setup -Force
L "setup folder: $((Get-ChildItem $setup | ForEach-Object Name) -join ', ')"

# The front end logs to REDS\Update Manager\logs; copy them out as they grow.
Start-Job -ScriptBlock {
    while ($true) {
        foreach ($p in "C:\Program Files\Redbox\REDS\Update Manager\logs", "C:\Program Files\Redbox\REDS\Update Manager\installGuid.txt") {
            if (Test-Path $p) { Copy-Item $p "C:\rb\control\classic\" -Recurse -Force -ErrorAction SilentlyContinue }
        }
        Start-Sleep 5
    }
} | Out-Null

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Drawing, System.Windows.Forms
$ae = [Windows.Automation.AutomationElement]
$tree = [Windows.Automation.TreeScope]
function Save-Screen([string] $name) {
    $b = [Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object Drawing.Bitmap $b.Width, $b.Height
    $g = [Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Location, [Drawing.Point]::Empty, $b.Size); $g.Dispose()
    $bmp.Save((Join-Path $out "$name.png"), [Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
}
Add-Type -Namespace RbTest -Name Mouse -MemberDefinition @"
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
[DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, System.UIntPtr extra);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr hwnd);
"@
# The wizard's buttons (Divelements.WizardFramework) are drawn by the wizard, not Win32 buttons: look for any
# element by name, and click it where it is.
function Get-Buttons($window) {
    @($window.FindAll($tree::Descendants, [Windows.Automation.Condition]::TrueCondition) |
        Where-Object { $_.Current.Name -match '^(&)?(Next|Finish|OK|Yes)\b' -and -not $_.Current.BoundingRectangle.IsEmpty })
}
function Click($button) {
    try { ($button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)).Invoke(); return } catch { }
    $r = $button.Current.BoundingRectangle
    [RbTest.Mouse]::SetCursorPos([int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2)) | Out-Null
    [RbTest.Mouse]::mouse_event(0x2, 0, 0, 0, [UIntPtr]::Zero); [RbTest.Mouse]::mouse_event(0x4, 0, 0, 0, [UIntPtr]::Zero)
}

L "running setup.exe (the wizard)"
$p = Start-Process "$setup\setup.exe" -ArgumentList "--nosplash", "--log:$out\rix.log" -WorkingDirectory $setup -PassThru
$page = 0; $lastTitle = ""; $finished = $false; $tick = 0
while (-not $p.HasExited) {
    Start-Sleep 2
    if ((++$tick % 30) -eq 0) {
        # Every minute: what is on screen, for when the wizard waits on something the driver does not know.
        Save-Screen ("tick-{0:000}" -f ($tick / 30))
        $all = $ae::RootElement.FindAll($tree::Children, [Windows.Automation.Condition]::TrueCondition)
        L ("windows: " + (($all | ForEach-Object { "[$($_.Current.ProcessId)] $($_.Current.Name)" }) -join "; "))
    }
    # The wizard's windows, and the front end's (only its message boxes are answered).
    $ids = @(Get-Process setup, installer-front-end -ErrorAction SilentlyContinue | ForEach-Object Id)
    $windows = @(foreach ($id in $ids) {
        $ae::RootElement.FindAll($tree::Children, (New-Object Windows.Automation.PropertyCondition($ae::ProcessIdProperty, [int]$id)))
    })
    foreach ($w in $windows) {
        $buttons = @(Get-Buttons $w)
        $names = $buttons | ForEach-Object { $_.Current.Name }
        $text = ($w.FindAll($tree::Descendants, [Windows.Automation.Condition]::TrueCondition) | ForEach-Object { $_.Current.Name } | Where-Object { $_ } | Select-Object -Unique) -join " | "
        if (-not ($names -match "Next|Finish") -and ($names -match "^(&)?OK")) {
            # A message box of the host or its scripts: record it and acknowledge it.
            Save-Screen ("{0:00}-messagebox" -f ++$page); L "message box '$($w.Current.Name)': $text"
            Click ($buttons | Where-Object { $_.Current.Name -match "^(&)?OK" } | Select-Object -First 1); continue
        }
        $next = $buttons | Where-Object { $_.Current.Name -match '^(&)?Next' -and $_.Current.IsEnabled } | Select-Object -First 1
        $finish = $buttons | Where-Object { $_.Current.Name -match '^(&)?Finish' -and $_.Current.IsEnabled } | Select-Object -First 1
        if (-not $next -and -not $finish) { continue }
        if ($text -ne $lastTitle) { Save-Screen ("{0:00}-page" -f ++$page); L "page ${page}: $text"; $lastTitle = $text }
        if ($finish) {
            # In test mode the engine is running by now and its window, which is topmost, covers the wizard: press
            # Enter (Finish is the last page's accept button) instead of clicking.
            [RbTest.Mouse]::SetForegroundWindow([IntPtr]$w.Current.NativeWindowHandle) | Out-Null
            Start-Sleep -Milliseconds 300; [Windows.Forms.SendKeys]::SendWait("{ENTER}")
            $finished = $true; L "pressed Finish"
        }
        else { Click $next; L "clicked Next" }
    }
    # The date and time page opens the clock settings; close them again.
    Get-Process SystemSettings, rundll32 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
L "setup.exe exit $($p.ExitCode) (Finish clicked: $finished)"
Start-Sleep 60

# The kiosk checks (the same ones as the Install-Kiosk.ps1 run).
& {
    "== services"
    Get-Service 'halsvc$default', 'updatemgrd$default', 'device$service', 'kioskclient$service', 'updateclient$service' -ErrorAction SilentlyContinue |
        Format-Table -AutoSize Status, StartType, Name | Out-String -Width 200
    "== shell"
    "Winlogon\Shell: " + (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon").Shell
    try { "Shell Launcher: " + ((Get-CimInstance -Namespace root\StandardCimv2\Embedded -ClassName WESL_UserSetting -ErrorAction Stop | Out-String).Trim()) } catch { "Shell Launcher: not available" }
    "== engine registry (32-bit view)"
    $k = "HKLM:\SOFTWARE\WOW6432Node\Redbox\REDS\Kiosk Engine"
    "Store: " + ((Get-ItemProperty "$k\Store" | Select-Object ID, MaxSlots, Environment, WatchDogEnabled | Out-String).Trim())
    "Broker: " + (Get-ItemProperty "$k\Remote Services\Reservation Services").BrokerServicesURL
    "KioskServices: " + (Get-ItemProperty "$k\Remote Services\Store Services").KioskServicesURL
    "MaxSlots in the 64-bit view: " + (Get-ItemProperty "HKLM:\SOFTWARE\Redbox\REDS\Kiosk Engine\Store" -ErrorAction SilentlyContinue).MaxSlots
    "== .NET"
    ".NET 3.5 (NetFx3): " + (Get-WindowsOptionalFeature -Online -FeatureName NetFx3 -ErrorAction SilentlyContinue).State
    "OnlyUseLatestCLR: " + (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\.NETFramework" -ErrorAction SilentlyContinue).OnlyUseLatestCLR
    "== processes"
    Get-Process halsvc-win32, updatemgrd, kioskengine, DeviceService.WebApi, KioskClientService.Api, UpdateClientService.API -ErrorAction SilentlyContinue |
        Format-Table -AutoSize Id, ProcessName, StartTime | Out-String -Width 200
    "== C:\.post-install: " + (Get-Content C:\.post-install -ErrorAction SilentlyContinue)
} | Out-File -Width 300 (Join-Path $out "checks.txt")
L "checks written"
Save-Screen "99-after"
& C:\rb\clients\sandbox\loop.ps1
