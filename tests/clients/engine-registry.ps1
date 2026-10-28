<#
.SYNOPSIS
  Machine settings for running the real (32-bit) Kiosk Engine on this PC against the self-hosted backend, and their
  exact removal.
.DESCRIPTION
  -Apply writes the engine's settings under HKLM\SOFTWARE\WOW6432Node\Redbox\REDS\Kiosk Engine (the values a real
  kiosk has, with every host pointed at this backend; no RebootTime, so nothing asks the PC to restart) and records
  every key and value it adds or changes in -Manifest (with the previous value). -Clean undoes exactly that.
  Writing there needs either an elevated shell or write access to HKLM\SOFTWARE\WOW6432Node\Redbox, e.g. granted
  once from an elevated PowerShell (see backend\README.md, "Running the real Kiosk Engine").
.EXAMPLE
  .\engine-registry.ps1 -Apply -KioskId <store ID>
  .\engine-registry.ps1 -Clean
#>
param(
    [switch] $Apply,
    [switch] $Clean,
    [int] $KioskId,  # the store ID (the kiosk data's store); required with -Apply
    [string] $BrokerUrl = "gtcp://localhost:7890/RegistrationBroker/Default.rem",
    [string] $KioskServicesUrl = "http://localhost:80/KioskServices/MessageService.rem",
    [string] $HardwareServiceUrl = "rcp://127.0.0.1:7001",
    [string] $Manifest = (Join-Path $PSScriptRoot "..\..\local\engine-registry-added.json")
)
$ErrorActionPreference = "Stop"
$root = "HKLM:\SOFTWARE\WOW6432Node\Redbox\REDS\Kiosk Engine"

if ($Clean) {
    if (-not (Test-Path $Manifest)) { "no manifest at $Manifest; nothing recorded"; exit 0 }
    $m = Get-Content -Raw $Manifest | ConvertFrom-Json
    foreach ($v in @($m.values)) {
        if (-not (Test-Path $v.key)) { continue }
        if ($null -eq $v.previous) { Remove-ItemProperty -Path $v.key -Name $v.name -ErrorAction SilentlyContinue; "removed $($v.key)\$($v.name)" }
        else { Set-ItemProperty -Path $v.key -Name $v.name -Value $v.previous; "restored $($v.key)\$($v.name)" }
    }
    # Keys created by -Apply, deepest first (and whatever the engine wrote inside them).
    foreach ($k in @($m.keys) | Sort-Object Length -Descending) {
        if (Test-Path $k) { Remove-Item -Path $k -Recurse -Force; "removed key $k" }
    }
    # Values and keys the engine itself added to keys that existed before -Apply (e.g. Store\RebootTime).
    if ($m.snapshot) {
        $before = @{}; foreach ($e in @($m.snapshot)) { $before[$e] = $true }
        foreach ($key in @(Get-Item $root) + @(Get-ChildItem $root -Recurse)) {
            $kp = "HKLM:\" + $key.Name.Substring($key.Name.IndexOf('\') + 1)
            if (-not $before.ContainsKey("$kp|")) { continue }  # not there before: a new key, handled above
            foreach ($n in $key.GetValueNames()) {
                if (-not $before.ContainsKey("$kp|$n")) { Remove-ItemProperty -Path $kp -Name $n; "removed engine-written $kp\$n" }
            }
        }
        foreach ($key in @(Get-ChildItem $root -Recurse) | Sort-Object { $_.Name.Length } -Descending) {
            $kp = "HKLM:\" + $key.Name.Substring($key.Name.IndexOf('\') + 1)
            if ((Test-Path $kp) -and -not $before.ContainsKey("$kp|")) { Remove-Item $kp -Recurse -Force; "removed engine-written key $kp" }
        }
    }
    Remove-Item $Manifest
    exit 0
}
if (-not $Apply) { Get-Help $MyInvocation.MyCommand.Path; exit 1 }
if (-not $KioskId) { throw "-KioskId (the kiosk's store ID) is required with -Apply" }

$record = @{ values = @(); keys = @(); snapshot = @() }
if (Test-Path $Manifest) {
    $old = Get-Content -Raw $Manifest | ConvertFrom-Json
    $record.values = @($old.values); $record.keys = @($old.keys); $record.snapshot = @($old.snapshot)
} elseif (Test-Path $root) {
    # What is there before the first -Apply: "<key>|" per key and "<key>|<value name>" per value.
    foreach ($key in @(Get-Item $root) + @(Get-ChildItem $root -Recurse)) {
        $kp = "HKLM:\" + $key.Name.Substring($key.Name.IndexOf('\') + 1)
        $record.snapshot += "$kp|"
        foreach ($n in $key.GetValueNames()) { $record.snapshot += "$kp|$n" }
    }
}
function Ensure-Key($path) {
    if (-not (Test-Path $path)) {
        $parent = Split-Path $path
        if (-not (Test-Path $parent)) { Ensure-Key $parent }
        New-Item -Path $path | Out-Null
        $script:record.keys += $path
    }
}
function Set-Setting($sub, $name, $value, $type = "String") {
    $path = if ($sub) { "$root\$sub" } else { $root }
    Ensure-Key $path
    $existing = (Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue).$name
    if (-not ($script:record.values | Where-Object { $_.key -eq $path -and $_.name -eq $name })) {
        $script:record.values += [pscustomobject]@{ key = $path; name = $name; previous = $existing }
    }
    New-ItemProperty -Path $path -Name $name -Value $value -PropertyType $type -Force | Out-Null
}

# From a real kiosk's export, hosts replaced; RebootTime, card-reader lists and serial numbers left out.
Set-Setting "Store" "ID" "$KioskId"
Set-Setting "Store" "Environment" "Production"
Set-Setting "Store" "MaxSlots" "90"
Set-Setting "Store" "MaxDecks" "8"
Set-Setting "Store" "RunInitialSync" "False"
Set-Setting "Store" "InitialSyncType" "background"
Set-Setting "Store" "WatchDogEnabled" "False"
Set-Setting "Store" "OutOfOrder" "false"
Set-Setting "Store" "Duetime" "09:00PM"
Set-Setting "Core" "BundleSearchPath" @("..\bundles") "MultiString"
Set-Setting "Field Maintenance" "MaintenanceMode" "false"
foreach ($svc in "Authentication Services", "Health Services", "Inventory Services", "Merchant Services", "Promotion Services",
                 "Reservation Services", "Store Services") {
    Set-Setting "Remote Services\$svc" "LiveCall" "True"
}
Set-Setting "Remote Services\Health Services" "IsServerBusy" "False"
Set-Setting "Remote Services\Reservation Services" "BrokerServicesURL" $BrokerUrl
Set-Setting "Remote Services\Store Services" "KioskServicesURL" $KioskServicesUrl
Set-Setting "Remote Services\Store Services" "HardwareServiceURL" $HardwareServiceUrl
# The Device Service's simulated card reader (no card reader hardware): without it the rental application goes to
# maintenance mode for the card reader (0072CDRR).
Set-Setting "Remote Services\Device Service Simulator" "UseSimulatedCardReader" "True"
Set-Setting "Remote Services\Device Service Simulator" "IsCardReaderTampered" "False"
Set-Setting "Remote Services\Device Service Simulator" "IsConnectedToDeviceService" "True"
Set-Setting "Remote Services\Device Service Simulator" "IsCardReaderConnected" "True"

New-Item -ItemType Directory -Force (Split-Path $Manifest) | Out-Null
$record | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $Manifest
"applied; recorded $(@($record.values).Count) values and $(@($record.keys).Count) new keys in $Manifest"
