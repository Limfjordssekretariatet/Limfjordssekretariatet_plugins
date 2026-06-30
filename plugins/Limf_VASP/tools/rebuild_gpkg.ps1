# Samlet genopbygning af vasp_data.gpkg fra Access-databasen.
#
# Kører begge eksport-trin i den rigtige bitness:
#   Trin 1 (dump_access.ps1)  -> 32-bit PowerShell  (kun 32-bit ACE-driver findes)
#   Trin 2 (build_gpkg.py)    -> QGIS' 64-bit Python (GDAL bygger GeoPackagen)
#
# Kan dobbeltklikkes (via en .bat-genvej) eller kaldes af pluginnets
# "Opdater data"-knap. Returnerer exit-kode 0 ved succes.
#
# Brug:  powershell -ExecutionPolicy Bypass -File tools\rebuild_gpkg.ps1 [-Mdb <sti>]

param(
  [string]$Mdb = ""
)
$ErrorActionPreference = "Stop"
$toolsDir  = $PSScriptRoot
$pluginDir = Split-Path -Parent $toolsDir
if (-not $Mdb) { $Mdb = Join-Path $pluginDir "VASPdatabase_Dummykopi.mdb" }

function Find-QgisPython {
    # Find QGIS' python-qgis*.bat uanset version/sti.
    $candidates = @()
    foreach ($base in @("C:\Program Files\QGIS *", "C:\OSGeo4W", "C:\OSGeo4W64")) {
        $candidates += Get-ChildItem -Path $base -Filter "python-qgis*.bat" `
            -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
    }
    if ($candidates.Count -eq 0) {
        throw "Kunne ikke finde QGIS' python-qgis*.bat. Ret stien i rebuild_gpkg.ps1."
    }
    return $candidates[0]
}

Write-Host "=== VASP: genopbygger GeoPackage ==="

# --- Trin 1: dump fra Access (32-bit) --------------------------------------
$ps32 = "$env:WINDIR\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path $ps32)) {
    throw "32-bit PowerShell ikke fundet: $ps32"
}
Write-Host "[1/2] Dumper data fra Access (32-bit) ..."
Write-Host "      ($Mdb)"
& $ps32 -NoProfile -ExecutionPolicy Bypass -File (Join-Path $toolsDir "dump_access.ps1") -Mdb $Mdb
if ($LASTEXITCODE -ne 0) { throw "Trin 1 (dump_access.ps1) fejlede (exit $LASTEXITCODE)." }

# --- Trin 2: byg GeoPackage (QGIS 64-bit Python) ---------------------------
$qgisPy = Find-QgisPython
Write-Host "[2/2] Bygger GeoPackage med QGIS-Python ..."
Write-Host "      ($qgisPy)"
& $qgisPy (Join-Path $toolsDir "build_gpkg.py")
if ($LASTEXITCODE -ne 0) { throw "Trin 2 (build_gpkg.py) fejlede (exit $LASTEXITCODE)." }

# --- Oprydning af midlertidige TSV-filer -----------------------------------
Remove-Item (Join-Path $pluginDir "_profiles.tsv")  -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $pluginDir "_points.tsv")    -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $pluginDir "_lines.tsv")     -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $pluginDir "_gislinjer.tsv") -Force -ErrorAction SilentlyContinue

Write-Host "=== Færdig. vasp_data.gpkg er opdateret. ==="
