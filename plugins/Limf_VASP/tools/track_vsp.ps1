# Overvågningsværktøj: sporer hvad der ændres i databasen OG i filsystemet,
# når du gemmer/beregner et vandspejl i VASP.
#
# Sådan bruges det (32-bit PowerShell, pga. Access-driveren):
#   1) Kør med -Mode before   -> tager et FØR-snapshot (før du gemmer i VASP)
#   2) Gem/beregn vandspejlet i VASP
#   3) Kør med -Mode after    -> tager EFTER-snapshot og viser forskellen
#
# Det viser: rækketal-ændringer pr. tabel, nyeste UPDDATE/CREDATE-rækker, og
# hvilke filer der blev oprettet/ændret i de overvågede mapper.
#
# Overvågede filstier: databasens egen mappe + VASP's eksterne stier fra DBINI
# (BINPATH, VLBGISDB). Ret -WatchPaths hvis du kender andre placeringer.

param(
  [Parameter(Mandatory=$true)][ValidateSet("before","after")][string]$Mode,
  [string]$Mdb = "",
  [string]$SnapDir = "$env:TEMP\vasp_track",
  [string[]]$WatchPaths = @()
)
$ErrorActionPreference = "Stop"

# Find plugin-mappen robust (PSScriptRoot kan være tom afhængigt af kald).
$scriptDir = $PSScriptRoot
if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$pluginDir = Split-Path -Parent $scriptDir
if (-not $Mdb) { $Mdb = Join-Path $pluginDir "VASPdatabase_Dummykopi.mdb" }
if (-not (Test-Path $SnapDir)) { New-Item -ItemType Directory -Path $SnapDir -Force | Out-Null }

# --- find stier at overvåge (databasemappe + DBINI-stier) ------------------
function Get-WatchPaths($conn) {
  $paths = New-Object System.Collections.Generic.List[string]
  $paths.Add((Split-Path -Parent $Mdb))
  try {
    $c = $conn.CreateCommand()
    $c.CommandText = "SELECT AENTRY, ARTN FROM DBINI"
    $r = $c.ExecuteReader()
    while ($r.Read()) {
      $val = "$($r['ARTN'])".Trim()
      if ($val -and (Test-Path $val -ErrorAction SilentlyContinue)) { $paths.Add($val) }
    }
    $r.Close()
  } catch {}
  return $paths | Select-Object -Unique
}

# --- database-snapshot: rækketal + seneste UPDDATE/CREDATE pr. brugertabel --
# Rækketal alene fanger ikke rækker der ÆNDRES uden at nye tilføjes. Derfor
# tager vi også den seneste UPDDATE og CREDATE (hvis kolonnerne findes), så en
# beregning der opdaterer eksisterende rækker også bliver synlig.
function Get-DbSnapshot($conn) {
  $snap = @{}
  $cols = $conn.GetSchema("Columns")
  $tables = $conn.GetSchema("Tables") |
    Where-Object { $_.TABLE_TYPE -eq "TABLE" -and $_.TABLE_NAME -notmatch "^MSys" } |
    Select-Object -ExpandProperty TABLE_NAME
  foreach ($t in $tables) {
    $c = $conn.CreateCommand()
    try {
      $c.CommandText = "SELECT COUNT(*) FROM [$t]"
      $count = [int]$c.ExecuteScalar()
    } catch { $count = -1 }
    $tcols = ($cols | Where-Object { $_.TABLE_NAME -eq $t } |
      Select-Object -ExpandProperty COLUMN_NAME)
    $maxupd = ""
    foreach ($dc in @("UPDDATE", "CREDATE", "CALCDATE")) {
      if ($tcols -contains $dc) {
        try {
          $c.CommandText = "SELECT MAX([$dc]) FROM [$t]"
          $v = $c.ExecuteScalar()
          if ($v -and $v -isnot [DBNull]) { $maxupd += "$dc=$($v.ToString('s'));" }
        } catch {}
      }
    }
    $snap[$t] = "$count|$maxupd"
  }
  return $snap
}

# --- filsystem-snapshot: sti -> (størrelse, sidst ændret) ------------------
function Get-FileSnapshot($paths) {
  $snap = @{}
  foreach ($p in $paths) {
    try {
      Get-ChildItem -Path $p -Recurse -File -ErrorAction SilentlyContinue |
        ForEach-Object { $snap[$_.FullName] = "$($_.Length)|$($_.LastWriteTime.Ticks)" }
    } catch {}
  }
  return $snap
}

$conn = New-Object System.Data.OleDb.OleDbConnection "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$Mdb;"
$conn.Open()
if (-not $WatchPaths -or $WatchPaths.Count -eq 0) { $WatchPaths = Get-WatchPaths $conn }

$dbSnap = Get-DbSnapshot $conn
$conn.Close()
$fileSnap = Get-FileSnapshot $WatchPaths

$dbBeforeFile   = Join-Path $SnapDir "db_before.clixml"
$fileBeforeFile = Join-Path $SnapDir "fs_before.clixml"
$pathFile       = Join-Path $SnapDir "paths.clixml"

if ($Mode -eq "before") {
  $dbSnap   | Export-Clixml $dbBeforeFile
  $fileSnap | Export-Clixml $fileBeforeFile
  $WatchPaths | Export-Clixml $pathFile
  Write-Host "FØR-snapshot taget."
  Write-Host "Overvåger $($WatchPaths.Count) mappe(r):"
  $WatchPaths | ForEach-Object { Write-Host "  $_" }
  Write-Host ""
  Write-Host "Gem/beregn nu vandspejlet i VASP, og kør derefter:  -Mode after"
  return
}

# --- Mode after: sammenlign mod FØR-snapshotet ------------------------------
if (-not (Test-Path $dbBeforeFile)) {
  Write-Host "FEJL: intet FØR-snapshot fundet. Kør '-Mode before' først."
  return
}
$dbBefore   = Import-Clixml $dbBeforeFile
$fileBefore = Import-Clixml $fileBeforeFile

Write-Host "=== DATABASE-ÆNDRINGER (rækketal + seneste dato) ==="
$anyDb = $false
foreach ($t in ($dbSnap.Keys | Sort-Object)) {
  $b = if ($dbBefore.ContainsKey($t)) { $dbBefore[$t] } else { "0|" }
  $a = $dbSnap[$t]
  if ($a -ne $b) {
    $bc = ($b -split '\|', 2)[0]; $ac = ($a -split '\|', 2)[0]
    $bd = ($b -split '\|', 2)[1]; $ad = ($a -split '\|', 2)[1]
    Write-Host "  $t :"
    if ($bc -ne $ac) { Write-Host "      rækker: $bc -> $ac" }
    if ($bd -ne $ad) { Write-Host "      dato:   '$bd' -> '$ad'" }
    $anyDb = $true
  }
}
if (-not $anyDb) { Write-Host "  (ingen ændringer i rækketal eller datoer i nogen tabel)" }

Write-Host ""
Write-Host "=== FIL-ÆNDRINGER (oprettet/ændret) ==="
$anyFile = $false
foreach ($f in ($fileSnap.Keys | Sort-Object)) {
  if (-not $fileBefore.ContainsKey($f)) {
    Write-Host "  NY:      $f"; $anyFile = $true
  } elseif ($fileBefore[$f] -ne $fileSnap[$f]) {
    Write-Host "  ÆNDRET:  $f"; $anyFile = $true
  }
}
foreach ($f in ($fileBefore.Keys | Sort-Object)) {
  if (-not $fileSnap.ContainsKey($f)) { Write-Host "  SLETTET: $f"; $anyFile = $true }
}
if (-not $anyFile) { Write-Host "  (ingen filændringer i de overvågede mapper)" }

Write-Host ""
Write-Host "=== KONKLUSION ==="
if ($anyDb)   { Write-Host "  -> Vandspejlet PÅVIRKER databasen (se tabeller ovenfor)." }
if ($anyFile) { Write-Host "  -> Vandspejlet skriver til FILER (se stier ovenfor)." }
if (-not $anyDb -and -not $anyFile) {
  Write-Host "  -> Ingen ændring registreret i database eller overvågede mapper."
  Write-Host "     Data gemmes måske et sted der ikke overvåges — tilføj -WatchPaths."
}
