# Runs inside Windows Sandbox (as its administrator, from the .wsb LogonCommand). It executes every script dropped into
# C:\rb\control\inbox (mapped from the host), writes its output to C:\rb\control\outbox\<name>.log and moves it to
# C:\rb\control\done, so the host can drive the sandbox step by step. C:\rb\control\stop ends the loop.
$control = "C:\rb\control"
foreach ($d in "inbox", "outbox", "done") { New-Item -ItemType Directory -Force (Join-Path $control $d) | Out-Null }
Set-ExecutionPolicy -Scope Process Bypass -Force
"loop started $(Get-Date -Format o) as $(whoami)" | Set-Content (Join-Path $control "outbox\loop.log")
while (-not (Test-Path (Join-Path $control "stop"))) {
    foreach ($s in Get-ChildItem (Join-Path $control "inbox") -Filter *.ps1 | Sort-Object Name) {
        $out = Join-Path $control ("outbox\" + $s.BaseName + ".log")
        # Streamed line by line, so the host sees progress (and where a step hangs, e.g. on a dialog).
        try { & $s.FullName *>&1 | ForEach-Object { ($_ | Out-String -Width 400).TrimEnd() | Add-Content $out } }
        catch { "ERROR: $_" | Add-Content $out }
        "--- done $(Get-Date -Format o)" | Add-Content $out
        Move-Item $s.FullName (Join-Path $control "done") -Force
    }
    Start-Sleep -Milliseconds 500
}
