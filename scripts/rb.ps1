<#
.SYNOPSIS
  Build, run, stop and reset the Redbox self-hosted backend with WSLC (wslc.exe).

.DESCRIPTION
  WSLC has no compose, so this script creates the network, volumes and containers itself.

    rb.ps1 build                 build the redbox-backend image
    rb.ps1 up                    create network/volumes, generate certificates, start every container
    rb.ps1 down                  stop and remove the containers (data volumes are kept)
    rb.ps1 reset [-Force]        down + delete the volumes (database, content, certificates)
    rb.ps1 status                containers and health
    rb.ps1 logs <service>        service = db | updatesvc | cloudapi | iot
    rb.ps1 admin <args...>       python -m redbox_backend admin <args> inside rb-iot
    rb.ps1 import-store <path>   import a kiosk's "REDS\Update Manager\.store" (repositories, config/store files)
    rb.ps1 export-ca             copy ca.crt / ca.cer to backend\certs\ for installing on kiosks
    rb.ps1 test                  run the protocol tests inside a container against the running stack

  Settings come from backend\local\backend.env (copy example.env). Host ports: 443 (cloud APIs, IoT
  certificate service, S3), 7002 (srcp Update Service), 7080 (change-set downloads), 8883 (MQTT/TLS);
  8081 (IoT operator API) is published on 127.0.0.1 only.
#>
param(
    [Parameter(Position = 0)] [string] $Command = "help",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)] [string[]] $Rest,
    [switch] $Force
)
$ErrorActionPreference = "Stop"
$Wslc = "C:\Program Files\WSL\wslc.exe"
$Backend = Split-Path -Parent $PSScriptRoot
$Repo = Split-Path -Parent $Backend
$Image = "redbox-backend:latest"
$Network = "redbox"
$Volumes = @{ pg = "rb-pgdata"; data = "rb-data"; certs = "rb-certs" }
$EnvFile = Join-Path $Backend "local\backend.env"
$UcsEncryptionSource = Join-Path $Repo "src\UpdateClient\UpdateClientService.API\Services\IoT\Certificate\Security\EncryptionService.cs"

function W { & $Wslc @args; if ($LASTEXITCODE -ne 0) { throw "wslc $($args -join ' ') failed ($LASTEXITCODE)" } }
# Run wslc ignoring failures (PowerShell 5.1 turns native stderr into errors under "Stop").
function WQuiet {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Wslc @args *> $null; $LASTEXITCODE -eq 0 } finally { $ErrorActionPreference = $old }
}

function Read-Env {
    $vars = [ordered]@{}
    $file = if (Test-Path $EnvFile) { $EnvFile } else { Join-Path $Backend "example.env" }
    foreach ($line in Get-Content $file) {
        if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') { $vars[$Matches[1]] = $Matches[2].Trim() }
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
        Start-Service "rb-cloudapi" "rb-cloudapi" @("${bind}443:443") @("python", "-m", "redbox_backend", "cloudapi") $ucsMount $ucsEnv
        Start-Service "rb-updatesvc" "rb-updatesvc" (@("${bind}7002:7002", "${bind}7080:7080") + $plain) @("python", "-m", "redbox_backend", "updatesvc")
        Start-Service "rb-iot" "rb-iot" (@("${bind}8883:8883", "127.0.0.1:8081:8081") + $mplain) @("python", "-m", "redbox_backend", "iot")
        & $PSCommandPath status
    }
    "down" {
        foreach ($c in "rb-iot", "rb-updatesvc", "rb-cloudapi", "rb-db") {
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
    "status" {
        & $Wslc list --all
        foreach ($probe in @(@("rb-cloudapi", "https://localhost:443/_health"), @("rb-updatesvc", "http://localhost:7080/_health"))) {
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
    "import-store" {
        if (-not $Rest) { throw "usage: rb.ps1 import-store <path to 'REDS\Update Manager\.store'> [admin import-store options]" }
        $store = (Resolve-Path $Rest[0]).Path
        $opts = if ($Rest.Count -gt 1) { $Rest[1..($Rest.Count - 1)] } else { @("--config-files", "--store-files") }
        $envs = Env-Args
        W run --rm --network $Network -v "$($Volumes.data):/data" -v "$($Volumes.certs):/certs" -v "${store}:/store:ro" `
            @envs $Image python -m redbox_backend admin import-store /store @opts
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
        $envs = Env-Args
        W run --rm --network $Network -v "$($Volumes.data):/data" -v "$($Volumes.certs):/certs" -v "${Backend}:/app:ro" @ucs @envs `
            -e CLOUDAPI_URL=https://rb-cloudapi -e UPDATESVC_HOST=rb-updatesvc -e IOT_HOST=rb-iot -e PYTHONDONTWRITEBYTECODE=1 $Image `
            sh -c "pip install -q pytest httpx >/dev/null 2>&1; cd /app && python -m pytest -p no:cacheprovider -q $($sel -join ' ')"
    }
    default {
        Get-Help $PSCommandPath -Detailed
    }
}
