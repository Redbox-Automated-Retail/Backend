<#
.SYNOPSIS
  Do what Redbox's RIX setup host does for its script, from an unpacked copy: for comparison runs in Windows Sandbox,
  where the host itself cannot run (it needs the .NET 2.0 runtime: on .NET 4 its JScript/VSA page engine fails and it
  stops with "[I001] Unable to initialize setup"; .NET 3.5 cannot be enabled in the sandbox).
.DESCRIPTION
  -Unpacked holds script.xml (the decoded PREREQ/SCRIPT resource) and every embedded file under the file name of its
  source-path (Software repo: tools/installer/dump_rix.py makes both). In install order, as RIX does
  (software/docs/systems/store-installer.md section 3.3):
    order 2   every <file copy="true">: copy to destination-path (external ones from -SetupDir), unzip when
              explode-on-copy;
    order 96+ every <execute-process> with wait-for-exit, its ${...} properties expanded.
  The pages are skipped; their only side effect is MaxSlots (written here as the R630 default would).
#>
param(
    [Parameter(Mandatory)] [string] $Unpacked,
    [Parameter(Mandatory)] [string] $SetupDir,       # where data.zip is (RIX: next to setup.exe)
    [string] $Log = "C:\rb\control\original\rix-unpacked.log"
)
$ErrorActionPreference = "Stop"
function L($m) { "$(Get-Date -Format T) $m" | Add-Content $Log; $m }
[xml]$x = Get-Content -Raw (Join-Path $Unpacked "script.xml")
$props = @{}
foreach ($p in $x.rix.property) { $props[$p.name] = $p.value }
function Expand([string] $s) {
    if ($null -eq $s) { return $null }
    for ($i = 0; $i -lt 5; $i++) { $s = [regex]::Replace($s, '\$[\{\(]([^\}\)]+)[\}\)]', { param($m) if ($props.ContainsKey($m.Groups[1].Value)) { $props[$m.Groups[1].Value] } else { $m.Value } }) }
    return $s
}
Add-Type -AssemblyName System.IO.Compression.FileSystem

foreach ($f in $x.rix.file) {
    if ($f.copy -eq "false") { continue }
    $leaf = Split-Path (Expand $f.'source-path') -Leaf
    $src = if ($f.external -eq "true") { Join-Path $SetupDir $leaf } else { Join-Path $Unpacked $leaf }
    $dir = Expand $f.'destination-path'
    New-Item -ItemType Directory -Force $dir | Out-Null
    $dst = Join-Path $dir $leaf
    Copy-Item $src $dst -Force
    L "copy $leaf -> $dir"
    if ($f.'explode-on-copy' -eq "true") {
        $to = if ($f.'explode-target-path') { Expand $f.'explode-target-path' } else { $dir }
        $z = [IO.Compression.ZipFile]::OpenRead($dst)
        try { foreach ($e in $z.Entries) { $t = Join-Path $to $e.FullName; if ($e.Name) { New-Item -ItemType Directory -Force (Split-Path $t) | Out-Null; [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $t, $true) } } }
        finally { $z.Dispose() }
        L "   unzipped into $to"
        if ($f.'delete-after-explode' -eq "true") { Remove-Item $dst }
    }
}
# The kiosk-type page (R630 is preselected).
& reg.exe add "HKLM\SOFTWARE\Redbox\REDS\Kiosk Engine\Store" /v MaxSlots /t REG_SZ /d 90 /f | Out-Null
foreach ($e in $x.rix.'execute-process' | Sort-Object { [int]$_.'install-order' }) {
    $exe = Expand $e.filename; $args_ = Expand $e.arguments; $wd = (Expand $e.'working-dir') -replace '\\\\', '\'
    L "run [$($e.'install-order')] $exe $args_"
    $p = Start-Process $exe -ArgumentList $args_ -WorkingDirectory $wd -PassThru
    if ($e.'wait-for-exit' -eq "true") { $p.WaitForExit(); L "   exit $($p.ExitCode)" }
}
L "done"
