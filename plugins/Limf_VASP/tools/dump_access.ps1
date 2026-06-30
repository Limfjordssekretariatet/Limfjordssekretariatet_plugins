# Trin 1 af eksporten (32-bit): dumper VASP-profildata fra Access til TSV-filer.
#
# Køres med 32-bit PowerShell, fordi kun 32-bit ACE/Access-driveren findes
# (Office er 32-bit). Trin 2 (build_gpkg.py) køres bagefter med QGIS' 64-bit
# Python og samler en GeoPackage ud fra disse TSV-filer.
#
# Output i plugin-mappen:  _profiles.tsv  og  _points.tsv

param(
  [string]$Mdb = "$(Split-Path -Parent $PSScriptRoot)\VASPdatabase_Dummykopi.mdb",
  [string]$OutDir = "$(Split-Path -Parent $PSScriptRoot)"
)
$ErrorActionPreference = "Stop"

$conn = New-Object System.Data.OleDb.OleDbConnection "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$Mdb;"
$conn.Open()

function Export-Query($sql, $outFile, $header) {
  $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql
  $r = $cmd.ExecuteReader()
  $sw = New-Object System.IO.StreamWriter($outFile, $false, [System.Text.Encoding]::UTF8)
  $sw.WriteLine($header)
  $n = $r.FieldCount
  while ($r.Read()) {
    $vals = for ($i=0; $i -lt $n; $i++) {
      if ($r.IsDBNull($i)) { "" } else { ($r.GetValue($i) -replace "`t"," " -replace "`r|`n"," ") }
    }
    $sw.WriteLine($vals -join "`t")
  }
  $sw.Close(); $r.Close()
}

$profilesSql = @"
SELECT h.LGDID, h.NAVN, h.PROJEKTID, h.KOORDSYSID, t.N, h.GEOCODEGDSID
FROM LGDPROFHEADER AS h
INNER JOIN (
    SELECT LGDID, COUNT(*) AS N FROM TVPDATAEXT
    WHERE KOORDX IS NOT NULL AND KOORDX <> 0 GROUP BY LGDID
) AS t ON t.LGDID = h.LGDID
ORDER BY h.NAVN
"@
Export-Query $profilesSql (Join-Path $OutDir "_profiles.tsv") "lgdid`tnavn`tprojektid`tkoordsysid`tpunkter`tgeocodegdsid"
Write-Host "Skrev _profiles.tsv"

$pointsSql = @"
SELECT TVPID, LGDID, STATION, KOORDX, KOORDY, DNNADDENT, TVPTYPEKODE
FROM TVPDATAEXT
WHERE KOORDX IS NOT NULL AND KOORDX <> 0
ORDER BY LGDID, STATION
"@
Export-Query $pointsSql (Join-Path $OutDir "_points.tsv") "tvpid`tlgdid`tstation`tkoordx`tkoordy`tkote`ttvptypekode"
Write-Host "Skrev _points.tsv"

# --- Vandløbslinjer (VANDLØBGIS) -------------------------------------------
# Dumper punkterne (X, Y, station, kote) for ALLE linjer med geometri, så de
# kan bruges både til stationeringspunkter (profiler) og til fri import af en
# vandløbslinje. GISDATA-BLOB: int32 antal N, derefter N * 4 doubles
# (X, Y, station, kote), little-endian.
$lineCmd = $conn.CreateCommand()
$lineCmd.CommandText = "SELECT GISDATAID, GISDATA FROM VANDLØBGIS WHERE GISDATA IS NOT NULL"
$r = $lineCmd.ExecuteReader()
$sw = New-Object System.IO.StreamWriter((Join-Path $OutDir "_lines.tsv"), $false, [System.Text.Encoding]::UTF8)
$sw.WriteLine("gisdataid`tseq`tstation`tx`ty`tkote")
$lineCount = 0
while ($r.Read()) {
  $gid = $r["GISDATAID"]
  $blob = [byte[]]$r["GISDATA"]
  if ($blob.Length -lt 4) { continue }
  $n = [BitConverter]::ToInt32($blob, 0)
  # Valider at blob-længden passer med formatet (4 + N*32).
  if ($blob.Length -lt (4 + $n * 32)) { continue }
  for ($i = 0; $i -lt $n; $i++) {
    $o = 4 + $i * 32
    $x  = [BitConverter]::ToDouble($blob, $o)
    $y  = [BitConverter]::ToDouble($blob, $o + 8)
    $st = [BitConverter]::ToDouble($blob, $o + 16)
    $k  = [BitConverter]::ToDouble($blob, $o + 24)
    $sw.WriteLine(("{0}`t{1}`t{2}`t{3}`t{4}`t{5}" -f $gid, $i, $st, $x, $y, $k))
  }
  $lineCount++
}
$sw.Close(); $r.Close()
Write-Host "Skrev _lines.tsv ($lineCount linjer)"

# Liste over vandløbslinjer (navn, vandløb, længde, koordsystem) til GUI-valg.
$glCmd = $conn.CreateCommand()
$glCmd.CommandText = @"
SELECT g.GISDATAID, g.NAVN, g.DIGILÆNGDE, g.DDHKOORDSYSID, v.NAVN AS VLBNAVN
FROM VANDLØBGIS AS g LEFT JOIN VANDLØB AS v ON v.VLBSYSID = g.VLBSYSID
WHERE g.GISDATA IS NOT NULL
ORDER BY v.NAVN, g.NAVN
"@
Export-Query $glCmd.CommandText (Join-Path $OutDir "_gislinjer.tsv") "gisdataid`tnavn`tlaengde`tkoordsysid`tvlbnavn"
Write-Host "Skrev _gislinjer.tsv"

$conn.Close()
Write-Host "Dump færdig."
