# Classic store installer (Redbox's installer, modernized)

Redbox's own Windows 10 store installer (`Windows10-Store-Installer-3.1.0.48-setup.exe`), forked so that it gives a
working kiosk on Windows 10 (1903 or later) and Windows 11 against this backend. It keeps Redbox's setup host (RIX),
its wizard pages, its front end (`installer-front-end.exe`) and its `installer-steps.lua` flow; the
[What changed](#what-changed-from-the-original) table lists every difference.

It sits beside [`../installer/Install-Kiosk.ps1`](../installer/README.md), our own rewrite, which it does not
replace. Both take the same three inputs (a data zip, a kiosk profile, a KioskShell build) and end with the same
kiosk.

The original is decompiled and documented in the Software repository (the `software/` submodule):
`software/docs/systems/store-installer.md`, sources in `software/src/Installer`. That copy stays as it is: this
folder only holds the files that differ, and the build compiles the rest from the submodule.

## What is here

| Path | What it is |
|---|---|
| `host/host.csproj` | The RIX setup host, built for .NET Framework 4.8 from `software/src/Installer/host` with three files of this folder in place of the originals: `VsaScriptingHost.cs` (the JScript.NET engine, see below), `Program.cs` (no .NET 3.5 check) and `ScriptKernelObject.cs` (`MaxSlots` in the 32-bit registry view; `readJsonValue` for the profile page). ILRepack merges it into one `host.exe`, as Redbox shipped it. |
| `rixc/` | The RIX compiler, which Redbox did not ship: setup XML + files -> setup exe (`PREREQ/SCRIPT`, `PREREQ/PROPS`, `FILE/NNNN` resources, layout in store-installer.md section 2.1). |
| `setup/store-installer.xml` | The setup script (Redbox's `PREREQ/SCRIPT`), forked. |
| `scripts/` | The front end's Lua scripts (`post-install.lua`, `installer-utils.lua`, `installer-steps.lua`), forked, and two PowerShell helpers the steps call: `set-kiosk-shell.ps1`, `retarget-netfx2.ps1`. `installer-heading.lua` is written by the build. |
| `update-manager/` | The front end's two config files (Redbox's settings; nothing secret). |
| `Build-ClassicInstaller.ps1` | Builds everything and puts one kiosk's installer in a folder. |
| `tests/classic-logon.ps1` | Windows Sandbox logon script: runs `setup.exe` and goes through its wizard. |

## Building

Needs: Visual Studio 2022 or later (or the Build Tools) with the .NET desktop workload (MSBuild; `dotnet build` cannot
build the host's .resx files), the `software` submodule, an update-store seed, and the backend running (for the
kiosk profile; or pass `-ProfileFile`).

```powershell
git submodule update --init software
.\scripts\rb.ps1 up
.\provisioning\installer-classic\Build-ClassicInstaller.ps1 -Out E:\kiosk-12345 -StoreId 12345 `
    -DataZip D:\seeds\data-572024.zip -Address 10.0.0.5 -HalConfig D:\kiosk-12345\hal.xml
```

It builds, in this order:

1. `rixc` and the host (`host\bin\Release\net48\merged\host.exe`);
2. the front end from `software/src/UpdateManager/installer-front-end`, merged into one exe as Redbox shipped it, with
   `Quartz.dll` and `Common.Logging*.dll` beside it;
3. `lua51.dll` and `xdelta3.exe`, rebuilt from the seed's `kop-update-manager` repository (seed copy plus xdelta3
   deltas, SHA-1 checked at every revision): the front end needs them before the seed is activated;
4. KioskShell from `software/src/Tools/KioskShell` (or `-KioskShell <build folder or zip>`), zipped;
5. `rb-reprovision.exe` (`kiosk/reprovision/build.ps1`);
6. the .NET Core and ASP.NET Core 2.2.1 x86 runtimes: Microsoft's files, SHA-256 pinned (the same as Install-Kiosk's),
   cached in `%LOCALAPPDATA%\redbox-installer-cache`;
7. the kiosk profile (`admin kiosk-profile <id> --address <ip>`, or `--base-domain`), and `installer-heading.lua`;
8. `setup.exe` with `rixc`.

Nothing comes out of Redbox's setup exe. Optional parameters: `-Banner` / `-Margin` (the wizard's images; plain red
ones otherwise), `-Wallpaper`, `-LockScreen`, `-IngenicoDriver`, `-ShellMode Auto|ShellLauncher|Winlogon|None`,
`-Version` (the `ProductVersion`, default 3.2.0.0), `-TestMode`, `-KeepStaging` (keeps `<Out>\.staging`, the files
as embedded).

`<Out>` then holds what goes on the USB stick:

| File | |
|---|---|
| `setup.exe` | The installer (about 50 MB, most of it the two runtimes). The same exe works for every kiosk of the same backend. |
| `data.zip` | The seed. |
| `kiosk-profile.json` | This kiosk's profile: hosts or DNS names, the backend's CA, the kiosk number, every URL. |
| `hal.xml` | This kiosk's HAL configuration, with `-HalConfig`. |

## On the kiosk

Run `setup.exe` as an administrator. The wizard is Redbox's, with one page added:

1. Welcome;
2. **Select Data File Location**: finds `data.zip` next to `setup.exe` (Browse copies another one there);
3. **Select Kiosk Profile** (new): finds `kiosk-profile.json` next to `setup.exe`, shows its kiosk number, and passes
   it to the front end (`-n:`);
4. **Select Kiosk Type**: R630 (`MaxSlots` 90) or R504 (72);
5. **Set Date and Time**: opens the clock settings;
6. the installation: files, the two runtimes (and the Ingenico driver if built in), then the front end runs the
   21 steps below;
7. Finish, and a reboot.

`setup.exe --silent` skips the pages (and with them `MaxSlots`); `--log:<file>` writes the host's log. After the
reboot `updatemgrd` polls the backend from the seed's heads, the Update Client connects over MQTT, and KioskShell
starts the engine (Ctrl+Alt+Enter, or `updatemgrd`'s watchdog once HAL answers).

The steps (`scripts/installer-steps.lua`), with the original's numbering where it differs:

| # | Step | |
|---|---|---|
| 1 | Apply Kiosk Profile | New, replaces "Install Digicert Certificates": `rb-reprovision apply kiosk-profile.json --no-restart`, so that the next step reaches the backend |
| 2 | Check Internet Connectivity | `installer start` / `installer log` against the backend; checks for .NET 4.8 instead of .NET 3.5 SP1 |
| 3 | Shutdown Legacy Services | No longer sets `Store\ID` to `unconfigured` |
| 4 | HAL Maintenance | Unchanged |
| 5 | Kiosk Engine Configuration | PROD values, without Redbox's broker and KioskServices URLs |
| 6 | Activate Image | Unchanged |
| 7 | Point Kiosk at Backend | New: the profile again, over the configuration files the image just installed |
| 8 (7) | Install HAL | Installs `hal.xml`, retargets the .NET 2.0 programs to 4.8, checks that `halsvc$default` exists |
| 9 (8) | Setup Card Reader | Without Redbox's card reader list and AuditPol; card reader simulator in test mode |
| 10 (9) | Install lock screen wallpaper | Optional files |
| 11 (10) | Install Redbox Shell | `set-kiosk-shell.ps1` instead of `wmic.exe`; fails when the shell is not set |
| 12-17 (11-16) | Mute, Register Update Manager, Device Service, Kiosk Client, Update Client, Autorun | Each registration is checked; the reader firmware update can be switched off |
| 18-20 (17-19) | Scheduler entries, Quoted Strings, Sticky Keys | Unchanged |
| - (20) | Remove Blink | Removed |
| 21 | Final Image Maintenance | Reboot; in test mode starts the services and the engine instead |

Like the original, the front end resumes after the last finished step recorded in `C:\.post-install`; delete that
file to run every step again. Its log is `REDS\Update Manager\logs\front-end.log`; the helpers log next to it
(`rb-reprovision.log`, `retarget-netfx2.log`, `hal-package-installer.log`, `set-kiosk-shell.log`).

## What changed from the original

| Area | Original (3.1.0.48) | Here |
|---|---|---|
| Setup host runtime | .NET 2.0 exe; refused to start without `%windir%\Microsoft.NET\Framework\v3.5` | .NET Framework 4.8, no .NET 3.5 check (`host/Program.cs`) |
| Page scripts | JScript.NET through `Microsoft.JScript.Vsa.VsaEngine` and .NET 2's `Microsoft.Vsa`. On .NET 4 the host stopped with `[I001] Unable to initialize setup` | The same JScript.NET, compiled with the JScript compiler of .NET 4 (`jsc` through `JScriptCodeProvider`) and run in the host (`host/VsaScriptingHost.cs`). `rix`, `page` and `Host` are untyped globals of the compiled program, so the scripts still reach them late bound. The page scripts are Redbox's, unchanged |
| `.rix` format | Built by Redbox's RIX compiler (not shipped) | Same resources and layout, built by `rixc` |
| `MaxSlots` | Written by the AnyCPU host to the 64-bit registry view; the x86 engine reads the 32-bit one, so it never saw it | 32-bit view (`host/ScriptKernelObject.cs`) |
| Pages | Welcome, data file, kiosk type, date and time, progress, finish ("use the control panel to configure this kiosk with a kiosk number") | The same, plus **Select Kiosk Profile**; the finish page says that the kiosk number comes from the profile |
| Payload | Kaseya 1-9, McAfee agent, nxlog, NAnt and NAntContrib, Redbox NAnt tasks, eEye Blink `core.zip` and `RemoveBlink.bat`, KioskAdmin, DigiCert certificates, two Windows XP patches, AuditPol, .NET 4.7.2, .NET Core / ASP.NET Core 2.2.1, Ingenico driver, KioskShell, the front end, `bitsadmin.exe`, `xdelta3-64.exe` | .NET Core / ASP.NET Core 2.2.1, optional Ingenico driver and wallpapers, KioskShell, `rb-reprovision`, the front end, `lua51.dll`, `xdelta3.exe`, Quartz; all from our builds, the seed and Microsoft |
| .NET 3.5 | Step 2 failed without .NET 3.5 SP1. HAL (`HAL.PackageInstaller.exe`, `halsvc-win32.exe`) is .NET 2.0 and did not run without it; nothing noticed | Step 2 checks for .NET 4.8. `retarget-netfx2.ps1` gives every .NET 2.0 exe under `C:\Program Files\Redbox` a `supportedRuntime v4.0` and sets `OnlyUseLatestCLR = 1` (both registry views) for executables a later update brings |
| Shell | `kernel.replaceshellwith` ran `wmic.exe` (gone from Windows 11) and its failure was ignored; the step "succeeded" with Explorer still the shell | `set-kiosk-shell.ps1`: the same Shell Launcher calls through CIM, or the Winlogon shell where the edition has no Shell Launcher (Pro); the step fails if the shell is not set |
| Kiosk number and endpoints | Step 3 set `Store\ID = unconfigured`; step 5 wrote Redbox's broker (`blt.accessredbox.net:443`) and KioskServices URLs; the front end and `updatemgrd` used `updatesvc.redbox.com` as shipped | The kiosk profile, applied with `rb-reprovision` before the check-in (hosts, CA, number) and again after the image is activated (every component's URLs) |
| HAL configuration | `HAL.PackageInstaller` installed `HAL.xml.default` (0 decks) on a fresh PC; halsvc then died at start-up | `hal.xml` next to `setup.exe` (or a `C:\hal.xml` from an earlier install) goes in first |
| Results not checked | HAL install, `updatemgrd --register`, the three `sc create` | Each one is checked; a failure stops the install at that step (resumable) |
| Card reader | `HKLM\SOFTWARE\Redbox\CRList` values, AuditPol, the reader firmware update (6 minutes without a reader) | No CRList or AuditPol; firmware update kept, off in test mode; test mode turns on the engine's card reader simulator, as Install-Kiosk.ps1 does |
| Removed steps | "Remove Blink" (msiexec with a management password); the QA environment | - |
| Small fixes | Step 2 compared `kernel.finishinstaller`'s boolean with the string "True" (messages swapped), so it logged "Succeeded" whatever happened; a missing answer from `installer start` was indexed as a table; the lock screen step failed without its wallpaper | Fixed |
| Test mode | - | `-TestMode`: `reboot="Never"`, no `DisableTaskMgr`, no reader firmware, card reader simulator, and the last step starts the services and the engine |

Not changed: the store layout and `data.zip` extraction, the `installer start` check-in (the backend answers it),
`installGuid.txt`, Activate Image and the `before/afteractivate.lua` scripts, the service definitions, the PCI
settings, `C:\.post-install`. Like the original, it neither creates the auto-logon user nor sets autologon; use
Install-Kiosk.ps1's `User.*` settings or Sysinternals Autologon for that.

## Test mode and Windows Sandbox

```powershell
.\scripts\rb.ps1 up
$ip = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "vEthernet (Default Switch)").IPAddress
.\provisioning\installer-classic\Build-ClassicInstaller.ps1 -Out E:\temp\classic-74001 -StoreId 74001 `
    -DataZip D:\seeds\data-572024.zip -Address $ip -HalConfig <hal.xml> -TestMode
.\tests\clients\sandbox\sandbox.ps1 prepare ...          # optional: the hardware emulator, for HAL
.\provisioning\installer\tests\sandbox.ps1 up -Classic E:\temp\classic-74001 [-Tools E:\temp\rb-sandbox\tools]
.\provisioning\installer\tests\sandbox.ps1 status -StoreId 74001
.\provisioning\installer\tests\sandbox.ps1 down
```

`tests/classic-logon.ps1` copies the folder to `C:\rbsetup`, starts `setup.exe` and clicks Next on every page and
Finish at the end (UI Automation), saving a screenshot of each page to `control\classic\`. Then it writes
`control\classic\checks.txt`: the services, the shell, the engine's registry values, .NET 3.5 and the processes.
Nothing is prepared for the installer: no hosts entries, no CA, no fake .NET 3.5, no blocked reboot.

## Verification

Run on 2026-10-31 in Windows Sandbox (Windows 11 host), against this backend under WSLC (`rb.ps1 up`), with the
`data-572024.zip` seed, kiosk 74001, the profile from `admin kiosk-profile 74001 --address <Default Switch IP>`, a
`hal.xml` and the hardware emulator from `tests/clients/sandbox`. Built with `-TestMode`; KioskShell built from
`software/src/Tools/KioskShell` by the script.

**The wizard.** `setup.exe` started on .NET 4.8 without .NET 3.5 (`NetFx3` stays `DisabledWithPayloadRemoved` in the
sandbox) and showed Redbox's pages: Welcome, data file (`data.zip` found), the new kiosk profile page ("Kiosk
profile for kiosk 74001 was found"), kiosk type (R630), date and time, the installation, Finish. `setup.exe`
exited with 0 after Finish. About 11 minutes from the date page to Finish, most of it the two runtimes and
Activate Image.

**The steps.** All 21 ran without intervention (`C:\.post-install` = 21):

- step 1 applied the profile; step 2's `installer start` and `installer log` were answered by the backend
  (`installer start -> 203`);
- Activate Image staged and activated all 12 repositories, `before/afteractivate.lua` included;
- step 8 installed the kiosk's `hal.xml`, gave 9 .NET 2.0 executables the v4 runtime (`halsvc-win32`,
  `HAL.PackageInstaller`, `halmgmtc`, `CameraTuner`, `MSHALTester`, `inventoryimport`, `kioskutil`, `redscc`, and
  `rb-reprovision`, which already had it), and `HAL.PackageInstaller` returned 0 and registered `halsvc$default`;
- step 11: the sandbox lists Shell Launcher but cannot enable it, so `Auto` fell back to the Winlogon shell;
- steps 13-16 registered `updatemgrd$default`, `device$service`, `kioskclient$service`, `updateclient$service`;
- step 21 (test mode) started the five services and the engine.

**The kiosk** (`checks.txt`): `halsvc$default`, `updatemgrd$default`, `device$service`, `kioskclient$service` and
`updateclient$service` running; `kioskengine.exe` at its start view; `Winlogon\Shell` =
`"C:\Program Files\Redbox\KioskShell\KioskShell.exe"`; in the engine's (32-bit) registry `Store\ID` = 74001,
`MaxSlots` = 90 (nothing in the 64-bit view), `Environment` = Production, `WatchDogEnabled` = true, and the
profile's broker and KioskServices URLs; `OnlyUseLatestCLR` = 1.

**What the backend saw from the kiosk:**

- the engine (1.88.0.80) registered with the reservation broker: `kiosk 74001 registered`;
- `updatemgrd` checked in: `u p -> 203`, about 7 minutes after it started;
- the Update Client connected over MQTT: `client 74001 connected`, subscribed to `redbox/kiosk/74001/command`, and
  received `SyncTimestampAndTimezone`;
- the Kiosk Client and the engine called the cloud APIs as kiosk 74001 (pricing, kiosk statistics).

**Found on the way** (all fixed in what is committed):

- ILRepack breaks the pre-serialized resources of the NuGet Divelements.WizardFramework (the wizard did not open),
  so that library and System.Resources.Extensions are embedded unchanged and loaded on demand (`host/Program.cs`).
- jsc, unlike the VSA engine, needs `import System;` for fully qualified names; without it `if="..."` expressions
  failed silently and `hal.xml` was not copied.
- The step helpers must be started by the plain System32 path: the front end is x86, but `run_logged` goes through
  the 64-bit `cmd.exe`, which has no `Sysnative`.
- `kernel.finishinstaller` returns a boolean (see the table above).

Not run: a physical kiosk, a reboot into KioskShell (test mode does not reboot; autologon is outside this
installer, as it was outside Redbox's), Shell Launcher on an edition that can enable it, the reader firmware update,
`--silent`, and the Ingenico driver.