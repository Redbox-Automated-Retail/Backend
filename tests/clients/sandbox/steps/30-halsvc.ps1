# The real HAL service (halsvc-win32.exe, the kiosk image's configured install) as a Windows service, as its
# register-service.bat creates it; it opens COM1/COM3 (the emulator) and listens on rcp://127.0.0.1:7001.
$exe = "C:\Program Files\Redbox\halservice\bin\halsvc-win32.exe"
# HAL targets .NET 2.0 (v2.0.50727). Windows Sandbox has no .NET 3.5 and cannot enable it (NetFx3 is
# "DisabledWithPayloadRemoved"; DISM fails with 0x80072EE6 even with the host's WinSxS as source), so the config of
# this scratch copy also allows the v4 runtime. The HAL binaries are unchanged; the original is kept as .orig.
$cfg = "$exe.config"
if (-not (Test-Path "$cfg.orig")) { Copy-Item $cfg "$cfg.orig" }
$text = Get-Content -Raw $cfg
if ($text -notmatch 'supportedRuntime version="v4.0"') {
    Set-Content -Path $cfg -Value ($text -replace '<supportedRuntime version="v2.0.50727"/>', '<supportedRuntime version="v2.0.50727"/><supportedRuntime version="v4.0"/>') -NoNewline
    "allowed the v4 runtime in $cfg"
}
if (-not (Get-Service 'halsvc$default' -ErrorAction SilentlyContinue)) {
    sc.exe create 'halsvc$default' binPath= "`"$exe`"" depend= tcpip DisplayName= "Redbox Hardware Abstraction Layer (HAL)" start= demand
}
sc.exe start 'halsvc$default'
for ($i = 0; $i -lt 60; $i++) {
    if (Get-NetTCPConnection -LocalPort 7001 -State Listen -ErrorAction SilentlyContinue) { break }
    Start-Sleep 2
}
Get-Service 'halsvc$default' | Format-Table -AutoSize Status, Name | Out-String
"port 7001 listening: " + [bool](Get-NetTCPConnection -LocalPort 7001 -State Listen -ErrorAction SilentlyContinue)
Get-ChildItem "C:\Program Files\Redbox\KioskLogs" -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 8 |
    Format-Table -AutoSize LastWriteTime, Length, FullName | Out-String -Width 300
"--- emulator log tail"
Get-Content "C:\Program Files\Redbox\KioskLogs\hw-emu.log" -Tail 15
