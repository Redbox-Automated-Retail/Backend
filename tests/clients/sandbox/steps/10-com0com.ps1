# Virtual serial port pairs for the hardware emulator: HAL opens COM1 (control boards) and COM3 (Arcus motion
# controller); the emulator serves the other ends, COM10 and COM13. com0com 2.2.2.0 (signed x64 build).
$ErrorActionPreference = "Continue"
$dir = "C:\Program Files (x86)\com0com"
if (-not (Test-Path "$dir\setupc.exe")) {
    $src = Get-ChildItem C:\rb\tools\com0com -Recurse -Filter setup.exe | Select-Object -First 1
    "installing $($src.FullName)"
    Start-Process $src.FullName -ArgumentList "/S" -Wait
}
if (-not (Test-Path "$dir\setupc.exe")) { $dir = "C:\Program Files\com0com" }
Push-Location $dir
& .\setupc.exe list
if (-not ((& .\setupc.exe list) -match "PortName=COM1\b")) {
    & .\setupc.exe --silent install PortName=COM1 PortName=COM10
    & .\setupc.exe --silent install PortName=COM3 PortName=COM13
}
& .\setupc.exe list
Pop-Location
"serial ports: " + ([System.IO.Ports.SerialPort]::GetPortNames() -join ",")
Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue | Format-Table -AutoSize Status, FriendlyName | Out-String
