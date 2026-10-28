# Point the sandboxed kiosk at the backend as a real kiosk is pointed (backend\README.md, "Pointing a kiosk at the
# backend"): hosts entries for Redbox's host names -> this PC (through relay.py on the Default Switch address), the
# backend's CA trusted, and the Kiosk Engine machine settings (engine-registry.ps1, from a real kiosk's export).
param([string] $HostIp = ((Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1).NextHop),
      [string] $KioskId = (Get-Content C:\rb\control\store-id.txt -ErrorAction SilentlyContinue))  # the kiosk data's store ID
if (-not $KioskId) { throw "put the kiosk's store ID in the control folder's store-id.txt" }
"backend host (default gateway): $HostIp"
$names = "updatesvc.redbox.com", "proxyservice.kiosk.prod.redbox.com", "transactionservice.kiosk.prodp.redbox.com",
         "kioskinventory.kiosk.prod.redbox.com", "dataservice.kiosk.prod.redbox.com", "adserver.kiosk.prod.redbox.com",
         "reels.product.prod.redbox.com", "iotcertificateservice.kiosk.prod.redbox.com", "files.kiosk.prod.redbox.com",
         "aj5a0v5j9z9ai-ats.iot.us-east-1.amazonaws.com", "a2zxs75ezdmapj-ats.iot.us-east-1.amazonaws.com",
         "blt.accessredbox.net", "bl2.accessredbox.net"
$hosts = "$env:windir\System32\drivers\etc\hosts"
$lines = Get-Content $hosts | Where-Object { $_ -notmatch "# redbox-self-hosted$" }
$lines += $names | ForEach-Object { "$HostIp $_ # redbox-self-hosted" }
Set-Content -Path $hosts -Value $lines -Encoding ASCII
"hosts: $($names.Count) entries"
Import-Certificate -FilePath C:\rb\control\ca.cer -CertStoreLocation Cert:\LocalMachine\Root | Format-List Subject, Thumbprint | Out-String
& C:\rb\clients\engine-registry.ps1 -Apply -KioskId $KioskId `
    -BrokerUrl "gtcp://blt.accessredbox.net:7890/RegistrationBroker/Default.rem" `
    -KioskServicesUrl "https://bl2.accessredbox.net/KioskServices/MessageService.rem" `
    -Manifest C:\rb\control\engine-registry-added.json
try { "https check: " + (Invoke-WebRequest -UseBasicParsing https://dataservice.kiosk.prod.redbox.com/_health -TimeoutSec 10).Content }
catch { "https check failed: $_" }
