<#
.SYNOPSIS
  A real Redbox kiosk (Kiosk Engine, HAL service, emulated hardware) in Windows Sandbox, against this backend.
.DESCRIPTION
  Like a Dockerfile for Windows Sandbox: every sandbox starts from a clean Windows, and provision.ps1 (its logon
  command) runs steps\NN-*.ps1 in order, so the same inputs give the same kiosk every time.

    prepare  Build the inputs under -Work (once; re-run to refresh). Pinned downloads checked by SHA-256 (embeddable
             Python, com0com, the emulator's Python packages), unredbox/HW-Emu-Python at a pinned commit, the Kiosk
             Engine and Device Service exported from the backend's update store, the kiosk image's data folder and
             configured HAL install (copied; the image is not changed), the backend CA, and the store ID.
    up       Start the relay (backend ports from the Default Switch address only) and the sandbox; it provisions
             itself (a few minutes). Needs the backend running (rb.ps1 up).
    status   Sandbox, relay, provisioning log and the broker's view of the kiosk.
    down     Close the sandbox (everything inside is discarded) and stop the relay. -Work is kept for the next up.
.EXAMPLE
  .\sandbox.ps1 prepare -ImageDir "C:\Users\me\Downloads\Images\Redbox" -StoreId <store ID>
  .\sandbox.ps1 up
  .\sandbox.ps1 status
  .\sandbox.ps1 down
#>
param(
    [Parameter(Mandatory, Position = 0)] [ValidateSet("prepare", "up", "status", "down")] [string] $Command,
    [string] $Work = "E:\temp\rb-sandbox",
    [string] $ImageDir,          # prepare: a kiosk image's "C:\Program Files\Redbox" (halservice, REDS\Kiosk Engine\data)
    [string] $StoreId            # prepare: the kiosk data's store ID
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$backend = Resolve-Path (Join-Path $here "..\..\..")
$wslc = "C:\Program Files\WSL\wslc.exe"
$tarExe = Join-Path $env:SystemRoot "System32\tar.exe"     # Windows' bsdtar (a Git/MSYS tar on PATH cannot open E:\...)
$pins = @(
    @{ Name = "python-3.12.7-embed-amd64.zip"; Url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip"
       Sha256 = "0D57BB6CB078B74D23DBFE91F77D6780D45BED328911609F1F7EE2BA1606BF44" },
    @{ Name = "com0com-2.2.2.0-x64-fre-signed.zip"; Url = "https://sourceforge.net/projects/com0com/files/com0com/2.2.2.0/com0com-2.2.2.0-x64-fre-signed.zip/download"
       Sha256 = "3BC7313E8577774CE78CFF4D1C816FAF96CFA48C485BBCC5527D4569F7D5E373" }
)
$emuRepo = "https://github.com/unredbox/HW-Emu-Python"
$emuCommit = "8259b87af3649b9a82259b5ebfa4d6755460aa31"   # branch v2
$tools = Join-Path $Work "tools"; $cache = Join-Path $Work "cache"; $control = Join-Path $Work "control"
$engineRoot = Join-Path $Work "engine"                       # export root: <engineRoot>\Program Files\Redbox\REDS
$reds = Join-Path $engineRoot "Program Files\Redbox\REDS"
$relayPid = Join-Path $Work "relay.pid"

function Step($m) { Write-Host "== $m" }

function Get-Pinned($p) {
    $f = Join-Path $cache $p.Name
    if (-not (Test-Path $f) -or (Get-FileHash $f -Algorithm SHA256).Hash -ne $p.Sha256) {
        Step "download $($p.Name)"
        & curl.exe -sSL -o $f $p.Url
        if ($LASTEXITCODE) { throw "download failed: $($p.Url)" }
    }
    $h = (Get-FileHash $f -Algorithm SHA256).Hash
    if ($h -ne $p.Sha256) { throw "$($p.Name): SHA-256 $h, expected $($p.Sha256)" }
    return $f
}

function Export-Repos($dest, [string[]] $repos) {
    # admin export writes a tar to stdout; cmd's redirection keeps it binary (a PowerShell 5.1 pipe would not).
    New-Item -ItemType Directory -Force $dest | Out-Null
    $tar = Join-Path $dest "export.tar"
    cmd /c "`"$wslc`" exec rb-updatesvc python -m redbox_backend admin export $($repos -join ' ') > `"$tar`""
    if ($LASTEXITCODE -or (Get-Item $tar).Length -lt 1MB) { throw "admin export failed (is the backend running?)" }
    & $tarExe -xf $tar -C $dest
    if ($LASTEXITCODE) { throw "unpacking $tar failed" }
    Remove-Item $tar
}

switch ($Command) {
"prepare" {
    if (-not $ImageDir -or -not (Test-Path (Join-Path $ImageDir "halservice")) -or
        -not (Test-Path (Join-Path $ImageDir "REDS\Kiosk Engine\data"))) {
        throw "-ImageDir must be a kiosk image's Redbox folder (with halservice\ and REDS\Kiosk Engine\data\)"
    }
    if (-not $StoreId) { throw "-StoreId (the kiosk data's store ID) is required" }
    foreach ($d in $tools, $cache, $control, (Join-Path $Work "KioskLogs")) { New-Item -ItemType Directory -Force $d | Out-Null }

    Step "tools: Python, com0com, emulator packages"
    $py = Join-Path $tools "python"
    Remove-Item $py -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive (Get-Pinned $pins[0]) $py
    $wheels = Join-Path $cache "wheels"
    & python -m pip download --require-hashes --only-binary=:all: --platform win_amd64 --python-version 3.12 `
        --implementation cp --no-deps -r (Join-Path $here "tools-requirements.txt") -d $wheels -q
    if ($LASTEXITCODE) { throw "pip download failed" }
    foreach ($w in Get-ChildItem $wheels -Filter *.whl) {   # the embeddable Python has no pip: unpack into its folder
        Copy-Item $w.FullName "$($w.FullName).zip" -Force; Expand-Archive "$($w.FullName).zip" $py -Force; Remove-Item "$($w.FullName).zip"
    }
    $c0c = Join-Path $tools "com0com"
    Remove-Item $c0c -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive (Get-Pinned $pins[1]) $c0c
    # The driver's publisher, trusted in the sandbox before installing so Windows does not ask (needs 7-Zip here).
    $7z = @("$env:ProgramFiles\7-Zip\7z.exe", (Get-Command 7z -ErrorAction SilentlyContinue).Source) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($7z) {
        $x = Join-Path $cache "com0com-x"; Remove-Item $x -Recurse -Force -ErrorAction SilentlyContinue
        & $7z x -y "-o$x" (Get-ChildItem $c0c -Recurse -Filter setup.exe | Select-Object -First 1).FullName | Out-Null
        $sig = Get-AuthenticodeSignature (Join-Path $x "com0com.cat")
        [IO.File]::WriteAllBytes((Join-Path $tools "com0com-publisher.cer"), $sig.SignerCertificate.Export("Cert"))
        "com0com publisher: $($sig.SignerCertificate.Subject)"
    } else { "no 7-Zip: Windows will ask once in the sandbox to install the com0com driver" }

    Step "emulator: $emuRepo @ $emuCommit"
    $src = Join-Path $cache "HW-Emu-Python"
    if (-not (Test-Path (Join-Path $src ".git"))) { git clone -q $emuRepo $src }
    git -C $src fetch -q origin $emuCommit
    $emu = Join-Path $tools "hw-emu"; Remove-Item $emu -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory $emu | Out-Null
    $emuTar = Join-Path $cache "hw-emu.tar"
    git -C $src archive -o $emuTar $emuCommit
    & $tarExe -xf $emuTar -C $emu
    if ($LASTEXITCODE -or -not (Test-Path (Join-Path $emu "hal_hw_emu\__main__.py"))) { throw "emulator checkout failed" }

    Step "Kiosk Engine, Device Service, Update Manager, Update Client, Kiosk Client from the backend's update store"
    Remove-Item $engineRoot -Recurse -Force -ErrorAction SilentlyContinue
    # Only the engine and the Device Service run in the sandbox; the other three are there so that rb-reprovision
    # finds and edits their configuration files as on a kiosk.
    Export-Repos $engineRoot @("kop-v2-engine", "kop-rental-bundles", "kop-shared-bundles", "device-service",
                               "kop-update-manager", "updateclient", "kioskclient")
    Step "the kiosk image's data folder"
    robocopy (Join-Path $ImageDir "REDS\Kiosk Engine\data") (Join-Path $reds "Kiosk Engine\data") /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
    # Its config files point wherever the image was repointed; use Redbox's host names (hosts-mapped in the sandbox).
    $urls = @{ "KioskDataServicesUrl" = "https://dataservice.kiosk.prod.redbox.com"
               "BluefinServiceUrl" = "https://dataservice.kiosk.prod.redbox.com/bluefinservice"
               "FraudServicesUrl" = "https://dataservice.kiosk.prod.redbox.com/"; "AwsFraudServicesUrl" = "http://localhost/frauddata/" }
    foreach ($cfg in Get-ChildItem (Join-Path $reds "Kiosk Engine\data") -Filter *.config) {
        $j = Get-Content -Raw $cfg.FullName | ConvertFrom-Json
        $changed = $false
        foreach ($s in $j.ConfigurationData) { if ($urls.ContainsKey($s.SettingName) -and $s.Value -ne $urls[$s.SettingName]) { $s.Value = $urls[$s.SettingName]; $changed = $true } }
        if ($changed) { $j | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 $cfg.FullName; "  $($cfg.Name): service URLs set to Redbox host names" }
    }
    Step "the kiosk image's HAL install"
    robocopy (Join-Path $ImageDir "halservice") (Join-Path $Work "halservice") /MIR /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null

    Step "backend CA and store ID"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $backend "scripts\rb.ps1") export-ca | Out-Null
    Copy-Item (Join-Path $backend "certs\ca.cer") (Join-Path $control "ca.cer") -Force
    Set-Content (Join-Path $control "store-id.txt") $StoreId

    Step "rb-reprovision and this kiosk's settings file (step 40 applies it)"
    & (Join-Path $backend "software\src\Tools\Reprovision\build.ps1") -Out (Join-Path $tools "reprovision")
    # The hosts address is filled in inside the sandbox (--address), where the Default Switch address is known.
    $profilePath = Join-Path $control "kiosk-profile.json"
    cmd /c "`"$wslc`" exec -e LOG_LEVEL=WARNING rb-updatesvc python -m redbox_backend admin kiosk-profile $StoreId --address 0.0.0.0 > `"$profilePath`""
    if ($LASTEXITCODE) { throw "admin kiosk-profile failed" }
    "prepared $Work"
}
"up" {
    if (-not (Test-Path (Join-Path $control "ca.cer"))) { throw "run 'sandbox.ps1 prepare' first" }
    if ((& wsb list 2>$null) -or (Get-Process WindowsSandboxRemoteSession -ErrorAction SilentlyContinue)) {
        throw "a Windows Sandbox is still running (only one can); run 'sandbox.ps1 down' first"
    }
    if (-not (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:80/_health -TimeoutSec 5 -ErrorAction SilentlyContinue)) {
        throw "the backend is not answering on 127.0.0.1:80 (rb.ps1 up)"
    }
    foreach ($d in "inbox", "outbox", "done") { Remove-Item (Join-Path $control $d) -Recurse -Force -ErrorAction SilentlyContinue }
    $ip = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "vEthernet (Default Switch)").IPAddress
    if (-not ((Test-Path $relayPid) -and (Get-Process -Id (Get-Content $relayPid) -ErrorAction SilentlyContinue))) {
        $p = Start-Process python -ArgumentList "-u", (Join-Path $here "relay.py"), "--listen", $ip -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Work "relay.log") -RedirectStandardError (Join-Path $Work "relay.err") -PassThru
        Set-Content $relayPid $p.Id
        "relay on ${ip} (pid $($p.Id))"
    }
    & (Join-Path $here "make-wsb.ps1") -RedsDir $reds -HalDir (Join-Path $Work "halservice") -LogsDir (Join-Path $Work "KioskLogs") `
        -ToolsDir $tools -ControlDir $control -Wsb (Join-Path $Work "redbox-kiosk.wsb") -Start
    "the sandbox provisions itself; follow it with: sandbox.ps1 status"
}
"status" {
    "sandboxes: " + ((& wsb list 2>$null) -join ", ")
    "relay: " + $(if ((Test-Path $relayPid) -and (Get-Process -Id (Get-Content $relayPid) -ErrorAction SilentlyContinue)) { "running" } else { "stopped" })
    $log = Join-Path $control "outbox\provision.log"
    if (Test-Path $log) { "--- provisioning (tail)"; Get-Content $log -Tail 15 }
    "--- broker"
    & $wslc logs rb-remoting 2>&1 | Select-String "registered|disconnected|connection lost" | Select-Object -Last 3 | ForEach-Object { $_.Line.Substring(0, [Math]::Min(140, $_.Line.Length)) }
}
"down" {
    foreach ($id in (& wsb list 2>$null)) { if ($id) { & wsb stop --id $id; "stopped sandbox $id" } }
    # Windows runs one sandbox at a time: wait until it has really gone, so a following 'up' does not hang.
    for ($i = 0; $i -lt 60 -and ((& wsb list 2>$null) -or (Get-Process WindowsSandboxRemoteSession -ErrorAction SilentlyContinue)); $i++) { Start-Sleep 2 }
    Get-Process WindowsSandboxRemoteSession -ErrorAction SilentlyContinue | Stop-Process -Force
    if (Test-Path $relayPid) { Stop-Process -Id (Get-Content $relayPid) -Force -ErrorAction SilentlyContinue; Remove-Item $relayPid; "stopped relay" }
}
}
