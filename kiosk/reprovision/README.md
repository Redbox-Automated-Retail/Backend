# rb-reprovision: point a kiosk at a backend in one step

`rb-reprovision.exe` runs on the kiosk (elevated) and applies one settings file. That file is generated per
kiosk by the backend. One run can:

- add hosts entries;
- install or replace the backend CA;
- set the store ID and market;
- set the engine's registry values;
- set the Update Manager, Update Client, Kiosk Client and Device Service configuration;
- rewrite old URLs in the kiosk's config files.

Before it changes anything it records a manifest and backups, so `revert` undoes exactly what `apply` did.

```bat
rb-reprovision show   kiosk-12345.json [--address 10.0.0.5]   :: what would change; writes nothing
rb-reprovision apply  kiosk-12345.json [--address 10.0.0.5]   :: apply, back up, restart the affected services
rb-reprovision revert                                         :: undo, from the manifest
rb-reprovision status                                         :: print the manifest
```

Options:

- `--address IP` replaces the settings file's hosts address.
- `--reds DIR` sets the `REDS` folder, if it isn't found under `Program Files\Redbox\REDS`.
- `--no-restart` skips restarting services.
- `--state DIR` sets where the manifest and backups go (default `%ProgramData%\Redbox\reprovision`).

A second `apply` keeps the first backups, so `revert` always returns to the state from before the first
`apply`. After `revert` the state folder is renamed `reprovision.reverted-<time>`. It is not deleted.

## Building it

`.\build.ps1` builds `out\rb-reprovision.exe` and its `.config` using the C# compiler in Windows' .NET
Framework folder. A kiosk can build it the same way. It is a single x86 exe:

- **x86:** it sees the same 32-bit registry view (`HKLM\SOFTWARE\WOW6432Node\Redbox` on 64-bit Windows) as the
  kiosk's x86 programs: the engine, updatemgrd, the Update Client, the Kiosk Client and the Device Service.
- **Runtimes:** it is built with the .NET 3.5 compiler when available, so it runs on the 2.0/3.5 runtime of
  older kiosks. Its `.config` also lets it run on the 4.x runtime of current kiosks, where 3.5 may be missing.
- **No dependencies:** JSON is parsed by `Json.cs`.

Copy `rb-reprovision.exe`, `rb-reprovision.exe.config` and the settings file to the kiosk.

## Getting the settings file

- **Dashboard:** go to **Kiosks**, then **Reprovision a kiosk**, or **Reprovision file** on a kiosk's page.
- **CLI:**

  ```sh
  docker compose exec rb-iot python -m redbox_backend admin kiosk-profile 12345 --address 10.0.0.5 > kiosk-12345.json
  .\scripts\rb.ps1 admin kiosk-profile 12345 --address 10.0.0.5 > kiosk-12345.json      # WSLC
  ```

`--address IP` keeps the original Redbox host names in every URL and maps them to the backend in the hosts file.
`--base-domain D` uses `<service>.D` names that your DNS resolves, and writes no hosts entries; those names must
be in the server certificate (`EXTRA_HOSTNAMES`), and the generator warns when they aren't.

`--rewrite OLD=NEW` (repeatable) replaces text in the kiosk's config files. Use it for images that were repointed
elsewhere before.

## Settings file (format 1)

Every section is optional. A missing section or a `null` value means "leave it as it is".

```json
{
  "Format": 1,
  "Comment": "kiosk 12345 -> 10.0.0.5",
  "KioskId": 12345,
  "Market": "charlotte",
  "Hosts": { "Address": "10.0.0.5", "Names": ["updatesvc.redbox.com", "proxyservice.kiosk.prod.redbox.com", "..."] },
  "CaCertificate": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n",
  "Engine": {
    "Remote Services\\Reservation Services": { "BrokerServicesURL": "gtcp://blt.accessredbox.net:7890/RegistrationBroker/Default.rem" },
    "Remote Services\\Store Services": { "KioskServicesURL": "https://bl2.accessredbox.net/KioskServices/MessageService.rem" }
  },
  "UpdateManager": { "UpdateServiceUrl": "srcp://updatesvc.redbox.com:7002", "CredentialKeys": { "Key1": "...", "Key2": "..." } },
  "UpdateClient": { "IoTCertificateServiceUrl": "https://...", "DefaultProxyServiceUrl": "https://...", "IoTBrokerEndpoint": "..." },
  "KioskClient": { "DefaultProxyServiceUrl": "https://..." },
  "DeviceService": { "KioskDataApiUrl": "https://dataservice.kiosk.prod.redbox.com" },
  "UrlRewrites": { "https://old-host": "https://dataservice.kiosk.prod.redbox.com" },
  "RedsPath": null
}
```

What each section changes on the kiosk. Paths are relative to `REDS`, and registry paths are in the 32-bit view.
A listed Windows service is restarted only when one of its files changed.

| Section | Changes | Restarts |
|---|---|---|
| `Hosts` | `%SystemRoot%\System32\drivers\etc\hosts`: replaces the lines ending `# redbox-self-hosted` with `<Address> <name>` for each name | none |
| `CaCertificate` | adds the certificate to LocalMachine\Root; removes certificates with the same subject (they are backed up) | none |
| `KioskId`, `Market` | `HKLM\SOFTWARE\Redbox\REDS\Kiosk Engine\Store` `ID`, `Market` (read by the engine, updatemgrd, the Update Client); `DeviceService\bin\Data\KioskData.json` `KioskId` | `device$service` |
| `Engine` | `HKLM\SOFTWARE\Redbox\REDS\Kiosk Engine\<subkey>` `<name>` = string, or a list for `REG_MULTI_SZ` | engine (see below) |
| `UpdateManager.UpdateServiceUrl` | `Update Manager\bin\updatemgrd.exe.config`, `update-console.exe.config`, `updatemgr-ui.exe.config`: `<setting name="UpdateServiceUrl">` | `updatemgrd$default` |
| `UpdateManager.CredentialKeys` | `Update Manager\bin\usdata.staged`: `{"Key1","Key2"}` encrypted with the kiosk's own Redbox.Core `Encrypt()` (`Redbox.Core.dll`, or merged into `updatemgrd.exe` as on 1.88 kiosks), which updatemgrd moves to `usdata.active` (key of the `h:` credential hash). Only needed when the backend sets `UPDATE_SERVICE_KEY1/2` | `updatemgrd$default` |
| `UpdateClient` | each key in `Update Client\bin\appsettings.Production.json` when that overlay sets it (it wins over the base file), else in `appsettings.json` (commented lines are skipped; missing keys are not added) | `updateclient$service` |
| `KioskClient` | the same for `Kiosk Client\bin\appsettings.json` / `appsettings.Production.json` | `kioskclient$service` |
| `DeviceService.KioskDataApiUrl` | `DeviceService\bin\Data\KioskData.json` `ApiUrl` (the engine also rewrites this file from `system.config`'s `KioskDataServicesUrl`) | `device$service` |
| `UrlRewrites` | plain text replacement in `Update Manager\bin\*.exe.config`, `Update Client\bin\appsettings*.json`, `Kiosk Client\bin\appsettings*.json`, `DeviceService\bin\appsettings*.json` and `Data\*.json`, `Kiosk Engine\data\*.config`, `Kiosk Engine\bin\kioskengine.exe.config`, `%ProgramData%\Redbox\KioskClient\Configuration\*` | the owning service |

Some changes take effect only after the Kiosk Engine is restarted (or the kiosk rebooted): the engine's registry
values, its `data\*.config`, the hosts file and the CA. The engine is a desktop process, not a service, so the
tool says so instead of killing it.

Edits are text-level, so each file keeps its encoding, BOM, line endings and comments. A component that is not
installed shows up as `skip` in `show`.

The engine's `data\*.config` files are copies of config files that updatemgrd gets from the Update Service.
To make a change stick, also change them on the backend (`admin import-store --rewrite`, `admin config-file-set`).
Otherwise the next config-file generation brings the old values back.

## Older kiosks

Kiosks from 2014-2016 (engine 1.7, .NET 2.0 runtime) use the same registry layout (`Store\ID`,
`Remote Services\Reservation Services\BrokerServicesURL`, `Remote Services\Store Services\KioskServicesURL` and
`HardwareServiceURL`). They have no Update Client, Kiosk Client or Device Service, and the tool skips what isn't
installed. They also use `tickets-dev.redbox.com` (Ticket Services, plain HTTP), which is in the generated
hosts list.

## Manifest

`%ProgramData%\Redbox\reprovision\manifest.json` lists, in order:

- every file the tool touched, with its backup under `backup\`, or `null` if the tool created it;
- every registry value, with its previous value and type, or `none` if it didn't exist;
- every key it created;
- every certificate it added or removed (removed ones are backed up as `.cer`).

`revert` walks the list backwards. A created registry key is deleted only if it is empty, so anything the
kiosk wrote there since is kept.
