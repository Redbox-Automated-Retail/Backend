<#
.SYNOPSIS
  Write a Windows Sandbox configuration that runs a real kiosk stack (Kiosk Engine, HAL service, hardware emulator)
  against the self-hosted backend on this PC, and start it.
.DESCRIPTION
  Mapped into the sandbox (all scratch copies; the sandbox is discarded when closed):
    -RedsDir      -> C:\Program Files\Redbox\REDS        (Kiosk Engine with its data, DeviceService, ...)
    -HalDir       -> C:\Program Files\Redbox\halservice   (a configured HAL install)
    -LogsDir      -> C:\Program Files\Redbox\KioskLogs
    -ToolsDir     -> C:\rb\tools (read-only)              (python embeddable + pyserial, com0com, pcb.py/arcus.py)
    -ControlDir   -> C:\rb\control                        (command loop: inbox/outbox/done, see loop.ps1)
    this folder   -> C:\rb\clients (read-only)            (setup scripts, engine-registry.ps1)
  At logon loop.ps1 starts; the host then drops scripts into ControlDir\inbox (see run-sandbox-step.ps1).
#>
param(
    [string] $RedsDir = "E:\temp\rb-engine2024\Program Files\Redbox\REDS",
    [string] $HalDir = "E:\temp\rb-sandbox\halservice",
    [string] $LogsDir = "E:\temp\rb-sandbox\KioskLogs",
    [string] $ToolsDir = "E:\temp\rb-sandbox\tools",
    [string] $ControlDir = "E:\temp\rb-sandbox\control",
    [string] $Wsb = "E:\temp\rb-sandbox\redbox-kiosk.wsb",
    [switch] $Start
)
$clients = Split-Path $PSScriptRoot
function Map($host_, $sandbox, $ro) {
    "    <MappedFolder><HostFolder>$host_</HostFolder><SandboxFolder>$sandbox</SandboxFolder><ReadOnly>$($ro.ToString().ToLower())</ReadOnly></MappedFolder>"
}
New-Item -ItemType Directory -Force $ControlDir, $LogsDir | Out-Null
$xml = @"
<Configuration>
  <Networking>Enable</Networking>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
$(Map $RedsDir 'C:\Program Files\Redbox\REDS' $false)
$(Map $HalDir 'C:\Program Files\Redbox\halservice' $false)
$(Map $LogsDir 'C:\Program Files\Redbox\KioskLogs' $false)
$(Map $ToolsDir 'C:\rb\tools' $true)
$(Map $ControlDir 'C:\rb\control' $false)
$(Map $clients 'C:\rb\clients' $true)
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File C:\rb\clients\sandbox\loop.ps1</Command></LogonCommand>
</Configuration>
"@
Set-Content -Path $Wsb -Value $xml -Encoding UTF8
"wrote $Wsb"
if ($Start) { Start-Process $Wsb; "started" }
