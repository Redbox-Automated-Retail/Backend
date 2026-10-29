<#
.SYNOPSIS
  Build, run, stop and reset the Redbox self-hosted backend with WSLC (wslc.exe).

.DESCRIPTION
  The development path: WSLC has no compose, so this script creates the network, volumes and containers that
  compose.yaml describes (same names, Dockerfiles and settings file). Production deployments use compose.yaml.

    rb.ps1 build                 build the redbox-backend image
    rb.ps1 up                    create network/volumes, generate certificates, start every container
    rb.ps1 down                  stop and remove the containers (data volumes are kept)
    rb.ps1 reset [-Force]        down + delete the volumes (database, content, certificates)
    rb.ps1 status                containers and health
    rb.ps1 logs <service>        service = db | updatesvc | cloudapi | iot | admin | remoting
    rb.ps1 set-admin-password [user]  set the dashboard login (hash stored in local\backend.env)
    rb.ps1 admin <args...>       python -m redbox_backend admin <args> inside rb-iot
    rb.ps1 admin-in <dir> <args> same, in a one-off container with <dir> mounted at /in
    rb.ps1 import-store <path>   import a kiosk's "REDS\Update Manager\.store", a data-<date>.zip seed or a folder of seeds
    rb.ps1 import-packages [dir] import every data-*.zip seed in PACKAGES_DIR (or dir), read-only
    rb.ps1 build-seed <dir> <args> admin build-seed with <dir> mounted read-write at /work (docs/seed-zips.md)
    rb.ps1 export-ca             copy ca.crt / ca.cer to certs\ for installing on kiosks
    rb.ps1 test                  run the protocol tests inside a container against the running stack

  Settings come from .env (copy .env.example), the same file compose.yaml uses. Host ports: 443 (cloud APIs, IoT
  certificate service, S3; plain HTTP on 80 for the 2014 Ticket Services), 7002 (srcp Update Service), 7080 (change-set downloads), 8883 (MQTT/TLS);
  8081 (IoT operator API) and 8443 (admin dashboard) are published on 127.0.0.1 only by default.
#>
param(
    [Parameter(Position = 0)] [string] $Command = "help",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)] [string[]] $Rest,
    [switch] $Force
)
$ErrorActionPreference = "Stop"
$Wslc = "C:\Program Files\WSL\wslc.exe"
$Backend = Split-Path -Parent $PSScriptRoot
$Software = Join-Path $Backend "software"   # the Software repository (git submodule)
$Image = "redbox-backend:latest"
$RemotingImage = "redbox-remoting:latest"
$Network = "redbox"
$Volumes = @{ pg = "rb-pgdata"; data = "rb-data"; certs = "rb-certs" }
# The same settings file as compose.yaml; local\backend.env is the older location (still read when .env is missing).
$EnvFile = Join-Path $Backend ".env"
if (-not (Test-Path $EnvFile) -and (Test-Path (Join-Path $Backend "local\backend.env"))) { $EnvFile = Join-Path $Backend "local\backend.env" }
$UcsEncryptionSource = Join-Path $Software "src\UpdateClient\UpdateClientService.API\Services\IoT\Certificate\Security\EncryptionService.cs"

function W { & $Wslc @args; if ($LASTEXITCODE -ne 0) { throw "wslc $($args -join ' ') failed ($LASTEXITCODE)" } }
# Run wslc ignoring failures (PowerShell 5.1 turns native stderr into errors under "Stop").
function WQuiet {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Wslc @args *> $null; $LASTEXITCODE -eq 0 } finally { $ErrorActionPreference = $old }
}

function Read-Env {
    $vars = [ordered]@{}
    $file = if (Test-Path $EnvFile) { $EnvFile } else { Join-Path $Backend ".env.example" }
    foreach ($line in Get-Content $file) {
        if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') {
            $v = $Matches[2].Trim()
            # Quotes as in Compose's .env format (single quotes keep the "$" of a password hash literal).
            if ($v.Length -ge 2 -and ($v[0] -eq "'" -or $v[0] -eq '"') -and $v[-1] -eq $v[0]) { $v = $v.Substring(1, $v.Length - 2) }
            $vars[$Matches[1]] = $v
        }
    }
    $vars["DATABASE_URL"] = "postgresql://redbox:redbox@rb-db:5432/redbox"
    $vars
}

function Env-Args($extra = @{}) {
    $list = @()
    $vars = Read-Env
    foreach ($k in $extra.Keys) { $vars[$k] = $extra[$k] }
    foreach ($k in $vars.Keys) { if ($vars[$k] -ne "") { $list += @("-e", "$k=$($vars[$k])") } }
    $list
}

function Container-Exists($name) { WQuiet inspect $name }

function Start-Service($name, $alias, $ports, $cmd, $extraVolumes = @(), $extraEnv = @{}) {
    if (Container-Exists $name) { [void](WQuiet remove -f $name) }
    $wargs = @("run", "-d", "--name", $name, "--network", $Network, "--network-alias", $alias,
               "-v", "$($Volumes.data):/data", "-v", "$($Volumes.certs):/certs") + $extraVolumes
    foreach ($p in $ports) { $wargs += @("-p", $p) }
    $wargs += Env-Args $extraEnv
    $wargs += @($Image) + $cmd
    W @wargs | Out-Null
    Write-Host "started $name"
}

switch ($Command) {
    "build" {
        W build -t $Image $Backend
        # Mono host for the legacy .NET Remoting backends; it compiles the kiosk's contract assemblies from
        # software/src (only the files in remoting\Dockerfile.dockerignore are sent).
        W build -t $RemotingImage -f (Join-Path $Backend "remoting\Dockerfile") $Backend
    }
    "up" {
        if (-not (WQuiet network inspect $Network)) { W network create $Network | Out-Null }
        foreach ($v in $Volumes.Values) { if (-not (WQuiet volume inspect $v)) { W volume create $v | Out-Null } }
        if (-not (Container-Exists "rb-db")) {
            W run -d --name rb-db --network $Network --network-alias rb-db -v "$($Volumes.pg):/var/lib/postgresql/data" `
                -e POSTGRES_USER=redbox -e POSTGRES_PASSWORD=redbox -e POSTGRES_DB=redbox postgres:16-alpine | Out-Null
            Write-Host "started rb-db"
        } else { [void](WQuiet start rb-db) }
        # Certificates (idempotent; regenerated when the host name list changes).
        $envs = Env-Args
        W run --rm -v "$($Volumes.certs):/certs" @envs $Image python -m redbox_backend certs | Out-Null
        $ucsMount = @()
        $ucsEnv = @{}
        if (Test-Path $UcsEncryptionSource) {
            # The IoT certificate service reads the Update Client's hard-coded passphrase from the decompiled
            # source at run time instead of keeping a copy of it.
            $ucsMount = @("-v", "$(Split-Path $UcsEncryptionSource):/ucs:ro")
            $ucsEnv = @{ UCS_ENCRYPTION_SOURCE = "/ucs/EncryptionService.cs" }
        }
        $vars = Read-Env
        # WSLC publishes on 127.0.0.1 unless an address is given; kiosks on the LAN need 0.0.0.0.
        $bind = if ($vars["BIND_ADDRESS"]) { $vars["BIND_ADDRESS"] + ":" } else { "0.0.0.0:" }
        $plain = if ($vars["UPDATE_SERVICE_PLAIN_PORT"] -and $vars["UPDATE_SERVICE_PLAIN_PORT"] -ne "0") { @("$bind$($vars['UPDATE_SERVICE_PLAIN_PORT']):$($vars['UPDATE_SERVICE_PLAIN_PORT'])") } else { @() }
        $mplain = if ($vars["MQTT_PLAIN_PORT"] -and $vars["MQTT_PLAIN_PORT"] -ne "0") { @("$bind$($vars['MQTT_PLAIN_PORT']):$($vars['MQTT_PLAIN_PORT'])") } else { @() }
        Start-Service "rb-cloudapi" "rb-cloudapi" @("${bind}443:443", "${bind}80:80") @("python", "-m", "redbox_backend", "cloudapi") $ucsMount $ucsEnv
        Start-Service "rb-updatesvc" "rb-updatesvc" (@("${bind}7002:7002", "${bind}7080:7080") + $plain) @("python", "-m", "redbox_backend", "updatesvc")
        # Admin dashboard: localhost only unless ADMIN_BIND_ADDRESS says otherwise.
        $adminBind = if ($vars["ADMIN_BIND_ADDRESS"]) { $vars["ADMIN_BIND_ADDRESS"] } else { "127.0.0.1" }
        $adminPort = if ($vars["ADMIN_PORT"]) { $vars["ADMIN_PORT"] } else { "8443" }
        $adminCmd = @("python", "-m", "redbox_backend", "dashboard", "--port", $adminPort)
        if ($vars["ADMIN_TLS"] -eq "false") { $adminCmd += "--no-tls" }
        if (-not $vars["ADMIN_PASSWORD_HASH"]) { Write-Warning "No admin password set: run 'rb.ps1 set-admin-password' (nobody can log in to the dashboard until then)." }
        # The dashboard can import update-store seeds from PACKAGES_DIR (mounted read-only).
        $pkgMount = @(); $pkgEnv = @{ IOT_ADMIN_URL = "http://rb-iot:8081" }
        if ($vars["PACKAGES_DIR"] -and (Test-Path $vars["PACKAGES_DIR"])) {
            $pkgMount = @("-v", "$((Resolve-Path $vars['PACKAGES_DIR']).Path):/packages:ro"); $pkgEnv["RB_PACKAGES_DIR"] = "/packages"
        }
        Start-Service "rb-admin" "rb-admin" @("${adminBind}:${adminPort}:${adminPort}") $adminCmd $pkgMount $pkgEnv
        # Legacy .NET Remoting (Mono): reservation broker on gtcp 7890 (published); the MessageService (8090) and
        # the control API (8091) stay on the WSLC network - rb-cloudapi fronts the MessageService on 443.
        if (Container-Exists "rb-remoting") { [void](WQuiet remove -f "rb-remoting") }
        $renv = Env-Args @{ BACKEND_URL = "http://rb-cloudapi:80" }
        W run -d --name rb-remoting --network $Network --network-alias rb-remoting -v "$($Volumes.certs):/certs:ro" `
            -p "${bind}7890:7890" @renv $RemotingImage | Out-Null
        Write-Host "started rb-remoting"
        Start-Service "rb-iot" "rb-iot" (@("${bind}8883:8883", "127.0.0.1:8081:8081") + $mplain) @("python", "-m", "redbox_backend", "iot")
        & $PSCommandPath status
    }
    "down" {
        foreach ($c in "rb-admin", "rb-iot", "rb-remoting", "rb-updatesvc", "rb-cloudapi", "rb-db") {
            if (Container-Exists $c) { [void](WQuiet remove -f $c); Write-Host "removed $c" }
        }
    }
    "reset" {
        if (-not $Force) {
            $answer = Read-Host "Delete the database, imported content and certificates? (yes/no)"
            if ($answer -ne "yes") { return }
        }
        & $PSCommandPath down
        foreach ($v in $Volumes.Values) { if (WQuiet volume inspect $v) { W volume remove $v | Out-Null; Write-Host "deleted volume $v" } }
    }
    "set-admin-password" {
        # Stores only a PBKDF2-SHA256 hash in the git-ignored .env; restart with "up" afterwards.
        $user = if ($Rest) { $Rest[0] } else { "admin" }
        $p1 = Read-Host "Dashboard password for '$user'" -AsSecureString
        $p2 = Read-Host "Repeat it" -AsSecureString
        $plain1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p1))
        $plain2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p2))
        if ($plain1 -ne $plain2) { throw "The passwords differ." }
        if ($plain1.Length -lt 10) { throw "Use at least 10 characters." }
        $salt = New-Object byte[] 16
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($salt)
        $iterations = 310000
        $kdf = New-Object Security.Cryptography.Rfc2898DeriveBytes($plain1, $salt, $iterations, [Security.Cryptography.HashAlgorithmName]::SHA256)
        $hash = "pbkdf2_sha256`$$iterations`$$([Convert]::ToBase64String($salt))`$$([Convert]::ToBase64String($kdf.GetBytes(32)))"
        New-Item -ItemType Directory -Force (Split-Path $EnvFile) | Out-Null
        if (-not (Test-Path $EnvFile)) { Copy-Item (Join-Path $Backend ".env.example") $EnvFile }
        $lines = @(Get-Content $EnvFile | Where-Object { $_ -notmatch '^(ADMIN_USERNAME|ADMIN_PASSWORD_HASH)=' })
        $lines += "ADMIN_USERNAME=$user", "ADMIN_PASSWORD_HASH='$hash'"
        Set-Content -Path $EnvFile -Value $lines -Encoding ASCII
        Write-Host "Saved the password hash for '$user' in $EnvFile. Run 'rb.ps1 up' to apply it."
    }
    "status" {
        & $Wslc list --all
        foreach ($probe in @(@("rb-cloudapi", "https://localhost:443/_health"), @("rb-updatesvc", "http://localhost:7080/_health"), @("rb-admin", "https://localhost:8443/api/status"))) {
            try {
                $r = & curl.exe -sk --max-time 3 $probe[1]
                Write-Host ("{0,-14} {1}" -f $probe[0], $r)
            } catch { Write-Host ("{0,-14} not answering" -f $probe[0]) }
        }
    }
    "logs" {
        $svc = if ($Rest) { $Rest[0] } else { "iot" }
        & $Wslc logs "rb-$svc"
    }
    "admin" {
        & $Wslc exec rb-iot python -m redbox_backend admin @Rest
    }
    "admin-in" {
        # Like "admin", in a one-off container with a host folder mounted read-only at /in (for publish,
        # fileset-create, config-file-set, download-file).
        if (-not $Rest -or $Rest.Count -lt 2) { throw "usage: rb.ps1 admin-in <host folder> <admin args...> (refer to the folder as /in)" }
        $dir = (Resolve-Path $Rest[0]).Path
        $envs = Env-Args @{ IOT_ADMIN_URL = "http://rb-iot:8081" }
        W run --rm --network $Network -v "$($Volumes.data):/data" -v "$($Volumes.certs):/certs" -v "${dir}:/in:ro" `
            @envs $Image python -m redbox_backend admin @($Rest[1..($Rest.Count - 1)])
    }
    "build-seed" {
        # Installer seed (docs/seed-zips.md), with a host folder mounted read-write at /work for the output zip and
        # any --add content: rb.ps1 build-seed D:\seeds /work --all   or   ... /work/data-01022030.zip --add r /work/new 'C:\...'
        if (-not $Rest -or $Rest.Count -lt 2) { throw "usage: rb.ps1 build-seed <host folder> <admin build-seed args> (refer to the folder as /work)" }
        New-Item -ItemType Directory -Force $Rest[0] | Out-Null
        $dir = (Resolve-Path $Rest[0]).Path
        $envs = Env-Args
        W run --rm --network $Network -v "$($Volumes.data):/data" -v "$($Volumes.certs):/certs" -v "${dir}:/work" `
            @envs $Image python -m redbox_backend admin build-seed @($Rest[1..($Rest.Count - 1)])
    }
    "import-store" {
        if (-not $Rest) { throw "usage: rb.ps1 import-store <.store folder | data-*.zip | folder of zips> [admin import-store options]" }
        $src = (Resolve-Path $Rest[0]).Path
        if (Test-Path $src -PathType Leaf) { $mount = Split-Path $src; $arg = "/src/" + (Split-Path $src -Leaf) }
        else { $mount = $src; $arg = "/src" }
        $opts = if ($Rest.Count -gt 1) { $Rest[1..($Rest.Count - 1)] } else { @("--config-files", "--store-files") }
        $envs = Env-Args
        W run --rm --network $Network -v "$($Volumes.data):/data" -v "$($Volumes.certs):/certs" -v "${mount}:/src:ro" `
            @envs $Image python -m redbox_backend admin import-store $arg @opts
    }
    "import-packages" {
        # Every data-*.zip in PACKAGES_DIR (or the given folder), read-only; the newest is served.
        $vars = Read-Env
        $dir = if ($Rest) { $Rest[0] } else { $vars["PACKAGES_DIR"] }
        if (-not $dir -or -not (Test-Path $dir)) { throw "usage: rb.ps1 import-packages <folder with data-*.zip> (or set PACKAGES_DIR)" }
        $dir = (Resolve-Path $dir).Path
        $opts = if ($Rest.Count -gt 1) { $Rest[1..($Rest.Count - 1)] } else { @() }
        $envs = Env-Args
        W run --rm --network $Network -v "$($Volumes.data):/data" -v "$($Volumes.certs):/certs" -v "${dir}:/packages:ro" `
            @envs $Image python -m redbox_backend admin import-store /packages @opts
    }
    "export-ca" {
        $out = Join-Path $Backend "certs"
        New-Item -ItemType Directory -Force $out | Out-Null
        W run --rm -v "$($Volumes.certs):/certs" -v "${out}:/out" $Image python -m redbox_backend certs --export /out | Out-Null
        Write-Host "CA written to $out\ca.crt and $out\ca.cer"
    }
    "test" {
        # Uses the working tree's code (mounted over /app) so tests can be re-run without a rebuild.
        $ucs = if (Test-Path $UcsEncryptionSource) { @("-v", "$(Split-Path $UcsEncryptionSource):/ucs:ro", "-e", "UCS_ENCRYPTION_SOURCE=/ucs/EncryptionService.cs") } else { @() }
        $sel = if ($Rest) { $Rest } else { @("tests") }
        # Real update-store seeds as read-only fixtures (tests/test_packages.py), when PACKAGES_DIR is set.
        $tv = Read-Env
        if ($tv["PACKAGES_DIR"] -and (Test-Path $tv["PACKAGES_DIR"])) {
            $ucs += @("-v", "$((Resolve-Path $tv['PACKAGES_DIR']).Path):/packages:ro", "-e", "RB_PACKAGES_DIR=/packages")
        }
        $envs = Env-Args
        W run --rm --network $Network -v "$($Volumes.data):/data" -v "$($Volumes.certs):/certs" -v "${Backend}:/app:ro" @ucs @envs `
            -e CLOUDAPI_URL=https://rb-cloudapi -e UPDATESVC_HOST=rb-updatesvc -e IOT_HOST=rb-iot -e PYTHONDONTWRITEBYTECODE=1 $Image `
            sh -c "pip install -q pytest httpx >/dev/null 2>&1; cd /app && python -m pytest -p no:cacheprovider -q -s $($sel -join ' ')"
    }
    default {
        Get-Help $PSCommandPath -Detailed
    }
}
