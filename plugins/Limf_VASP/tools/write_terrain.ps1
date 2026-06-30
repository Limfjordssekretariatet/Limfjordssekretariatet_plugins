# Skriver et terræn-datalag tilbage til VASP Access-databasen (32-bit).
#
# Opretter én ny LGDPROFHEADER (kopieret fra kildeprofilen, med nyt navn +
# GLOBID) og indsætter terrænpunkterne i TVPDATAEXT. Alt i én transaktion;
# ved fejl rulles tilbage. Tager backup af .mdb første gang (medmindre den
# allerede findes).
#
# Køres med 32-bit PowerShell (kun 32-bit ACE/Access-driver findes).
#
# Parametre:
#   -Mdb         sti til VASP .mdb
#   -SourceLgdid kildeprofilens LGDID (felter kopieres herfra)
#   -Navn        navn til den nye profil (fx '<kilde>_terrænVenstre')
#   -PointsTsv   TSV med kolonner: station, x, y, kote
#   -BackupDir   mappe til backup (backup tages hvis ingen findes for denne mdb)
#
# Output (stdout): linjen "NEW_LGDID=<id>" ved succes, ellers "ERROR: ...".

param(
  [Parameter(Mandatory=$true)][string]$Mdb,
  [Parameter(Mandatory=$true)][int]$SourceLgdid,
  [Parameter(Mandatory=$true)][string]$Navn,
  [Parameter(Mandatory=$true)][string]$PointsTsv,
  [Parameter(Mandatory=$true)][string]$BackupDir
)
$ErrorActionPreference = "Stop"

# --- Backup første gang (én pr. database-fil) ------------------------------
if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null }
$mdbName = [System.IO.Path]::GetFileNameWithoutExtension($Mdb)
$existing = Get-ChildItem $BackupDir -Filter "$mdbName*.mdb" -ErrorAction SilentlyContinue
if (-not $existing) {
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $backup = Join-Path $BackupDir "$mdbName`_backup_$stamp.mdb"
  Copy-Item $Mdb $backup
  Write-Host "BACKUP=$backup"
}

# --- Læs punkterne ---------------------------------------------------------
$points = Import-Csv $PointsTsv -Delimiter "`t"
if ($points.Count -eq 0) { Write-Host "ERROR: ingen punkter i $PointsTsv"; exit 1 }

$conn = New-Object System.Data.OleDb.OleDbConnection "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$Mdb;"
$conn.Open()
$tx = $conn.BeginTransaction()
try {
  $cmd = $conn.CreateCommand(); $cmd.Transaction = $tx

  # 1) Ny LGDPROFHEADER: kopiér kildeprofilens felter, nyt navn + GLOBID.
  $cmd.CommandText = @"
INSERT INTO LGDPROFHEADER (PROJEKTID, LØBENR, NAVN, KOORDSYSID, KOTESYSID,
  STATIONERINGSRETNING, KILDEART, LGDTYPEID, LGDSAMHÆNGID, EDITLOCK,
  GEOCODETYPE, GEOCODEGDSID, GEOCODESTATUS, GLOBID, CREINIT, CREDATE,
  STMIN, STMAX)
SELECT PROJEKTID, LØBENR, ?, KOORDSYSID, KOTESYSID,
  STATIONERINGSRETNING, KILDEART, LGDTYPEID, LGDSAMHÆNGID, EDITLOCK,
  GEOCODETYPE, GEOCODEGDSID, GEOCODESTATUS, ?, 'VSP', NOW(),
  STMIN, STMAX
FROM LGDPROFHEADER WHERE LGDID = ?
"@
  $newGuid = [Guid]::NewGuid().ToString()
  [void]$cmd.Parameters.AddWithValue("navn", $Navn)
  [void]$cmd.Parameters.AddWithValue("globid", $newGuid)
  [void]$cmd.Parameters.AddWithValue("src", $SourceLgdid)
  $cmd.ExecuteNonQuery() | Out-Null

  $cmd.Parameters.Clear()
  $cmd.CommandText = "SELECT @@IDENTITY"
  $newLgdid = [int]$cmd.ExecuteScalar()

  # 2) Terrænpunkter i TVPDATAEXT (mønster fra kildens mellempunkter).
  $ins = $conn.CreateCommand(); $ins.Transaction = $tx
  # Bundkoten ligger i PARAM1, i centimeter (VASP-konvention). DHM-koterne er
  # allerede i DVR90, så DNNADDENT (DNN->DVR90-addend) sættes til 0.
  $ins.CommandText = @"
INSERT INTO TVPDATAEXT (LGDID, STATION, ISTAT, KOORDX, KOORDY, DNNADDENT,
  PARAM1, TVPTYPEKODE, TVPSFKODE, ERKOTERET, FIXPUNKTID, SIDEKODE, PLOTTEKST,
  BEMÆRKNING)
VALUES (?, ?, ?, ?, ?, 0, ?, 1, 1, 1, 1, 0, 'TERRÆN', 'Terrain')
"@
  # Parametre i samme rækkefølge som ?-pladsholderne ovenfor.
  [void]$ins.Parameters.Add("lgdid",   [System.Data.OleDb.OleDbType]::Integer)
  [void]$ins.Parameters.Add("station", [System.Data.OleDb.OleDbType]::Double)
  [void]$ins.Parameters.Add("istat",   [System.Data.OleDb.OleDbType]::Double)
  [void]$ins.Parameters.Add("x",       [System.Data.OleDb.OleDbType]::Double)
  [void]$ins.Parameters.Add("y",       [System.Data.OleDb.OleDbType]::Double)
  [void]$ins.Parameters.Add("param1",  [System.Data.OleDb.OleDbType]::Double)

  $count = 0
  foreach ($p in $points) {
    $st = [double]($p.station -replace ',', '.')
    $ins.Parameters["lgdid"].Value   = $newLgdid
    $ins.Parameters["station"].Value = $st
    $ins.Parameters["istat"].Value   = $st
    $ins.Parameters["x"].Value       = [double]($p.x -replace ',', '.')
    $ins.Parameters["y"].Value       = [double]($p.y -replace ',', '.')
    # Bundkote i cm = DVR90-kote (m) * 100.
    $ins.Parameters["param1"].Value  = [double]($p.kote -replace ',', '.') * 100
    $ins.ExecuteNonQuery() | Out-Null
    $count++
  }

  $tx.Commit()
  Write-Host "NEW_LGDID=$newLgdid"
  Write-Host "POINTS=$count"
} catch {
  $tx.Rollback()
  Write-Host "ERROR: $($_.Exception.Message)"
  $conn.Close()
  exit 1
}
$conn.Close()
