<#
.SYNOPSIS
  Run a kiosk installer (test mode) on a clean Windows in Windows Sandbox against this backend.
  The installers are in the Software repository (the software\ submodule): software\installer\cli
  (Install-Kiosk.ps1) and software\installer\classic (Redbox's store installer, modernized).
.DESCRIPTION
  up      Start tests\clients\sandbox\relay.py (the backend's ports on the Hyper-V Default Switch address) and a
          sandbox that maps the payload read-only and runs sandbox-logon.ps1 (this folder) at logon: the hardware emulator
          if -Tools has one (tests\clients\sandbox\sandbox.ps1 prepare builds it), then the installer.
  status  The installer log, the services and processes in the sandbox, and what the backend saw of the kiosk.
  run     Run a command in the sandbox (tests\clients\sandbox\run-sandbox-step.ps1).
  down    Close the sandbox and stop the relay.
  The payload comes from software\installer\cli\New-InstallerPayload.ps1 -TestMode, with a kiosk profile made with
  --address set to the Default Switch address (status prints it). Only one Windows Sandbox can run at a time.
  -Classic <dir> runs the classic installer instead: <dir> is the output of
  software\installer\classic\Build-ClassicInstaller.ps1 -TestMode, and classic-logon.ps1 goes through its wizard.
.EXAMPLE
  .\tests\installer\sandbox.ps1 up -Payload E:\temp\kiosk-74001 -Tools E:\temp\rb-sandbox\tools
  .\tests\installer\sandbox.ps1 status -StoreId 74001
  .\tests\installer\sandbox.ps1 run -Run 'Get-Service *$*'
  .\tests\installer\sandbox.ps1 down
#>
param(
    [Parameter(Mandatory, Position = 0)] [ValidateSet("up", "status", "run", "down")] [string] $Command,
    [string] $Payload,
    [string] $Tools,                                  # optional: tests\clients\sandbox prepare's tools folder (emulator)
    [string] $Original,                               # up: run Redbox's original setup exe instead (comparison; see original-logon.ps1)
    [string] $DataZip,                                # up -Original: the data zip to put next to it
    [string] $Classic,                                # up: run the classic installer built by software\installer\classic\Build-ClassicInstaller.ps1
    [string] $Work = "E:\temp\rb-installer-sandbox",
    [string] $StoreId,
    [string] $Run
)
$ErrorActionPreference = "Stop"
$backend = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$clients = Join-Path $backend "tests\clients"
$control = Join-Path $Work "control"
$relayPid = Join-Path $Work "relay.pid"
$wslc = "C:\Program Files\WSL\wslc.exe"
function Test-SandboxRunning { [bool]((& wsb list 2>$null) -or (Get-Process WindowsSandboxRemoteSession -ErrorAction SilentlyContinue)) }

switch ($Command) {
"up" {
    if ($Classic) {
        if (-not (Test-Path (Join-Path $Classic "setup.exe"))) { throw "-Classic must be a Build-ClassicInstaller.ps1 folder" }
    } elseif (-not $Payload -or -not (Test-Path (Join-Path $Payload "settings.json"))) { throw "-Payload must be a New-InstallerPayload.ps1 folder" }
    if (Test-SandboxRunning) { throw "a Windows Sandbox is already running (only one can); close it first" }
    New-Item -ItemType Directory -Force $Work | Out-Null
    Remove-Item $control, (Join-Path $Work "KioskLogs") -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $control, (Join-Path $Work "KioskLogs") | Out-Null
    $ip = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "vEthernet (Default Switch)").IPAddress
    $prof = if ($Classic) { Join-Path $Classic "kiosk-profile.json" } else { Join-Path $Payload (Get-Content -Raw (Join-Path $Payload "settings.json") | ConvertFrom-Json).Backend.ProfileFile }
    $addr = (Get-Content -Raw $prof | ConvertFrom-Json).Hosts.Address
    if ($addr -and $addr -ne $ip) { Write-Warning "the profile maps the backend to $addr, but the Default Switch address is $ip" }
    if (-not ((Test-Path $relayPid) -and (Get-Process -Id (Get-Content $relayPid) -ErrorAction SilentlyContinue))) {
        $p = Start-Process python -ArgumentList "-u", (Join-Path $clients "sandbox\relay.py"), "--listen", $ip -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Work "relay.log") -RedirectStandardError (Join-Path $Work "relay.err") -PassThru
        Set-Content $relayPid $p.Id
        "relay on ${ip} (pid $($p.Id))"
    }
    function Map($h, $s, $ro) { "    <MappedFolder><HostFolder>$h</HostFolder><SandboxFolder>$s</SandboxFolder><ReadOnly>$($ro.ToString().ToLower())</ReadOnly></MappedFolder>" }
    $maps = @((Map $control 'C:\rb\control' $false), (Map $clients 'C:\rb\clients' $true),
              (Map (Join-Path $Work "KioskLogs") 'C:\Program Files\Redbox\KioskLogs' $false),
              (Map $PSScriptRoot 'C:\rb\installer-tests' $true))
    if ($Tools) { $maps += Map (Resolve-Path $Tools) 'C:\rb\tools' $true }
    if ($Classic) {
        $maps += Map (Resolve-Path $Classic) 'C:\rb\classic' $true
        $logon = 'C:\rb\installer-tests\classic-logon.ps1'
    } else {
        $maps += Map (Resolve-Path $Payload) 'C:\rb\payload' $true
        $logon = 'C:\rb\installer-tests\sandbox-logon.ps1'
    }
    if ($Original) {
        # Setup exe and data zip side by side, mapped read-only.
        $od = Join-Path $Work "original"; Remove-Item $od -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory $od | Out-Null
        foreach ($src in $Original, $DataZip) { Copy-Item $src (Join-Path $od (Split-Path $src -Leaf)) }
        $maps += Map $od 'C:\rb\original' $true
        $logon = 'C:\rb\installer-tests\original-logon.ps1'
    }
    $wsb = Join-Path $Work "installer-test.wsb"
    @"
<Configuration>
  <Networking>Enable</Networking>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
$($maps -join "`n")
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File $logon</Command></LogonCommand>
</Configuration>
"@ | Set-Content -Encoding UTF8 $wsb
    Start-Process $wsb
    "started $wsb; follow it with: sandbox.ps1 status"
}
"status" {
    "sandbox: " + $(if (Test-SandboxRunning) { "running" } else { "not running" })
    "Default Switch address: " + (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "vEthernet (Default Switch)").IPAddress
    $l = Join-Path $control "state\install.log"
    if (Test-Path $l) { "--- install.log (tail)"; Get-Content $l -Tail 25 }
    elseif (Test-Path (Join-Path $control "outbox\logon.log")) { "--- logon.log (tail)"; Get-Content (Join-Path $control "outbox\logon.log") -Tail 25 }
    $o = Join-Path $control "original\harness.log"
    $ch = Join-Path $control "classic"
    if (Test-Path (Join-Path $ch "harness.log")) {
        "--- classic installer: harness.log (tail)"; Get-Content (Join-Path $ch "harness.log") -Tail 12
        if (Test-Path (Join-Path $ch "logs\front-end.log")) { "--- front-end.log (tail)"; Get-Content (Join-Path $ch "logs\front-end.log") -Tail 12 }
        if (Test-Path (Join-Path $ch "checks.txt")) { "--- checks.txt"; Get-Content (Join-Path $ch "checks.txt") }
    }
    if (Test-Path $o) { "--- original installer: harness.log (tail)"; Get-Content $o -Tail 15; $fe = Get-ChildItem (Join-Path $control "original") -Recurse -Filter "front-end.log" | Select-Object -First 1; if ($fe) { "--- front-end.log (tail)"; Get-Content $fe.FullName -Tail 15 } }
    if ($StoreId) {
        "--- backend: Update Service, broker, IoT for store $StoreId"
        foreach ($c in "rb-updatesvc", "rb-remoting", "rb-iot", "rb-cloudapi") {
            & $wslc logs $c 2>&1 | Select-String -SimpleMatch $StoreId | Select-Object -Last 3 |
                ForEach-Object { "$c | " + $_.Line.Substring(0, [Math]::Min(170, $_.Line.Length)) }
        }
    }
}
"run" {
    & (Join-Path $clients "sandbox\run-sandbox-step.ps1") -Command $Run -ControlDir $control
}
"down" {
    foreach ($id in (& wsb list 2>$null)) { if ($id) { & wsb stop --id $id; "stopped sandbox $id" } }
    for ($i = 0; $i -lt 60 -and (Test-SandboxRunning); $i++) { Start-Sleep 2 }
    if (Test-Path $relayPid) { Stop-Process -Id (Get-Content $relayPid) -Force -ErrorAction SilentlyContinue; Remove-Item $relayPid; "stopped relay" }
}
}
