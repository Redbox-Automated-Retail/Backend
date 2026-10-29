# Kiosk installer (`Install-Kiosk.ps1`)

Turns a fresh Windows 10/11 PC into a Redbox kiosk that runs against this backend. It is an open replacement for
Redbox's `Windows10-Store-Installer-3.1.0.48-setup.exe`. That installer is decompiled and documented in the
Software repository: `software/docs/systems/store-installer.md` (setup host in `software/src/Installer`). This one
is written from that document; it contains no Redbox code or binaries.

It needs three inputs:

- a **data zip**: an update-store seed in Redbox's store format: one of Redbox's `data-<date>.zip`, or one built by
  this backend with `admin build-seed` / `rb.ps1 build-seed` (`docs/seed-zips.md`; `admin check-seed` checks one
  before you install it);
- a **kiosk profile**: the JSON file the backend generates for one kiosk (`admin kiosk-profile`, or the
  dashboard's *Reprovision file*). It holds the backend's address or DNS names, its CA, the store ID and every URL;
- **KioskShell**: a build of `software/src/Tools/KioskShell`. The seeds do not contain the shell; Redbox shipped it
  inside the installer.

## What it does

| Step | Original (`installer-steps.lua` / RIX script) | Here |
|---|---|---|
| `10-prerequisites` | RIX ran NDP 4.7.2, .NET Core 2.2.1 x86, ASP.NET Core 2.2.1 x86 and the Ingenico USB driver; step 2 required .NET 3.5 SP1 | .NET Framework 4.7.2+ (installed only if missing), .NET 3.5 via DISM, the two 2.2.1 x86 runtimes (Microsoft's files, SHA-256 pinned; identical to the ones Redbox shipped), optional Ingenico driver |
| `20-update-store` | RIX unzipped `data.zip` into `REDS\Update Manager\.store\.repository`; "Activate Image" ran `repositoryupdate` + `repositoryactivate` per repository, with `before/afteractivate.lua` | Unzips the seed there. Rebuilds `update-console.exe` from the seed's `kop-update-manager` into the state folder (SHA-1 checked), then runs `update-console --eval lua\activate-image.lua`. That is the kiosk's own RepositoryService, xdelta3 and Lua kernel doing the same per-repository loop. Then it checks that every repository's `.active` is its head |
| `30-engine` | Kiosk-type page (`MaxSlots`), "Kiosk Engine Configuration", `WatchDogEnabled`, the stats scheduler entries | Same values in `HKLM\SOFTWARE\WOW6432Node\Redbox\REDS\Kiosk Engine` (store ID, `MaxSlots`, `Environment`, `RunInitialSync`, `LiveCall`, `HardwareServiceURL`, `SchedulerEntries`) |
| `40-backend` | Nothing: kiosks used Redbox's host names; step 2 checked in with `installer start` | `rb-reprovision apply <profile> --no-restart` (`kiosk/reprovision`): hosts entries or DNS names, the CA in LocalMachine\Root, store ID and market, engine endpoints, updatemgrd's Update Service URL and credential keys, the Update Client / Kiosk Client / Device Service settings. `rb-reprovision revert` undoes it |
| `50-services` | `HAL.PackageInstaller.exe`, `updatemgrd --register`, `sc create device$service / kioskclient$service / updateclient$service` (delayed auto) | The same. Optionally installs a kiosk's `hal.xml` first. In test mode lets HAL run on the .NET 4 runtime |
| `60-shell` | KioskShell through Shell Launcher for every user (Administrators too); wallpaper, lock screen. The auto-logon user came with Redbox's image; `DemoteUser.lua` (later, remotely) made it a non-admin | Creates the kiosk user (non-admin by default), autologon with the password in the LSA secret (as Sysinternals Autologon does), KioskShell via Shell Launcher (Enterprise/Education/IoT) or the machine-wide Winlogon shell (Pro) |
| `70-hardening` | Autorun off, `AeDebug`, crash auto-reboot, `ServicesPipeTimeout`, IE8 browser emulation, accessibility hot keys off, `DisableTaskMgr`, mute, quoted service paths, AuditPol | Each one switchable in `Hardening`. The Redbox card-reader list, Blink removal, Kaseya, McAfee and nxlog are left out |
| `80-hardware` | `RBAInstaller.exe` (reader firmware); Dell BIOS through `KioskAdmin -runbios` (pushed later as `dellbios.lua`) | Optional, off by default: `CardReaderFirmware`, and `DellBios.Command` / `Arguments` for your own Dell Command \| Configure call |
| `90-finish` | Reboot | Reboot. With `StartAfterInstall` (test mode) it starts the services and the engine at once instead |

The steps write to `StateDir` (default `C:\ProgramData\Redbox\installer`): `install.log`, and `steps.json`, which
records finished steps. A second run skips those, like the original's `C:\.post-install`. `-Force` re-runs them;
`-Only 40,50` runs only those steps.

Left out on purpose: the Update Service `installer start` / `installer log` check-in (the backend answers it, but
nothing needs it any more), expiry of seeds and installers, `installGuid.txt`, the XP/7 migration steps, and
NAnt (nothing on Windows 10 used it).

## Making a payload

`New-InstallerPayload.ps1` puts everything for one kiosk in one folder. It builds `rb-reprovision.exe`, copies
KioskShell, downloads the runtimes, copies the seed, asks the backend for the kiosk profile and writes
`settings.json`:

```powershell
# KioskShell: msbuild software\src\Tools\KioskShell -restore -p:Configuration=Release
.\provisioning\installer\New-InstallerPayload.ps1 -Out E:\kiosk-12345 -StoreId 12345 `
    -DataZip D:\seeds\data-572024.zip -Address 10.0.0.5 `
    -KioskShell software\src\Tools\KioskShell\bin\Release\net472
```

`-BaseDomain example.net` makes the profile use DNS names instead of hosts entries. `-ProfileFile` uses a profile
you already have. `-Runtime compose` asks a Docker Compose deployment instead of WSLC.

On the kiosk, as an administrator:

```powershell
powershell -ExecutionPolicy Bypass -File E:\kiosk-12345\installer\Install-Kiosk.ps1 -Settings E:\kiosk-12345\settings.json
```

It reboots at the end. After that `updatemgrd` polls the backend from the seed's heads, the Update Client
connects over MQTT, and KioskShell starts the engine (Ctrl+Alt+Enter, or `updatemgrd`'s watchdog once HAL
answers).

## Settings

`settings.example.json` lists every key with its default. Relative paths are relative to the settings file.

| Key | Meaning |
|---|---|
| `StoreId` | The kiosk ID (required). It must match the profile's `KioskId`. |
| `DataZip` | The seed (required). |
| `KioskType` | `R630` (90 slots per deck, `MaxSlots = 90`) or `R504` (72). |
| `Environment` | `Kiosk Engine\Store\Environment`, `Production` like Redbox's PROD build. |
| `Backend.ProfileFile` / `Address` / `Reprovision` | The kiosk profile, an address overriding its hosts address, and `rb-reprovision.exe`. Without a profile step 40 is skipped and the kiosk keeps the URLs its packages shipped with. |
| `Prerequisites.*` | Local paths for the installers (else downloaded and SHA-256 checked); `NetFx3`: `Enable` (fail if it cannot), `IfAvailable`, `Skip`; `NetFx3Source`: the media's `sources\sxs` for offline PCs; `IngenicoDriver`: the driver installer, if you have it. |
| `User.*` | The auto-logon user (`Name`, `Create`, `PasswordFile`, `Administrator`, `AutoLogon`). Without a password file a random one is generated and kept only in the LSA secret. `Name: null` uses the account running the installer and does not touch autologon. |
| `Shell.*` | `KioskShell` (folder or zip), `Mode` (`Auto`, `ShellLauncher`, `Winlogon`, `None`), `Wallpaper` (to `C:\Redbox\background.bmp`), `LockScreen`. |
| `Hardening.*` | One switch per original setting. `AuditPolicy` is off by default. |
| `Hardware.*` | `Hal` (run HAL's package installer), `HalConfig` (a kiosk's `hal.xml`), `HalAllowV4Runtime`, `CardReaderFirmware`, `DellBios`. |
| `TestMode` | See below; also `-TestMode` on the command line. |
| `Reboot`, `StartAfterInstall`, `StateDir` | As named. |

## Test mode (VMs, Windows Sandbox)

`-TestMode` (or `"TestMode": true`) does not reboot. It leaves the shell and autologon alone (`Shell.Mode None`
unless set explicitly), only tries .NET 3.5 (`IfAvailable`) and allows HAL on .NET 4, which Windows Sandbox
needs because .NET 3.5 cannot be enabled there. It skips the hardware steps, and at the end starts every
service and the engine in the current session.

`tests/sandbox.ps1` runs the whole thing in Windows Sandbox against the backend on this PC:

```powershell
.\scripts\rb.ps1 up
$ip = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "vEthernet (Default Switch)").IPAddress
.\provisioning\installer\New-InstallerPayload.ps1 -Out E:\temp\kiosk-74001 -StoreId 74001 -DataZip D:\seeds\data-572024.zip `
    -Address $ip -KioskShell <KioskShell build> -TestMode
.\tests\clients\sandbox\sandbox.ps1 prepare ...          # optional: the hardware emulator, for HAL
.\provisioning\installer\tests\sandbox.ps1 up -Payload E:\temp\kiosk-74001 [-Tools E:\temp\rb-sandbox\tools]
.\provisioning\installer\tests\sandbox.ps1 status -StoreId 74001
.\provisioning\installer\tests\sandbox.ps1 down
```

It reuses `tests/clients/sandbox`: the relay (backend ports on the Default Switch address), the command loop, and,
with `-Tools`, com0com and the HW-Emu-Python control-board emulator.

`tests/sandbox.ps1 up -Original <setup.exe> -DataZip <zip>` starts a sandbox for Redbox's own installer instead
(`tests/original-logon.ps1`): the kiosk profile's hosts and CA, .NET 3.5 faked for its checks, and its reboots
blocked. Its RIX host still cannot run there (see Verification). `tests/rix-unpacked.ps1` then does the host's
part from an unpacked copy (`script.xml` and the dumped files, `software/tools/installer/dump_rix.py`), and runs
the original front end and Lua scripts.

## Verification

Run on 2026-10-28 in Windows Sandbox (Windows 11 Pro host), against this backend under WSLC, with the
`data-572024.zip` seed, store ID 74001 and a KioskShell built from `software/src/Tools/KioskShell`. The kiosk
profile came from `admin kiosk-profile 74001 --address <Default Switch IP>`, with the hardware emulator from
`tests/clients/sandbox`.

**Install-Kiosk.ps1 -TestMode, every step.**

- Prerequisites took 8 minutes, mostly the two 2.2.1 runtimes. NetFx3 could not be enabled (`0x80072ee6`), as
  expected in Sandbox.
- **20-update-store:** 12 repositories unpacked. 17 `kop-update-manager` files were rebuilt and SHA-1 checked.
  `update-console --eval activate-image.lua` staged and activated all 12 in about 40 s, running `kop-v2-engine`'s
  before- and afteractivate scripts, and every `.active` equals its revlog head.
- `rb-reprovision apply` made 4 changes.
- HAL.PackageInstaller registered `halsvc$default`, and `updatemgrd --register` and the three `sc create`
  calls succeeded.
- The kiosk user was created and the sound muted.
- In 90-finish all five services were running and the engine was started.

**What the backend saw from the kiosk:**

- updatemgrd polled `srcp://updatesvc.redbox.com:7002` as store 74001 over TLS, validated with the profile's CA:
  `u p -> 203`.
- The Update Client got its IoT certificate (`iotcert/issued`).
- The Kiosk Client called the Proxy Service as kiosk 74001 (pricing, planograms, nearby).
- The engine (1.88.0.80 from the seed) reached `start_view` and registered with the reservation broker:
  `kiosk 74001 registered ... "Version":"1.88.0.80"`.

**Found on the way:**

- **HAL needs the kiosk's `hal.xml`.** Without `Hardware.HalConfig`, HAL.PackageInstaller installs
  `HAL.xml.default` (0 decks), and halsvc stops at start-up in `DecksManager.get_First`. The run above used the
  image's `hal.xml` (a scratch copy) and the emulator, and HAL then initialised. The installer now warns when
  `HalConfig` is missing.
- **Without a card reader** the rental application goes to maintenance mode ("Credit Card Reader Communications
  Error"). `Hardware.SimulateCardReader`, on in test mode, sets the engine's simulator values, as
  `tests/clients/engine-registry.ps1` does.
- **Not working: the Update Client's MQTT connection.** It fails every minute with "The remote certificate is
  invalid according to the validation procedure", while HTTPS to the same CA validates. Probable cause: MQTTnet
  is set up without `IgnoreCertificateRevocationErrors`, and the backend's certificates have no CRL or OCSP
  endpoint. This is a backend issue, not the installer's. The backend now serves a CRL (commit "certs: CRL
  distribution point and a served CRL"), which came after this run; the installer has not been re-run against it.

**The original installer in the same setup** (`-Original`, same seed, same backend):

- **Its RIX host does not run.** Even with .NET 3.5 faked and a `supportedRuntime v4.0` config it stops with
  `[I001] Unable to initialize setup; the script was not found or unparsable`. Its JScript/VSA page engine is
  built against the .NET 2 `Microsoft.Vsa` types and does not load on .NET 4.
- **With `rix-unpacked.ps1` doing the host's part,** the original front end and `installer-steps.lua` ran all
  21 steps to "Installation complete":
  - `installer start` was answered by this backend;
  - Activate Image activated all 12 repositories;
  - `RBAInstaller.exe` ran for 6 minutes (no reader) and returned 0;
  - the three services were created and `updatemgrd` was registered.
- **The result is not a working kiosk on this Windows:**
  - **No HAL service:** HAL.PackageInstaller is a .NET 2.0 exe, and without .NET 3.5 it does not run.
  - **No shell replacement:** `ReplaceShellWith` starts `wmic.exe`, which Windows 11 no longer has. The exception
    is swallowed and the script ignores the result, so the shell stays `explorer.exe` while the step "succeeds".
  - **Not pointed at this backend:** step 5 resets `Store\ID` to `unconfigured` and the broker URL to Redbox's
    `:443`. A kiosk profile has to be applied after the original installer, not before.
  - **`MaxSlots`:** `rix-unpacked.ps1` writes it to the 64-bit registry view, where the AnyCPU host would; the
    x86 engine reads the 32-bit view. That the real host does this is inferred from its binary, not observed.

Not run: a reboot into the shell with autologon (test mode leaves both alone), Shell Launcher on an Enterprise
edition, the hardware steps, and a physical kiosk.
