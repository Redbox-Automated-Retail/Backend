<#
.SYNOPSIS
  Run a script inside the running Windows Sandbox (through loop.ps1) and print its output.
.EXAMPLE
  .\run-sandbox-step.ps1 -Script .\steps\10-com0com.ps1
  .\run-sandbox-step.ps1 -Command 'Get-Service halsvc*'
#>
param(
    [string] $Script,
    [string] $Command,
    [string] $ControlDir = "E:\temp\rb-sandbox\control",
    [int] $TimeoutSeconds = 600
)
$name = "{0:yyyyMMdd-HHmmss-fff}-{1}" -f (Get-Date), $(if ($Script) { [IO.Path]::GetFileNameWithoutExtension($Script) } else { "cmd" })
$inbox = Join-Path $ControlDir "inbox"
New-Item -ItemType Directory -Force $inbox | Out-Null
$tmp = Join-Path $ControlDir "$name.tmp"
if ($Script) { Copy-Item $Script $tmp } else { Set-Content -Path $tmp -Value $Command -Encoding UTF8 }
Move-Item $tmp (Join-Path $inbox "$name.ps1")   # appear complete, not half-written
$out = Join-Path $ControlDir "outbox\$name.log"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if ((Test-Path $out) -and (Select-String -Path $out -Pattern '^--- done' -Quiet)) { Get-Content $out; exit 0 }
    Start-Sleep -Milliseconds 500
}
"timed out after $TimeoutSeconds s waiting for $out (is the sandbox running loop.ps1?)"
exit 1
