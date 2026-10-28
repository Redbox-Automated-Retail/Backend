# The sandbox's LogonCommand: run every setup step (steps\NN-*.ps1, in order) into C:\rb\control\outbox\provision.log,
# then keep serving C:\rb\control\inbox (loop.ps1). Every sandbox starts from a clean Windows, so this is the whole
# setup; the host's sandbox.ps1 status shows the log.
Set-ExecutionPolicy -Scope Process Bypass -Force
$out = "C:\rb\control\outbox"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out "provision.log"
"provision started $(Get-Date -Format o)" | Set-Content $log
foreach ($step in Get-ChildItem C:\rb\clients\sandbox\steps -Filter *.ps1 | Sort-Object Name) {
    "=== $($step.Name) $(Get-Date -Format T)" | Add-Content $log
    try { & $step.FullName *>&1 | ForEach-Object { ($_ | Out-String -Width 300).TrimEnd() | Add-Content $log } }
    catch { "ERROR in $($step.Name): $_" | Add-Content $log }
}
"=== provision complete $(Get-Date -Format o)" | Add-Content $log
& C:\rb\clients\sandbox\loop.ps1
