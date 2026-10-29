# Point every component at the self-hosted backend with the backend's reprovisioning tool
# (kiosk/reprovision, rb-reprovision.exe) and the kiosk profile generated for this kiosk
# (admin kiosk-profile <id> / dashboard "Reprovision file"). It sets the hosts entries or DNS names, the backend CA,
# the store ID and market, the engine's endpoint registry values, updatemgrd's Update Service URL and credential
# keys, and the Update Client / Kiosk Client / Device Service settings; see kiosk/reprovision/README.md.
# The original installer did none of this: a Redbox kiosk used Redbox's host names as shipped.
param($Config)
$cfg = $Config
$b = $cfg.Backend
if (-not $b.ProfileFile) { Write-Log "   no Backend.ProfileFile: the kiosk keeps the URLs its packages shipped with" -Level Warn; return }
if (-not (Test-Path $b.ProfileFile)) { throw "kiosk profile not found: $($b.ProfileFile)" }
if (-not (Test-Path $b.Reprovision)) { throw "rb-reprovision.exe not found: $($b.Reprovision) (build it with kiosk\reprovision\build.ps1)" }

$profileId = (Get-Content -Raw $b.ProfileFile | ConvertFrom-Json).KioskId
if ($profileId -and "$profileId" -ne "$($cfg.StoreId)") { throw "the profile is for kiosk $profileId, the settings for $($cfg.StoreId)" }

$args_ = @("apply", $b.ProfileFile, "--no-restart")      # services are started by step 90 / the reboot
if ($b.Address) { $args_ += @("--address", $b.Address) }
Invoke-Program $b.Reprovision $args_ | Out-Null
Write-Log "   undo with: `"$($b.Reprovision)`" revert"
