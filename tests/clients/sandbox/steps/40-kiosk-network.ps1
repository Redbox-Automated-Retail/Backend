# Point the sandboxed kiosk at the backend the way a real kiosk is reprovisioned (software/src/Tools/Reprovision):
#  1. baseline: the machine settings a real kiosk has (engine-registry.ps1, from a real kiosk's export), with the
#     production broker URL (:443) and a placeholder store ID, as a kiosk is before it is reprovisioned;
#  2. rb-reprovision apply: hosts entries for Redbox's host names -> this PC (relay.py on the Default Switch address),
#     the backend's CA, store ID, engine and client URLs. `rb-reprovision revert` undoes step 2 exactly.
param([string] $HostIp = ((Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1).NextHop))
"backend host (default gateway): $HostIp"
& C:\rb\clients\engine-registry.ps1 -Apply -KioskId 1 `
    -BrokerUrl "gtcp://blt.accessredbox.net:443/RegistrationBroker/Default.rem" `
    -KioskServicesUrl "https://bl2.accessredbox.net/KioskServices/MessageService.rem" `
    -Manifest C:\rb\control\engine-registry-added.json
& C:\rb\tools\reprovision\rb-reprovision.exe apply C:\rb\control\kiosk-profile.json --address $HostIp
"rb-reprovision exit $LASTEXITCODE"
try { "https check: " + (Invoke-WebRequest -UseBasicParsing https://dataservice.kiosk.prod.redbox.com/_health -TimeoutSec 10).Content }
catch { "https check failed: $_" }
try { "https check (Kiosk Services name): " + (Invoke-WebRequest -UseBasicParsing https://bl2.accessredbox.net/_health -TimeoutSec 10).Content }
catch { "https check failed: $_" }
