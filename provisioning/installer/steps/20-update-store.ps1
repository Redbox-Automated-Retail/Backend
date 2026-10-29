# The data zip into the update store, then every repository staged and activated by the kiosk's own code.
# Original: RIX copied data.zip to .store\.repository and unzipped it there (a copy of the zip stayed);
# installer-steps.lua "Activate Image" ran repositoryupdate / repositoryactivate for each repository.
param($Config)
$cfg = $Config
$store = Split-Path $RepositoryRoot
$dataRoot = Join-Path $store ".data"

foreach ($svc in 'updatemgrd$default') {
    if ((Get-Service $svc -ErrorAction SilentlyContinue).Status -eq "Running") { Stop-Service $svc -Force; Write-Log "   stopped $svc" }
}

# 1. Unpack. The zip's layout is the store layout: <repo>\.revlog\<n>, .active, .current, <rev>-<SHA1(path)>.patch.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [IO.Compression.ZipFile]::OpenRead($cfg.DataZip)
try { $repos = @($z.Entries | Where-Object { $_.FullName -match '^([^/\\]+)[/\\]\.revlog[/\\]0$' } | ForEach-Object { $Matches[1] }) }
finally { $z.Dispose() }
if ($repos -notcontains "kop-update-manager") { throw "$($cfg.DataZip) is not a store seed with kop-update-manager (found: $($repos -join ', '))" }
Write-Log "   $($cfg.DataZip): $($repos.Count) repositories: $($repos -join ', ')"
foreach ($r in $repos) { $d = Join-Path $RepositoryRoot $r; if (Test-Path $d) { Remove-Item $d -Recurse -Force } }
Expand-ZipTo $cfg.DataZip $RepositoryRoot
New-Item -ItemType Directory -Force $dataRoot | Out-Null

# 2. Bootstrap update-console.exe out of the kop-update-manager repository into the state folder (outside the
#    paths the repository installs, so activating kop-update-manager does not hit the running exe).
function Get-RevLogs($repo) {
    $dir = Join-Path $RepositoryRoot "$repo\.revlog"
    Get-ChildItem $dir | Where-Object { $_.Name -match '^\d+$' } | Sort-Object { [int]$_.Name } |
        ForEach-Object { Get-Content -Raw $_.FullName | ConvertFrom-Json }
}
function Get-Sha1([string] $file) { (Get-FileHash $file -Algorithm SHA1).Hash }
function Get-PathHash([string] $path) {
    $sha = [Security.Cryptography.SHA1]::Create()
    -join ($sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($path)) | ForEach-Object { $_.ToString("X2") })
}
$boot = Join-Path $cfg.StateDir "bootstrap\bin"
Remove-Item (Split-Path $boot) -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $boot | Out-Null
$xdelta = $null
$current = @{}
foreach ($rev in Get-RevLogs "kop-update-manager") {
    foreach ($c in $rev.Changes) {
        if ($c.Composite) { continue }
        $patch = Join-Path $RepositoryRoot ("kop-update-manager\{0}-{1}.patch" -f $rev.Hash, (Get-PathHash (Join-Path $c.TargetPath $c.TargetName)))
        if (-not (Test-Path $patch)) { continue }
        $out = Join-Path $boot $c.TargetName
        if ($c.IsSeed) { Copy-Item $patch $out -Force }
        else {
            if (-not $xdelta -or -not $current.ContainsKey($c.TargetName)) { continue }
            & $xdelta -f -d -s $out $patch "$out.new"; if ($LASTEXITCODE) { throw "xdelta3 failed on $($c.TargetName)" }
            Move-Item "$out.new" $out -Force
        }
        if ((Get-Sha1 $out) -ne $c.VersionHash) { throw "bootstrap: $($c.TargetName) SHA-1 mismatch at revision $($rev.Hash)" }
        $current[$c.TargetName] = $c.VersionHash
        if ($c.TargetName -eq "xdelta3.exe") { $xdelta = $out }
    }
}
$console = Join-Path $boot "update-console.exe"
if (-not (Test-Path $console)) { throw "the seed's kop-update-manager has no update-console.exe" }
Write-Log "   bootstrap: $($current.Count) files of kop-update-manager in $boot"

# Its settings: the store's absolute paths (the shipped values are relative to REDS\Update Manager\bin).
$conf = "$console.config"
[xml]$x = Get-Content -Raw $conf
$section = $x.configuration.applicationSettings.ChildNodes | Where-Object { $_.NodeType -eq "Element" } | Select-Object -First 1
foreach ($kv in @{ ManifestRoot = $RepositoryRoot; DataStoreRoot = $dataRoot }.GetEnumerator()) {
    $s = $section.setting | Where-Object { $_.name -eq $kv.Key }
    if (-not $s) {
        $s = $x.CreateElement("setting"); $s.SetAttribute("name", $kv.Key); $s.SetAttribute("serializeAs", "String")
        $s.AppendChild($x.CreateElement("value")) | Out-Null; $section.AppendChild($s) | Out-Null
    }
    $s.SelectSingleNode("value").InnerText = $kv.Value
}
$x.Save($conf)

# 3. Stage and activate everything (the kiosk's RepositoryService; xdelta3 is found in the working directory).
$lua = Join-Path $InstallerRoot "lua\activate-image.lua"
$code = Invoke-Program $console @("--store-number:$($cfg.StoreId)", "--eval:$lua") -WorkingDirectory $boot -AllowFailure -TimeoutSeconds 7200
if ($code -ne 0) { Write-Log "   update-console exited with $code; checking the result" -Level Warn }

# 4. Check: every repository active at its head.
$bad = @()
foreach ($r in $repos) {
    $head = @(Get-RevLogs $r)[-1].Hash
    $active = (Get-Content -Raw (Join-Path $RepositoryRoot "$r\.active")) | ConvertFrom-Json
    Write-Log ("   {0,-20} active {1} {2}" -f $r, $active, $(if ($active -eq $head) { "= head" } else { "!= head $head" }))
    if ($active -ne $head) { $bad += $r }
}
if ($bad) { throw "not activated: $($bad -join ', ') (see the update-console log under $(Split-Path $boot))" }
if (Test-Path (Join-Path $Reds "Update Manager\bin\updatemgrd.exe")) { Write-Log "   updatemgrd.exe installed" } else { throw "updatemgrd.exe was not installed" }
