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

# Projekt og vandløb hentes med, så profilerne kan søges på samme måde som
# de øvrige valglister. Begge dækker alle profiler. Projektnavnet er ofte
# bare "VLBGIS" — vandløbet er det, brugeren leder efter.
$profilesSql = @"
SELECT h.LGDID, h.NAVN, h.PROJEKTID, h.KOORDSYSID, t.N, h.GEOCODEGDSID,
       p.NAVN AS PRJNAVN, v.NAVN AS VLBNAVN
FROM ((LGDPROFHEADER AS h LEFT JOIN PROJEKTER AS p ON p.PROJEKTID = h.PROJEKTID)
LEFT JOIN VANDLØB AS v ON v.VLBSYSID = p.VLBSYSID)
INNER JOIN (
    SELECT LGDID, COUNT(*) AS N FROM TVPDATAEXT
    WHERE KOORDX IS NOT NULL AND KOORDX <> 0 GROUP BY LGDID
) AS t ON t.LGDID = h.LGDID
ORDER BY h.NAVN
"@
Export-Query $profilesSql (Join-Path $OutDir "_profiles.tsv") "lgdid`tnavn`tprojektid`tkoordsysid`tpunkter`tgeocodegdsid`tprjnavn`tvlbnavn"
Write-Host "Skrev _profiles.tsv"

# Bundkoten ligger forskellige steder alt efter punkttype, så rå-felterne
# eksporteres og build_gpkg.py regner koten ud (jf. _kote_for_punkt der):
#   type 0 (tværprofil)  : laveste punkt i PKTDATA-blob'en, se _tvp_points.tsv
#   type 1/2/3 (mellem-  : PARAM1 i cm
#     punkt, rør, brønd)
#   type 4/5 (parametrisk): PARAM2 i cm (bundkote)
# DNNADDENT er en datum-korrektion (hele basen spænder −0,096 til +0,019 m)
# og lægges til — den er IKKE en kote i sig selv. Den blev tidligere
# eksporteret som kolonnen "kote", så alle længdeprofiler kom ind med ~0.
$pointsSql = @"
SELECT TVPID, LGDID, STATION, KOORDX, KOORDY, DNNADDENT, TVPTYPEKODE,
       PARAM1, PARAM2
FROM TVPDATAEXT
WHERE KOORDX IS NOT NULL AND KOORDX <> 0
ORDER BY LGDID, STATION
"@
Export-Query $pointsSql (Join-Path $OutDir "_points.tsv") "tvpid`tlgdid`tstation`tkoordx`tkoordy`tdnnaddent`ttvptypekode`tparam1`tparam2"
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

# --- Vandspejlsberegninger (headers til GUI-valg) --------------------------
# Selve punkterne ligger i .ber-filer (ikke i databasen); her dumpes kun de
# headers brugeren skal kunne søge og vælge imellem. Simpel = VSPBERHEADER,
# multi = VSPBERMULHEADER. 'multi'-kolonnen skelner dem, 'berid' er filnr.
# Vandløbet hentes med via PROJEKTER → VANDLØB, så beregningerne kan findes
# på vandløbsnavnet som de øvrige valglister. Projektnavnet er ofte noget
# andet — fx projektet "Ryddet Lavbundsprojekt" på vandløbet "Hasseris å".
$vspSql = @"
SELECT h.VSPBERID AS berid, 0 AS multi, h.NAVN AS navn, h.PROJEKTID AS projektid,
       h.KOORDSYSID AS koordsysid, h.CALCSTMIN AS stmin, h.CALCSTMAX AS stmax,
       p.NAVN AS prjnavn, v.NAVN AS vlbnavn
FROM ((VSPBERHEADER AS h LEFT JOIN PROJEKTER AS p ON p.PROJEKTID = h.PROJEKTID)
LEFT JOIN VANDLØB AS v ON v.VLBSYSID = p.VLBSYSID)
WHERE h.CALCSTATUS > 0
"@
Export-Query $vspSql (Join-Path $OutDir "_vsp_simpel.tsv") "berid`tmulti`tnavn`tprojektid`tkoordsysid`tstmin`tstmax`tprjnavn`tvlbnavn"
Write-Host "Skrev _vsp_simpel.tsv"

$mulSql = @"
SELECT m.DSID AS berid, 1 AS multi, m.NAVN AS navn, m.PRJID AS projektid,
       0 AS koordsysid, m.STMIN AS stmin, m.STMAX AS stmax, p.NAVN AS prjnavn,
       v.NAVN AS vlbnavn
FROM ((VSPBERMULHEADER AS m LEFT JOIN PROJEKTER AS p ON p.PROJEKTID = m.PRJID)
LEFT JOIN VANDLØB AS v ON v.VLBSYSID = p.VLBSYSID)
"@
Export-Query $mulSql (Join-Path $OutDir "_vsp_multi.tsv") "berid`tmulti`tnavn`tprojektid`tkoordsysid`tstmin`tstmax`tprjnavn`tvlbnavn"
Write-Host "Skrev _vsp_multi.tsv"

# --- Tværprofiler: opmålte profiler (TVPTYPEKODE 0) ------------------------
# PKTDATA-BLOB (little-endian):
#   offset 0 : int32 = 334        magic/version (ens overalt)
#   offset 4 : int32 = N          antal punkter
#   offset 8 : N * 4 * float64    [afstand_m, værdi_cm, sigteplan_cm, markør]
# Sanity check: længden er 8 + N*32.
#
# Koten er IKKE ét felt — den findes på to måder, alt efter om punktet er
# nivelleret eller indtastet som en færdig kote:
#   sigteplan sat (< 1e30)  -> værdien er en AFLÆSNING: kote = (sigteplan-værdi)/100
#   sigteplan = 1.7e308     -> sentinel for "ikke sat": værdien ER koten i cm
# I basen er ca. 2/3 af punkterne den sidste slags. Markør-feltet er en int64
# gemt i en double; 1 ser ud til at markere brinkpunkter. De rå værdier skrives
# med, så formlen kan efterprøves uden at dumpe databasen igen.
#
# DNNADDENT lægges IKKE til. Efterprøvet af brugeren mod VASP på tvpid 924513
# (station 8176, DNNADDENT = -0,058): den rigtige kote er -0,455, altså den rå
# værdi. Blev korrektionen lagt til, gav det -0,513. Den skrives fortsat med i
# sin egen kolonne, så den kan bruges, hvis et datumskifte bliver aktuelt.
#
# Punkter med urimelige tal (sentinel, NaN, vildt store) springes over — de
# findes spredt i basen og ville ellers give koter på 1e306.
$tvpCmd = $conn.CreateCommand()
$tvpCmd.CommandText = @"
SELECT TVPID, LGDID, STATION, KOORDX, KOORDY, DNNADDENT, PKTDATA
FROM TVPDATAEXT
WHERE TVPTYPEKODE = 0 AND PKTDATA IS NOT NULL
  AND KOORDX IS NOT NULL AND KOORDX <> 0
ORDER BY LGDID, STATION
"@
$r = $tvpCmd.ExecuteReader()
$sw = New-Object System.IO.StreamWriter((Join-Path $OutDir "_tvp_points.tsv"), $false, [System.Text.Encoding]::UTF8)
$sw.WriteLine("lgdid`ttvpid`tstation`tx`ty`tseq`tafstand`traavaerdi`tsigteplan`tmarkoer`tdnnaddent`tkote")
$iStation = $r.GetOrdinal("STATION")
$iDnn = $r.GetOrdinal("DNNADDENT")
$SENTINEL = 1.0e30      # sigteplan 1.7e308 = "ikke sat"
$tvpCount = 0
$ptCount = 0
$skipCount = 0
$badPts = 0

function Test-Tal([double]$v, [double]$graense) {
  return -not ([double]::IsNaN($v) -or [double]::IsInfinity($v) `
               -or [math]::Abs($v) -gt $graense)
}

while ($r.Read()) {
  $blob = [byte[]]$r["PKTDATA"]
  if ($blob.Length -lt 8) { $skipCount++; continue }
  $n = [BitConverter]::ToInt32($blob, 4)
  if ($n -le 0 -or $blob.Length -lt (8 + $n * 32)) { $skipCount++; continue }
  $lgd = $r["LGDID"]
  $tvp = $r["TVPID"]
  $st = if ($r.IsDBNull($iStation)) { "" } else { $r["STATION"] }
  $x = $r["KOORDX"]
  $y = $r["KOORDY"]
  $dnn = if ($r.IsDBNull($iDnn)) { 0.0 } else { [double]$r["DNNADDENT"] }
  $skrevet = 0
  for ($i = 0; $i -lt $n; $i++) {
    $o = 8 + $i * 32
    $afst = [BitConverter]::ToDouble($blob, $o)
    $raa = [BitConverter]::ToDouble($blob, $o + 8)
    $sig = [BitConverter]::ToDouble($blob, $o + 16)
    $mark = [BitConverter]::ToInt64($blob, $o + 24)

    # Afstand langs tværsnittet: højst nogle få hundrede meter.
    if (-not (Test-Tal $afst 10000.0)) { $badPts++; continue }
    if (-not (Test-Tal $raa 1000000.0)) { $badPts++; continue }

    if (Test-Tal $sig $SENTINEL) {
      $kote = ($sig - $raa) / 100.0            # nivelleret aflæsning
      $sigUd = $sig
    } else {
      $kote = $raa / 100.0                     # værdien er selve koten
      $sigUd = ""
    }
    # Danske koter: alt uden for dette er skrald, ikke terræn.
    if ($kote -lt -50.0 -or $kote -gt 300.0) { $badPts++; continue }

    $sw.WriteLine(("{0}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6}`t{7}`t{8}`t{9}`t{10}`t{11}" -f `
      $lgd, $tvp, $st, $x, $y, $i, $afst, $raa, $sigUd, $mark, $dnn, $kote))
    $ptCount++
    $skrevet++
  }
  if ($skrevet -ge 2) { $tvpCount++ }
}
$sw.Close(); $r.Close()
Write-Host "Skrev _tvp_points.tsv ($tvpCount tværsnit, $ptCount punkter, $badPts ugyldige punkter, $skipCount blobs sprunget over)"

# --- Tværprofiler: parametriske profiler (TVPTYPEKODE 4 og 5) --------------
# Type 4 "Simpel geometri":   PARAM2 = bundkote (cm), PARAM3 = bundbredde (m),
#                             PARAM4 = anlæg (symmetrisk).
# Type 5 "Sammensat geometri": PARAM2 = bundkote (cm), PARAM5 = afsatskote (cm),
#                             PARAM3/PARAM4 ligner anlæg/bundbredde, men
#                             PARAM0/1/6/7 er ikke afklaret. Kun 25 rækker i
#                             basen; dumpes så de kan tydes senere.
$paramSql = @"
SELECT TVPID, LGDID, STATION, KOORDX, KOORDY, DNNADDENT, TVPTYPEKODE,
       PARAM0, PARAM1, PARAM2, PARAM3, PARAM4, PARAM5, PARAM6, PARAM7
FROM TVPDATAEXT
WHERE TVPTYPEKODE = 4 OR TVPTYPEKODE = 5
ORDER BY LGDID, STATION
"@
Export-Query $paramSql (Join-Path $OutDir "_tvp_params.tsv") "tvpid`tlgdid`tstation`tx`ty`tdnnaddent`ttypekode`tp0`tp1`tp2`tp3`tp4`tp5`tp6`tp7"
Write-Host "Skrev _tvp_params.tsv"

# --- Profil-headere (til valglisten: navn + vandløb pr. LGDID) -------------
$hdrSql = @"
SELECT h.LGDID, h.NAVN, h.PROJEKTID, h.KOORDSYSID, h.GEOCODEGDSID, v.NAVN AS VLBNAVN
FROM ((LGDPROFHEADER AS h
LEFT JOIN PROJEKTER AS p ON p.PROJEKTID = h.PROJEKTID)
LEFT JOIN VANDLØB AS v ON v.VLBSYSID = p.VLBSYSID)
ORDER BY v.NAVN, h.NAVN
"@
Export-Query $hdrSql (Join-Path $OutDir "_lgd_headers.tsv") "lgdid`tnavn`tprojektid`tkoordsysid`tgeocodegdsid`tvlbnavn"
Write-Host "Skrev _lgd_headers.tsv"

# --- DBINI (konfiguration, bl.a. BINPATH hvor .ber-filerne ligger) ---------
$dbiniSql = "SELECT ASECTION, AENTRY, ARTN FROM DBINI"
Export-Query $dbiniSql (Join-Path $OutDir "_dbini.tsv") "asection`taentry`tvalue"
Write-Host "Skrev _dbini.tsv"

$conn.Close()
Write-Host "Dump færdig."
