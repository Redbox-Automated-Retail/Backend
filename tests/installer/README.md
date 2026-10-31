# Kiosk installers in Windows Sandbox

`sandbox.ps1` runs a kiosk installer on a clean Windows in Windows Sandbox, against this backend running on the
same PC, and shows what the backend saw of the kiosk. The installers themselves are in the Software repository
(the `software/` submodule):

- `software/installer/classic`: Redbox's store installer (the wizard), modernized;
- `software/installer/cli`: `Install-Kiosk.ps1`, the command-line installer.

It reuses `tests/clients/sandbox`: the relay (the backend's ports on the Hyper-V Default Switch address), the
command loop, and, with `-Tools`, com0com and the HW-Emu-Python control-board emulator for HAL
(`tests\clients\sandbox\sandbox.ps1 prepare` builds that folder). Only one Windows Sandbox can run at a time.

## The classic installer

```powershell
.\scripts\rb.ps1 up
$ip = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "vEthernet (Default Switch)").IPAddress
cmd /c ".\scripts\rb.ps1 admin kiosk-profile 74001 --address $ip > E:\temp\kiosk-74001.json"
.\software\installer\classic\Build-ClassicInstaller.ps1 -Out E:\temp\classic-74001 -StoreId 74001 `
    -DataZip D:\seeds\data-572024.zip -ProfileFile E:\temp\kiosk-74001.json -HalConfig <hal.xml> -TestMode
.\tests\installer\sandbox.ps1 up -Classic E:\temp\classic-74001 [-Tools E:\temp\rb-sandbox\tools]
.\tests\installer\sandbox.ps1 status -StoreId 74001
.\tests\installer\sandbox.ps1 down
```

(The profile is written through `cmd` so that it stays UTF-8; a Windows PowerShell 5.1 redirect writes UTF-16.)

`classic-logon.ps1` copies the folder to `C:\rbsetup`, starts `setup.exe` and clicks Next on every page and Finish
at the end (UI Automation), saving a screenshot of each page to `control\classic\`. Then it writes
`control\classic\checks.txt`: the services, the shell, the engine's registry values, .NET 3.5 and the processes.
Nothing is prepared for the installer: no hosts entries, no CA, no fake .NET 3.5, no blocked reboot.

## Install-Kiosk.ps1

```powershell
.\software\installer\cli\New-InstallerPayload.ps1 -Out E:\temp\kiosk-74001 -StoreId 74001 `
    -DataZip D:\seeds\data-572024.zip -ProfileFile E:\temp\kiosk-74001.json -KioskShell <KioskShell build> -TestMode
.\tests\installer\sandbox.ps1 up -Payload E:\temp\kiosk-74001 [-Tools E:\temp\rb-sandbox\tools]
```

`sandbox-logon.ps1` runs `Install-Kiosk.ps1 -TestMode` on the mapped payload and keeps serving the command loop.

## Redbox's original installer

`sandbox.ps1 up -Payload <payload> -Original <setup.exe> -DataZip <zip>` starts a sandbox for Redbox's own
`Windows10-Store-Installer-3.1.0.48-setup.exe` instead (`original-logon.ps1`): the kiosk profile's hosts and CA,
.NET 3.5 faked for its checks, and its reboots blocked. Its RIX host still cannot run there (it needs the .NET 2
script engine; see `software/installer/cli/README.md`, Verification). `rix-unpacked.ps1` then does the host's
part from an unpacked copy (`script.xml` and the dumped files, `software/tools/installer/dump_rix.py`), and runs
the original front end and Lua scripts.
