# The real Kiosk Engine (kioskengine.exe from the kop-v2-engine snapshot, with the kiosk's data folder), started in
# the sandbox's desktop session. Its log is C:\Program Files\Redbox\REDS\Kiosk Engine\logs\kioskengine.log.
$bin = "C:\Program Files\Redbox\REDS\Kiosk Engine\bin"
Get-Process kioskengine -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force; "stopped previous engine $($_.Id)" }
# The simulated card reader (UseSimulatedCardReader) loads its keyboard test cards from this file; without it its model
# is null and any key press while a card read is pending (the start view) throws in
# DeviceServiceClientHelperSimulator.ProcessKey and the engine exits. Map keys to CardData here to swipe from the keyboard.
$sim = "C:\Program Files\Redbox\REDS\Kiosk Engine\data\KioskEngineSimulatedCardReaderData.json"
if (-not (Test-Path $sim)) { Set-Content -Path $sim -Value '{"CurrentCard":null,"StoredCardData":{}}' -Encoding UTF8; "wrote $sim" }
$log = "C:\Program Files\Redbox\REDS\Kiosk Engine\logs\kioskengine.log"
if (Test-Path $log) { Move-Item $log "$log.$(Get-Date -Format yyyyMMddHHmmss)" -Force }
$p = Start-Process (Join-Path $bin "kioskengine.exe") -WorkingDirectory $bin -PassThru
Start-Sleep 20
"engine pid $($p.Id), running: $(-not $p.HasExited)"
Get-Content $log -Tail 5 -ErrorAction SilentlyContinue
