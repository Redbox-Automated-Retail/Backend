<#
.SYNOPSIS
  Load a catalog (profile.data + inventory.data) with the real Kiosk Engine's data classes (CatalogEngineHarness.cs).
.PARAMETER BinDir
  A Kiosk Engine bin folder (Redbox.Rental.Data.dll, Redbox.ProductLookupCatalog.dll and the VistaDB.NET20.dll the
  engine ships with). Nothing is installed; the assemblies are loaded from there.
.PARAMETER DataDir
  A writable folder with profile.data and inventory.data (the engine extracts box art into ProfileImages\ there),
  e.g. where run-updatemgr-harness.ps1 activated a published dvd-data revision.
.PARAMETER Title
  Product ids to look up. -Barcode "barcode=product id" pairs to find; -Art "product id=image file" to compare with
  the box art the engine extracted.
.EXAMPLE
  & C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe -File run-catalog-engine-harness.ps1 `
      -BinDir "E:\engine\Program Files\Redbox\REDS\Kiosk Engine\bin" -DataDir E:\harness\kiosk-data `
      -Title 1000,915978 -Barcode 000123456789=915978 -Art 915978=E:\work\art.jpg
#>
param(
    [Parameter(Mandatory)] [string] $BinDir,
    [Parameter(Mandatory)] [string] $DataDir,
    [string[]] $Title = @(),
    [string[]] $Barcode = @(),
    [string[]] $Art = @(),
    # Rows per table, "ProductCatalog=11656,..."; default: counted from the file with vendor\vistadb3-py
    [string] $Expected = ""
)
$ErrorActionPreference = "Stop"
# powershell.exe -File passes "a,b" as one string
$Title = @($Title | ForEach-Object { $_ -split "," } | Where-Object { $_ })
$Barcode = @($Barcode | ForEach-Object { $_ -split "," } | Where-Object { $_ })
$Art = @($Art | ForEach-Object { $_ -split "," } | Where-Object { $_ })
if (-not $Expected) {
    $src = Join-Path (Split-Path (Split-Path $PSScriptRoot)) "vendor\vistadb3-py\src"
    $env:PYTHONPATH = $src
    $Expected = (& python -c "import sys; from vistadb3 import Database; d = Database(sys.argv[1]); print(','.join(f'{n}={d.row_count(t)}' for n, t in d.tables.items()))" (Join-Path $DataDir "profile.data")).Trim()
}
Write-Host "rows in the file: $Expected"
Set-Location $BinDir
[Environment]::CurrentDirectory = $BinDir
[AppDomain]::CurrentDomain.add_AssemblyResolve({
    param($s, $e)
    $f = Join-Path $BinDir (($e.Name -split ",")[0] + ".dll")
    if (Test-Path $f) { return [Reflection.Assembly]::LoadFrom($f) }
    return $null
})
$refs = @()
foreach ($n in "Redbox.Core", "Redbox.KioskEngine.ComponentModel", "Redbox.Rental.Model", "Redbox.Rental.Data",
               "Redbox.ProductLookupCatalog") {
    $p = Join-Path $BinDir "$n.dll"
    [void][Reflection.Assembly]::LoadFrom($p)
    $refs += $p
}
$refs += "System.Core.dll", "System.Data.dll"
Add-Type -Path (Join-Path $PSScriptRoot "EngineStubs.cs"), (Join-Path $PSScriptRoot "CatalogEngineHarness.cs") -ReferencedAssemblies $refs
try { exit [CatalogEngineHarness]::Run($DataDir, $Expected, $Title, $Barcode, $Art) } catch { Write-Host $_.Exception.ToString(); exit 2 }
