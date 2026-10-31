<#
.SYNOPSIS
  Build Redbox's store installer (the forked RIX setup exe) for one kiosk and this backend.
.DESCRIPTION
  Everything comes from our own builds and the update-store seed, nothing from Redbox's setup exe:
    host\         the RIX setup host (software/src/Installer/host, .NET 4.8 overlay), merged into one exe
    rixc\         the RIX compiler: setup\store-installer.xml + the staged files -> setup.exe
    front end     software/src/UpdateManager/installer-front-end, merged as Redbox shipped it, plus Quartz
    lua51.dll, xdelta3.exe   rebuilt from the seed's kop-update-manager repository (SHA-1 checked)
    KioskShell    a build of software/src/Tools/KioskShell (-KioskShell), zipped as kioskshell.zip
    rb-reprovision           kiosk\reprovision\build.ps1
    .NET Core / ASP.NET Core 2.2.1 x86   Microsoft's installers, SHA-256 pinned (as Install-Kiosk.ps1)
    scripts\      the forked Lua scripts and their PowerShell helpers; installer-heading.lua is written here
  <Out> then holds what goes on the USB stick: setup.exe, data.zip, kiosk-profile.json and, with -HalConfig, hal.xml.
  Run setup.exe there as an administrator (the wizard), or `setup.exe --silent` (no pages; MaxSlots then stays unset).
.EXAMPLE
  .\Build-ClassicInstaller.ps1 -Out E:\kiosk-12345 -StoreId 12345 -DataZip D:\seeds\data-572024.zip -Address 10.0.0.5 `
      -KioskShell ..\..\software\src\Tools\KioskShell\bin\Release\net472 -HalConfig D:\kiosk-12345\hal.xml
#>
param(
    [Parameter(Mandatory)] [string] $Out,
    [Parameter(Mandatory)] [int] $StoreId,
    [Parameter(Mandatory)] [string] $DataZip,
    [string] $Address,                 # the backend's address as the kiosk sees it (hosts entries)
    [string] $BaseDomain,              # or: DNS names <service>.<domain> instead of hosts entries
    [string] $ProfileFile,             # an existing kiosk profile instead of asking the backend
    [string] $KioskShell,              # KioskShell build folder or zip; default: build software/src/Tools/KioskShell
    [string] $HalConfig,               # this kiosk's hal.xml (without it HAL gets HAL.xml.default, 0 decks)
    [string] $Banner,                  # wizard images (banner 80x100, margin 164x314); plain red ones otherwise
    [string] $Margin,
    [string] $Wallpaper,               # KioskShell's C:\Redbox\background.bmp
    [string] $LockScreen,              # the lock screen backgrounddefault.jpg
    [string] $IngenicoDriver,          # IngenicoUSBDrivers_3.14_setup.exe, if you have it
    [ValidateSet("Auto", "ShellLauncher", "Winlogon", "None")] [string] $ShellMode = "Auto",
    [string] $Version = "3.2.0.0",     # ProductVersion (Redbox's last was 3.1.0.48)
    [switch] $TestMode,                # VMs, Windows Sandbox: no reboot, simulated card reader, no reader firmware, start everything at the end
    [ValidateSet("wslc", "compose")] [string] $Runtime = "wslc",
    [switch] $KeepStaging
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$backend = (Resolve-Path (Join-Path $here "..\..")).Path
$software = Join-Path $backend "software"
function Step($m) { Write-Host "== $m" }
function Find-MSBuild {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $m = & $vswhere -latest -prerelease -products * -requires Microsoft.Component.MSBuild -find "MSBuild\**\Bin\MSBuild.exe" | Select-Object -First 1
        if ($m) { return $m }
    }
    throw "MSBuild not found (Visual Studio or the Build Tools with the .NET desktop workload)"
}
function Invoke-MSBuild([string] $Project, [string[]] $Extra = @()) {
    & $msbuild $Project -restore -nologo -v:m "-p:Configuration=Release" "-p:ILRepack=true" @Extra | Write-Host
    if ($LASTEXITCODE) { throw "MSBuild failed on $Project ($LASTEXITCODE)" }
}

if (-not (Test-Path (Join-Path $software "src\Installer\host"))) { throw "software\src\Installer\host is missing: git submodule update --init software" }
New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path
$stage = Join-Path $Out ".staging"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
foreach ($d in "installers", "update-manager", "scripts") { New-Item -ItemType Directory -Force (Join-Path $stage $d) | Out-Null }
$msbuild = Find-MSBuild

Step "setup host and rixc"
# Both into one build folder (rixc references the host project; ILRepack writes the merged host to merged\).
$buildDir = Join-Path $env:LOCALAPPDATA "redbox-installer-cache\build"
Invoke-MSBuild (Join-Path $here "rixc\rixc.csproj") @("-p:OutDir=$buildDir\")
$hostExe = Join-Path $buildDir "merged\host.exe"
$rixc = Join-Path $buildDir "rixc.exe"

Step "front end (software\src\UpdateManager\installer-front-end)"
$fe = Join-Path $software "src\UpdateManager\installer-front-end"
Invoke-MSBuild (Join-Path $fe "installer-front-end.csproj")
$feOut = Join-Path $fe "bin\Release\net46"
Copy-Item (Join-Path $feOut "merged\installer-front-end.exe") (Join-Path $stage "update-manager") -Force
# What ILRepack leaves outside: Quartz (as Redbox shipped it) and its logging library.
foreach ($dll in "Quartz.dll", "Common.Logging.dll", "Common.Logging.Core.dll") {
    if (Test-Path (Join-Path $feOut $dll)) { Copy-Item (Join-Path $feOut $dll) (Join-Path $stage "update-manager") -Force }
}
Copy-Item (Join-Path $here "update-manager\*") (Join-Path $stage "update-manager") -Force

Step "lua51.dll and xdelta3.exe from the seed's kop-update-manager"
# The store layout (software/docs/update-manager.md section 7): <repo>\.revlog\<n> (JSON: Hash, Changes[]), and per
# change <hash>-<SHA1(TargetPath\TargetName)>.patch, a seed copy or an xdelta3 (VCDIFF) delta on the previous version.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$seedDir = Join-Path $stage "seed"
$z = [IO.Compression.ZipFile]::OpenRead((Resolve-Path $DataZip).Path)
try {
    foreach ($e in $z.Entries | Where-Object { $_.FullName -match '^kop-update-manager[/\\]' -and $_.Name }) {
        $t = Join-Path $seedDir ($e.FullName -replace '/', '\')
        New-Item -ItemType Directory -Force (Split-Path $t) | Out-Null
        [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $t, $true)
    }
} finally { $z.Dispose() }
$repo = Join-Path $seedDir "kop-update-manager"
if (-not (Test-Path "$repo\.revlog")) { throw "$DataZip is not a store seed with kop-update-manager" }
$sha1 = [Security.Cryptography.SHA1]::Create()
function Get-PathHash([string] $p) { -join ($sha1.ComputeHash([Text.Encoding]::ASCII.GetBytes($p)) | ForEach-Object { $_.ToString("X2") }) }
$files = Join-Path $seedDir "files"; New-Item -ItemType Directory -Force $files | Out-Null
$xdelta = $null
$revs = Get-ChildItem "$repo\.revlog" | Where-Object { $_.Name -match '^\d+$' } | Sort-Object { [int]$_.Name } | ForEach-Object { Get-Content -Raw $_.FullName | ConvertFrom-Json }
foreach ($rev in $revs) {
    foreach ($c in $rev.Changes) {
        if ($c.Composite) { continue }
        $patch = Join-Path $repo ("{0}-{1}.patch" -f $rev.Hash, (Get-PathHash (Join-Path $c.TargetPath $c.TargetName)))
        if (-not (Test-Path $patch)) { continue }
        $f = Join-Path $files $c.TargetName
        if ($c.IsSeed) { Copy-Item $patch $f -Force }
        elseif ($xdelta -and (Test-Path $f)) {
            & $xdelta -f -d -s $f $patch "$f.new"; if ($LASTEXITCODE) { throw "xdelta3 failed on $($c.TargetName)" }
            Move-Item "$f.new" $f -Force
        } else { continue }
        if ((Get-FileHash $f -Algorithm SHA1).Hash -ne $c.VersionHash) { throw "$($c.TargetName): SHA-1 mismatch at revision $($rev.Hash)" }
        if ($c.TargetName -eq "xdelta3.exe") { $xdelta = $f }
    }
}
foreach ($n in "lua51.dll", "xdelta3.exe") {
    if (-not (Test-Path (Join-Path $files $n))) { throw "the seed's kop-update-manager has no $n" }
    Copy-Item (Join-Path $files $n) (Join-Path $stage "update-manager") -Force
    "  $n"
}

Step "KioskShell"
if (-not $KioskShell) {
    $ksProj = Join-Path $software "src\Tools\KioskShell"
    & $msbuild $ksProj -restore -nologo -v:m "-p:Configuration=Release" | Write-Host
    if ($LASTEXITCODE) { throw "MSBuild failed on KioskShell" }
    $KioskShell = Get-ChildItem (Join-Path $ksProj "bin\Release") -Recurse -Filter KioskShell.exe | Select-Object -First 1 | ForEach-Object DirectoryName
}
$ksZip = Join-Path $stage "installers\kioskshell.zip"
if ($KioskShell -like "*.zip") { Copy-Item $KioskShell $ksZip }
else {
    if (-not (Test-Path (Join-Path $KioskShell "KioskShell.exe"))) { throw "no KioskShell.exe in $KioskShell" }
    $ksTmp = Join-Path $stage "kioskshell"
    Copy-Item $KioskShell $ksTmp -Recurse
    Get-ChildItem $ksTmp -Recurse -Include *.pdb | Remove-Item
    [IO.Compression.ZipFile]::CreateFromDirectory($ksTmp, $ksZip)
}

Step "rb-reprovision (kiosk\reprovision\build.ps1)"
& (Join-Path $backend "kiosk\reprovision\build.ps1") -Out (Join-Path $stage "rb") | Write-Host
Copy-Item (Join-Path $stage "rb\rb-reprovision.exe*") (Join-Path $stage "installers") -Force

Step "prerequisites (pinned)"
. (Join-Path $backend "provisioning\installer\lib\common.ps1")
$defaults = (Get-DefaultSettings).Prerequisites
$cache = Join-Path $env:LOCALAPPDATA "redbox-installer-cache"; New-Item -ItemType Directory -Force $cache | Out-Null
foreach ($k in "DotNetCoreRuntime", "AspNetCoreRuntime") {
    $spec = $defaults[$k]; $f = Join-Path $cache (Split-Path $spec.Url -Leaf)
    if (-not (Test-Path $f) -or (Get-FileHash $f -Algorithm SHA256).Hash -ne $spec.Sha256) { & curl.exe -sSL -o $f $spec.Url; if ($LASTEXITCODE) { throw "download failed: $($spec.Url)" } }
    if ((Get-FileHash $f -Algorithm SHA256).Hash -ne $spec.Sha256) { throw "$f does not match its pinned SHA-256" }
    Copy-Item $f (Join-Path $stage "installers")
    "  $(Split-Path $f -Leaf)"
}
if ($IngenicoDriver) { Copy-Item $IngenicoDriver (Join-Path $stage "installers\IngenicoUSBDrivers_3.14_setup.exe") }
if ($Wallpaper) { Copy-Item $Wallpaper (Join-Path $stage "installers\background.bmp") }
if ($LockScreen) { Copy-Item $LockScreen (Join-Path $stage "installers\backgrounddefault.jpg") }

Step "wizard images"
Add-Type -AssemblyName System.Drawing
function New-WizardImage([string] $Path, [int] $W, [int] $H, [string] $Format) {
    $bmp = New-Object Drawing.Bitmap $W, $H
    $g = [Drawing.Graphics]::FromImage($bmp)
    $brush = New-Object Drawing.Drawing2D.LinearGradientBrush((New-Object Drawing.Point 0, 0), (New-Object Drawing.Point 0, $H),
        [Drawing.Color]::FromArgb(214, 0, 28), [Drawing.Color]::FromArgb(120, 0, 16))
    $g.FillRectangle($brush, 0, 0, $W, $H)
    $g.Dispose(); $bmp.Save($Path, [Drawing.Imaging.ImageFormat]::$Format); $bmp.Dispose()
}
if ($Banner) { Copy-Item $Banner (Join-Path $stage "banner.jpg") } else { New-WizardImage (Join-Path $stage "banner.jpg") 80 100 Jpeg }
if ($Margin) { Copy-Item $Margin (Join-Path $stage "margin.bmp") } else { New-WizardImage (Join-Path $stage "margin.bmp") 164 314 Bmp }

Step "kiosk profile"
$prof = Join-Path $Out "kiosk-profile.json"
if ($ProfileFile) { Copy-Item $ProfileFile $prof -Force }
else {
    $a = @("admin", "kiosk-profile", "$StoreId")
    if ($Address) { $a += @("--address", $Address) }
    if ($BaseDomain) { $a += @("--base-domain", $BaseDomain) }
    # Through cmd so the JSON is written as is (a PowerShell 5.1 redirect would re-encode it as UTF-16).
    if ($Runtime -eq "wslc") { cmd /c "`"C:\Program Files\WSL\wslc.exe`" exec rb-updatesvc python -m redbox_backend $($a -join ' ') > `"$prof`" 2>nul" }
    else { cmd /c "docker compose -f `"$backend\compose.yaml`" exec -T rb-iot python -m redbox_backend $($a -join ' ') > `"$prof`" 2>nul" }
}
$profile_ = Get-Content -Raw $prof | ConvertFrom-Json
if (-not $profile_.KioskId) { throw "no kiosk profile (is the backend running? admin kiosk-profile failed)" }
if ("$($profile_.KioskId)" -ne "$StoreId") { throw "the profile is for kiosk $($profile_.KioskId), not $StoreId" }
# With DNS names the Update Service is not updatesvc.redbox.com: give the front end the profile's URL.
$usUrl = $profile_.UpdateManager.UpdateServiceUrl
if ($usUrl) {
    $feConf = Join-Path $stage "update-manager\installer-front-end.exe.config"
    [xml]$x = Get-Content -Raw $feConf
    ($x.configuration.applicationSettings.ChildNodes[0].setting | Where-Object { $_.name -eq "UpdateServiceUrl" }).value = "$usUrl"
    $x.Save($feConf)
    "  front end Update Service: $usUrl"
}

Step "scripts"
Copy-Item (Join-Path $here "scripts\*") (Join-Path $stage "scripts") -Force
$lua = @"
-- installer-heading.lua: written by Build-ClassicInstaller.ps1 $(Get-Date -Format s) for kiosk $StoreId
Environment = "PROD"
HalProductID = "{92E5D7C8-C00B-4416-9120-FE11C59C984C}"
HalVersion = kernel.getregistrykey( "HKEY_LOCAL_MACHINE\\SOFTWARE\\Redbox\\HAL", "Product Version" )
TestMode = $(([bool]$TestMode).ToString().ToLower())
ShellMode = "$ShellMode"
UpdateCardReaderFirmware = $((-not $TestMode).ToString().ToLower())
SimulateCardReader = $(([bool]$TestMode).ToString().ToLower())
"@
[IO.File]::WriteAllText((Join-Path $stage "scripts\installer-heading.lua"), $lua, (New-Object Text.UTF8Encoding $false))

Step "setup.exe"
[xml]$x = Get-Content -Raw (Join-Path $here "setup\store-installer.xml")
foreach ($el in @($x.rix.SelectNodes("*[@optional='true']"))) {
    $src = if ($el.LocalName -eq "file") { Join-Path $stage $el.'source-path' } else { Join-Path $stage ("installers\" + (Split-Path $el.filename -Leaf)) }
    if (Test-Path $src) { $el.RemoveAttribute("optional") } else { $x.rix.RemoveChild($el) | Out-Null; "  left out: $($el.id)" }
}
if ($TestMode) { $x.rix.SetAttribute("reboot", "Never") }
$script = Join-Path $stage "store-installer.xml"
$x.Save($script)
& $rixc $script $stage $hostExe (Join-Path $Out "setup.exe") "-D:ProductVersion=$Version" | Write-Host
if ($LASTEXITCODE) { throw "rixc failed ($LASTEXITCODE)" }

Step "data zip, hal.xml"
Copy-Item $DataZip (Join-Path $Out "data.zip") -Force
if ($HalConfig) { Copy-Item $HalConfig (Join-Path $Out "hal.xml") -Force }
elseif (Test-Path (Join-Path $Out "hal.xml")) { Remove-Item (Join-Path $Out "hal.xml") }
if (-not $KeepStaging) { Remove-Item $stage -Recurse -Force }
"installer ready: $Out (setup.exe, data.zip, kiosk-profile.json$(if ($HalConfig) { ', hal.xml' }))"
"on the kiosk, as an administrator: setup.exe"
