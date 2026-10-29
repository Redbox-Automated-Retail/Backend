# Windows services (original installer-steps.lua: "Install HAL", "Register Update Manager", "Device Service Setup",
# "Register Kiosk Client Service", "Register Update Client Service"). None is started here.
param($Config)
$cfg = $Config

function Add-V4Runtime([string] $exe) {
    # HAL is built for the 2.0 runtime; where .NET 3.5 cannot be enabled (Windows Sandbox) let it run on 4.x.
    $conf = "$exe.config"
    if (-not (Test-Path $conf)) { Set-Content -Path $conf -Encoding UTF8 -Value '<?xml version="1.0" encoding="utf-8"?><configuration></configuration>' }
    [xml]$x = Get-Content -Raw $conf
    $root = $x.DocumentElement
    $startup = $root.SelectSingleNode("startup")
    if (-not $startup) { $startup = $x.CreateElement("startup"); $root.PrependChild($startup) | Out-Null }
    if (-not $startup.SelectSingleNode("supportedRuntime[@version='v4.0']")) {
        if (-not $startup.SelectSingleNode("supportedRuntime[@version='v2.0.50727']")) {
            $v2 = $x.CreateElement("supportedRuntime"); $v2.SetAttribute("version", "v2.0.50727"); $startup.AppendChild($v2) | Out-Null
        }
        $v4 = $x.CreateElement("supportedRuntime"); $v4.SetAttribute("version", "v4.0"); $startup.AppendChild($v4) | Out-Null
        $x.Save($conf); Write-Log "   $conf allows the v4 runtime"
    }
}

# HAL: the kop-hal-install repository put it in halservice; its package installer sets up hal.xml, the
# databases and the halsvc$default service (register-service.bat).
$halBin = Join-Path $RedboxProgramFiles "halservice\bin"
if ($cfg.Hardware.Hal -and (Test-Path "$halBin\HAL.PackageInstaller.exe")) {
    # A HAL that failed at start-up (e.g. on the default hal.xml) cannot be stopped normally; end it before replacing hal.xml.
    Stop-Service 'halsvc$default' -Force -ErrorAction SilentlyContinue
    Get-Process halsvc-win32 -ErrorAction SilentlyContinue | Stop-Process -Force
    if ($cfg.Hardware.HalConfig) { Copy-Item $cfg.Hardware.HalConfig "$halBin\hal.xml" -Force; Write-Log "   hal.xml from $($cfg.Hardware.HalConfig)" }
    elseif (-not (Test-Path "$halBin\hal.xml")) {
        # The package installer then installs HAL.xml.default, which configures 0 decks: halsvc stops at start-up
        # ("Kiosk configured for 0 decks", ArgumentOutOfRangeException in DecksManager) until the kiosk's own hal.xml is in place.
        Write-Log "   no Hardware.HalConfig: HAL gets HAL.xml.default (0 decks) and will not start until this kiosk's hal.xml is installed" -Level Warn
    }
    if ($cfg.Hardware.HalAllowV4Runtime) { Add-V4Runtime "$halBin\halsvc-win32.exe"; Add-V4Runtime "$halBin\HAL.PackageInstaller.exe" }
    Invoke-Program "$halBin\HAL.PackageInstaller.exe" -WorkingDirectory $halBin -TimeoutSeconds 300 -AllowFailure | Out-Null
    Start-Sleep 5
    Stop-Service 'halsvc$default' -Force -ErrorAction SilentlyContinue
    if (-not (Get-Service 'halsvc$default' -ErrorAction SilentlyContinue)) { throw "HAL.PackageInstaller did not register halsvc`$default" }
} elseif ($cfg.Hardware.Hal) { Write-Log "   no HAL in the store (kop-hal-install); skipped" -Level Warn }

# updatemgrd: its own --register (updatemgrd$default, automatic, LocalSystem).
$um = Join-Path $UpdateManagerBin "updatemgrd.exe"
if (-not (Get-Service 'updatemgrd$default' -ErrorAction SilentlyContinue)) {
    Invoke-Program $um @("--register") -WorkingDirectory $UpdateManagerBin | Out-Null
}
if (-not (Get-Service 'updatemgrd$default' -ErrorAction SilentlyContinue)) { throw "updatemgrd --register did not create updatemgrd`$default" }

# The .NET Core services, as the original created them with sc.exe (delayed auto start).
$svcs = @(
    @{ Name = 'device$service'; Display = "Redbox Device Service"; Exe = "DeviceService\bin\DeviceService.WebApi.exe" },
    @{ Name = 'kioskclient$service'; Display = "Redbox Kiosk Client Service"; Exe = "Kiosk Client\bin\KioskClientService.Api.exe" },
    @{ Name = 'updateclient$service'; Display = "Redbox Update Client Service"; Exe = "Update Client\bin\UpdateClientService.API.exe" }
)
foreach ($s in $svcs) {
    $exe = Join-Path $Reds $s.Exe
    if (Test-Path $exe) { Set-ServiceDefinition $s.Name $s.Display $exe } else { Write-Log "   $($s.Exe) not in the store; $($s.Name) skipped" -Level Warn }
}
# The Update Service had a 64-character file name limit; the Kiosk Client's afteractivate.lua copies this one back.
$kcs = Join-Path $Reds "Kiosk Client\bin"
if (Test-Path "$kcs\m.VisualStudio.Web.CodeGeneration.EntityFrameworkCore.dll") {
    Copy-Item "$kcs\m.VisualStudio.Web.CodeGeneration.EntityFrameworkCore.dll" "$kcs\Microsoft.VisualStudio.Web.CodeGeneration.EntityFrameworkCore.dll" -Force
}
