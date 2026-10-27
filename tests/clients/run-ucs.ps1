<#
.SYNOPSIS
  Run the real UpdateClientService.API against the self-hosted backend on this PC (needs an elevated shell).

.DESCRIPTION
  The Update Client only connects when (a) the backend's CA is trusted by Windows (MQTTnet runs with
  AllowUntrustedCertificates=false and the certificate service is HTTPS) and (b) a kiosk id is set in
  HKLM\SOFTWARE\Redbox\REDS\Kiosk Engine\Store (WOW6432Node for the 32-bit .NET Core 2.2 runtime).
  Both need administrator rights, so this script:
    1. imports backend\certs\ca.cer into LocalMachine\Root (run `rb.ps1 export-ca` first);
    2. sets ID / Market under HKLM\SOFTWARE\WOW6432Node\Redbox\REDS\Kiosk Engine\Store if they are missing;
    3. copies the UCS build to a temp folder with an appsettings.Production.json that points at localhost;
    4. runs it in console mode for -Seconds and then prints what it did (log lines, configuration.json,
       local API answers).
  It writes C:\ProgramData\Redbox\Configuration\configuration.json and C:\ProgramData\Redbox\UpdateClient
  like a kiosk does (an existing configuration.json is backed up and restored). Use -Cleanup to remove the
  CA and the registry values it added afterwards.
.EXAMPLE
  .\run-ucs.ps1 -UcsBin ..\..\..\src\UpdateClient\UpdateClientService.API\bin\Release\netcoreapp2.2 -KioskId 990002
#>
param(
    [Parameter(Mandatory)] [string] $UcsBin,
    [long] $KioskId = 990002,
    [string] $Market = "Test Market",
    [int] $Seconds = 150,
    [string] $Dotnet = "C:\Program Files (x86)\dotnet\dotnet.exe",
    [switch] $Cleanup
)
$ErrorActionPreference = "Stop"
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this from an elevated PowerShell." }

$backend = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$caFile = Join-Path $backend "certs\ca.cer"
if (-not (Test-Path $caFile)) { throw "Missing $caFile - run scripts\rb.ps1 export-ca first." }
$ca = [Security.Cryptography.X509Certificates.X509Certificate2]::new($caFile)
$storeKey = "HKLM:\SOFTWARE\WOW6432Node\Redbox\REDS\Kiosk Engine\Store"

if ($Cleanup) {
    Get-ChildItem Cert:\LocalMachine\Root | Where-Object Thumbprint -eq $ca.Thumbprint | Remove-Item
    if ((Get-ItemProperty $storeKey -ErrorAction SilentlyContinue).AddedByRedboxTest) { Remove-Item $storeKey -Recurse }
    Write-Host "removed the test CA and registry values"
    return
}

Import-Certificate -FilePath $caFile -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
if (-not (Test-Path $storeKey)) {
    New-Item -Path $storeKey -Force | Out-Null
    New-ItemProperty $storeKey -Name AddedByRedboxTest -Value 1 | Out-Null
}
if (-not (Get-ItemProperty $storeKey -Name ID -ErrorAction SilentlyContinue)) { Set-ItemProperty $storeKey -Name ID -Value "$KioskId" }
if (-not (Get-ItemProperty $storeKey -Name Market -ErrorAction SilentlyContinue)) { Set-ItemProperty $storeKey -Name Market -Value $Market }
$KioskId = [long](Get-ItemProperty $storeKey).ID
Write-Host "kiosk id $KioskId, market $((Get-ItemProperty $storeKey).Market)"

$work = Join-Path $env:TEMP "rb-ucs"
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Resolve-Path $UcsBin) $work -Recurse
@{ AppSettings = @{ IoTBrokerEndpoint = "localhost"; IoTCertificateServiceUrl = "https://localhost";
                    IoTCertificateServiceApiKey = "selfhosted"; DefaultProxyServiceUrl = "https://localhost";
                    BaseServiceUrl = "http://localhost:6001" } } | ConvertTo-Json -Depth 4 |
    Set-Content (Join-Path $work "appsettings.Production.json") -Encoding UTF8

$cfg = "C:\ProgramData\Redbox\Configuration\configuration.json"
$backup = "$cfg.rbtest-backup"
if ((Test-Path $cfg) -and -not (Test-Path $backup)) { Copy-Item $cfg $backup }
Remove-Item "C:\ProgramData\Redbox\UpdateClient\IoT\iotcertificatedata.json" -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath $Dotnet -ArgumentList "UpdateClientService.API.dll", "--console" -WorkingDirectory $work `
    -PassThru -RedirectStandardOutput (Join-Path $work "stdout.log") -RedirectStandardError (Join-Path $work "stderr.log")
try {
    Start-Sleep -Seconds $Seconds
    foreach ($u in "api/configuration/status", "api/segments", "api/statefile", "api/iot/statisticssummary") {
        try { Write-Host "GET $u -> $((Invoke-WebRequest -UseBasicParsing "http://localhost:6001/$u").Content)" }
        catch { Write-Host "GET $u -> $($_.Exception.Message)" }
    }
    try { Write-Host "POST api/broker/register -> $((Invoke-WebRequest -UseBasicParsing -Method Post -ContentType application/json -Body "{""kioskId"":$KioskId}" -Headers @{"app-name"="KioskEngine"} http://localhost:6001/api/broker/register).StatusCode)" }
    catch { Write-Host "POST api/broker/register -> $($_.Exception.Message)" }
} finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}
$log = Get-ChildItem "C:\ProgramData\Redbox\UpdateClient\Logs" -Filter *.log | Sort-Object LastWriteTime | Select-Object -Last 1
Write-Host "---- $($log.FullName) (IoT / config lines)"
Select-String -Path $log.FullName -Pattern "ConnectAsync completed|Subscribe Topic|Publish successfully|Response|configuration|Exception" |
    Select-Object -Last 40 | ForEach-Object { $_.Line }
if (Test-Path $cfg) { Write-Host "---- configuration.json"; Get-Content $cfg -TotalCount 30 }
if (Test-Path $backup) { Move-Item $backup $cfg -Force; Write-Host "(restored the previous configuration.json)" }
