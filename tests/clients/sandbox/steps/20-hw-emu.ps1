# The hardware HAL drives (control boards on COM1, Arcus motion controller on COM3), emulated by
# unredbox/HW-Emu-Python v2 on the other ends of the com0com pairs (COM10, COM13).
$log = "C:\Program Files\Redbox\KioskLogs\hw-emu.log"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*run_hw_emu*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped previous emulator $($_.ProcessId)" }
$p = Start-Process C:\rb\tools\python\python.exe -ArgumentList "-u", "C:\rb\clients\sandbox\run_hw_emu.py" `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden -PassThru
Start-Sleep 5
"emulator pid $($p.Id), running: $(-not $p.HasExited)"
Get-Content $log -Tail 20 -ErrorAction SilentlyContinue
Get-Content "$log.err" -Tail 20 -ErrorAction SilentlyContinue
