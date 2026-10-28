<#
.SYNOPSIS
  Run the 2014 kiosk engine's real WCF Ticket Services proxy against the self-hosted Ticket Controller Service.
.DESCRIPTION
  Loads Redbox.DVD.TicketServices.dll (from a Feb/Mar 2014 kiosk-engine update-store seed; it is x86, so the
  script re-runs itself in 32-bit Windows PowerShell 5.1) and calls every IControllerService operation through
  the DLL's internal ControllerServiceClient with the binding its TicketServiceProxy builds (basicHttpBinding).
  Checks that each reply and declared fault deserializes, then imports the server's ?wsdl with WsdlImporter.
.PARAMETER Dll
  Path to Redbox.DVD.TicketServices.dll.
.PARAMETER Url
  Ticket Controller Service URL (plain HTTP port of rb-cloudapi by default).
.PARAMETER ProductionId
  Production to buy from (70001 = seeded sample concert, schema/52_tickets.sql). Buys 2 tickets per run.
.EXAMPLE
  .\run-ticketservices-harness.ps1 -Dll C:\tmp\tickets\Redbox.DVD.TicketServices.dll
#>
param(
    [Parameter(Mandatory)] [string] $Dll,
    [string] $Url = "http://localhost:80/TicketControllerService/ControllerService.svc",
    [int] $Kiosk = 59394,
    [int] $ProductionId = 70001,
    [string] $Email = "ticket-harness@example.test"
)
$ErrorActionPreference = "Stop"
$Dll = (Resolve-Path $Dll).Path
if ([Environment]::Is64BitProcess) {
    $ps32 = Join-Path $env:WINDIR "SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
    & $ps32 -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -Dll $Dll -Url $Url -Kiosk $Kiosk `
        -ProductionId $ProductionId -Email $Email
    exit $LASTEXITCODE
}
Add-Type -Path (Join-Path $PSScriptRoot "TicketServicesHarness.cs") `
    -ReferencedAssemblies "System.ServiceModel", "System.Runtime.Serialization", "System.Core", "System.Xml"
$code = [TicketServicesHarness]::Run($Dll, $Url, $Kiosk, $ProductionId, $Email)
exit $code
