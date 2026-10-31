# Kiosk Engine machine settings that do not depend on the backend (original: the kiosk-type page, and
# installer-steps.lua "Kiosk Engine Configuration", "Install Redbox Shell" and the stats scheduler step).
# Values are strings, as kernel.setregistrykey wrote them. Backend URLs and the store ID are step 40's.
param($Config)
$cfg = $Config
$store = "$EngineKey\Store"

Set-MachineValue $store "ID" "$($cfg.StoreId)"     # step 40 (rb-reprovision) sets it too; here so it is set without a profile
Set-MachineValue $store "MaxSlots" $(if ($cfg.KioskType -eq "R504") { "72" } else { "90" })
Set-MachineValue $store "Environment" $cfg.Environment
Set-MachineValue $store "RunInitialSync" "False"
Set-MachineValue $store "InitialSyncType" "background"
Set-MachineValue $store "WatchDogEnabled" "true"   # updatemgrd's watchdog starts the engine once HAL answers
foreach ($svc in "Authentication", "Health", "Inventory", "Merchant", "Reservation", "Promotion", "Store") {
    Set-MachineValue "$EngineKey\Remote Services\$svc Services" "LiveCall" "True"
}
Set-MachineValue "$EngineKey\Remote Services\Store Services" "HardwareServiceURL" "rcp://127.0.0.1:7001"

# The two stats jobs the original wrote into the engine's scheduler ("Windows 7 migration - update manager scheduling").
$jobs = '[{"JobName":"StatsMediumTimer","Label":null,"StartTime":{"theDate":634420306327357576,"dateTimeKind":"Utc","dateString":"2011-05-26T18:17:12.7357576Z"},"EndTime":null,"CronExpression":"19 28 1-23/2 * * ? ","FunctionName":"stats_run_medium_timer","Program":null,"MisfireInstruction":"FireOnceNow","Parameters":[]},{"JobName":"StatsDailyTimer","Label":null,"StartTime":{"theDate":634420306328358576,"dateTimeKind":"Utc","dateString":"2011-05-26T18:17:12.8358576Z"},"EndTime":null,"CronExpression":"19 28 3 * * ? ","FunctionName":"stats_run_daily_timer","Program":null,"MisfireInstruction":"FireOnceNow","Parameters":[]}]'
Set-MachineValue "$EngineKey\Core" "SchedulerEntries" $jobs

# No card reader (VM, Sandbox): the engine's Device Service simulator, otherwise the rental application goes to
# maintenance mode ("Credit Card Reader Communications Error"). Values as in tests\clients\engine-registry.ps1.
if ($cfg.Hardware.SimulateCardReader) {
    $sim = "$EngineKey\Remote Services\Device Service Simulator"
    foreach ($kv in @{ UseSimulatedCardReader = "True"; IsCardReaderTampered = "False"; IsConnectedToDeviceService = "True"; IsCardReaderConnected = "True" }.GetEnumerator()) {
        Set-MachineValue $sim $kv.Key $kv.Value
    }
    # Its keyboard test cards; without the file a key press on the start view ends the engine (docs/engine-known-issues.md).
    $f = Join-Path $Reds "Kiosk Engine\data\KioskEngineSimulatedCardReaderData.json"
    if (-not (Test-Path $f)) { Set-Content -Path $f -Value '{"CurrentCard":null,"StoredCardData":{}}' -Encoding UTF8 }
}
