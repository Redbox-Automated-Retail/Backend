<#
.SYNOPSIS
  Put together everything Install-Kiosk.ps1 needs for one kiosk in one folder (copy it to a USB stick or share).
.DESCRIPTION
  <Out>\
    installer\            this folder (Install-Kiosk.ps1, steps, lib, lua)
    rb-reprovision\       rb-reprovision.exe + .config, built from kiosk\reprovision (build.ps1)
    KioskShell\           KioskShell build output (Software: src/Tools/KioskShell) or a kioskshell.zip
    prerequisites\        .NET Core / ASP.NET Core 2.2.1 x86, downloaded and SHA-256 checked
    data.zip              the update-store seed (copied, or left where it is with -LinkDataZip)
    kiosk-<id>.json       the kiosk profile from the backend (admin kiosk-profile)
    settings.json         Install-Kiosk settings pointing at all of the above
  The backend must be running for the profile (rb.ps1 up, or docker compose; or pass -ProfileFile).
.EXAMPLE
  .\New-InstallerPayload.ps1 -Out E:\kiosk-12345 -StoreId 12345 -DataZip D:\seeds\data-572024.zip `
      -Address 10.0.0.5 -KioskShell ..\..\software\src\Tools\KioskShell\bin\Release\net472
#>
param(
    [Parameter(Mandatory)] [string] $Out,
    [Parameter(Mandatory)] [int] $StoreId,
    [Parameter(Mandatory)] [string] $DataZip,
    [string] $Address,                 # the backend's address as the kiosk sees it (hosts entries)
    [string] $BaseDomain,              # or: DNS names <service>.<domain> instead of hosts entries
    [string] $ProfileFile,             # an existing kiosk profile instead of asking the backend
    [Parameter(Mandatory)] [string] $KioskShell,
    [string] $Wallpaper,
    [ValidateSet("R630", "R504")] [string] $KioskType = "R630",
    [string] $Reprovision = (Join-Path $PSScriptRoot "..\..\kiosk\reprovision"),   # the reprovisioning tool's source folder
    [switch] $LinkDataZip,             # reference -DataZip by its path instead of copying it
    [switch] $TestMode,
    [ValidateSet("wslc", "compose")] [string] $Runtime = "wslc"
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$backend = Resolve-Path (Join-Path $here "..\..")
New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path
function Step($m) { Write-Host "== $m" }

Step "installer"
$dst = Join-Path $Out "installer"
Remove-Item $dst -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $dst | Out-Null
foreach ($d in "Install-Kiosk.ps1", "lib", "steps", "lua", "tests", "README.md", "settings.example.json") { Copy-Item (Join-Path $here $d) $dst -Recurse }

Step "rb-reprovision (kiosk\reprovision\build.ps1)"
$rp = Join-Path $Reprovision "build.ps1"
if (-not (Test-Path $rp)) { throw "$rp not found: this checkout has no reprovisioning tool" }
& $rp -Out (Join-Path $Out "rb-reprovision") | Write-Host

Step "KioskShell"
$ks = Join-Path $Out "KioskShell"
Remove-Item $ks -Recurse -Force -ErrorAction SilentlyContinue
if ($KioskShell -like "*.zip") { New-Item -ItemType Directory $ks | Out-Null; Expand-Archive $KioskShell $ks }
else { Copy-Item $KioskShell $ks -Recurse }
if (-not (Test-Path (Join-Path $ks "KioskShell.exe"))) { throw "no KioskShell.exe in $KioskShell" }
Get-ChildItem $ks -Recurse -Include *.pdb | Remove-Item

Step "prerequisites (pinned)"
. (Join-Path $here "lib\common.ps1")
$defaults = (Get-DefaultSettings).Prerequisites
$pre = Join-Path $Out "prerequisites"; New-Item -ItemType Directory -Force $pre | Out-Null
foreach ($k in "DotNetCoreRuntime", "AspNetCoreRuntime") {
    $spec = $defaults[$k]; $f = Join-Path $pre (Split-Path $spec.Url -Leaf)
    if (-not (Test-Path $f) -or (Get-FileHash $f -Algorithm SHA256).Hash -ne $spec.Sha256) { & curl.exe -sSL -o $f $spec.Url; if ($LASTEXITCODE) { throw "download failed: $($spec.Url)" } }
    if ((Get-FileHash $f -Algorithm SHA256).Hash -ne $spec.Sha256) { throw "$f does not match its pinned SHA-256" }
    "  $(Split-Path $f -Leaf)"
}

Step "data zip"
$zipRef = $DataZip
if (-not $LinkDataZip) { Copy-Item $DataZip (Join-Path $Out "data.zip") -Force; $zipRef = "data.zip" }

Step "kiosk profile"
$prof = Join-Path $Out "kiosk-$StoreId.json"
if ($ProfileFile) { Copy-Item $ProfileFile $prof -Force }
else {
    $a = @("admin", "kiosk-profile", "$StoreId")
    if ($Address) { $a += @("--address", $Address) }
    if ($BaseDomain) { $a += @("--base-domain", $BaseDomain) }
    # Through cmd so the JSON is written as is (a PowerShell 5.1 redirect would re-encode it as UTF-16).
    if ($Runtime -eq "wslc") { cmd /c "`"C:\Program Files\WSL\wslc.exe`" exec rb-updatesvc python -m redbox_backend $($a -join ' ') > `"$prof`" 2>nul" }
    else { cmd /c "docker compose -f `"$backend\compose.yaml`" exec -T rb-iot python -m redbox_backend $($a -join ' ') > `"$prof`" 2>nul" }
    if ($LASTEXITCODE -or -not (Get-Content -Raw $prof | ConvertFrom-Json).KioskId) { throw "admin kiosk-profile failed (is the backend running?)" }
}

Step "settings.json"
$settings = [ordered]@{
    '$comment' = "Install-Kiosk.ps1 settings; see installer\README.md"
    StoreId = $StoreId
    DataZip = $zipRef
    KioskType = $KioskType
    TestMode = [bool]$TestMode
    Backend = [ordered]@{ ProfileFile = "kiosk-$StoreId.json"; Reprovision = "rb-reprovision\rb-reprovision.exe" }
    Prerequisites = [ordered]@{
        DotNetCoreRuntime = @{ Path = "prerequisites\dotnet-runtime-2.2.1-win-x86.exe" }
        AspNetCoreRuntime = @{ Path = "prerequisites\aspnetcore-runtime-2.2.1-win-x86.exe" }
    }
    Shell = [ordered]@{ KioskShell = "KioskShell" }
}
if ($Wallpaper) { Copy-Item $Wallpaper (Join-Path $Out "background.bmp") -Force; $settings.Shell.Wallpaper = "background.bmp" }
$settings | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $Out "settings.json")
"payload ready: $Out"
"on the kiosk (elevated): powershell -ExecutionPolicy Bypass -File installer\Install-Kiosk.ps1 -Settings settings.json"
