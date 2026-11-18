# Faelles hjaelper: vaelg den Access OLE DB-provider, der findes i DENNE proces.
#
# Dot-sources af de scripts, der aabner VASP-databasen:
#   . (Join-Path $PSScriptRoot "access_provider.ps1")
#   $prov = Get-AccessProvider $Mdb
#
# Raekkefoelge:
#   1) Microsoft.ACE.OLEDB.* (nyeste version) - foelger med Office / Access
#      Database Engine, findes i 32- ELLER 64-bit alt efter installationen.
#   2) Microsoft.Jet.OLEDB.4.0 - indbygget i Windows, men KUN 32-bit og KUN
#      til .mdb (ikke .accdb). Raekker til VASP-databasen, som er en .mdb.
#
# Holdes ren ASCII, saa Windows PowerShell 5.1 laeser filen ens uanset BOM.

function Get-AccessProvider([string]$Mdb = "") {
    $kilder = @((New-Object System.Data.OleDb.OleDbEnumerator).GetElements() |
                Select-Object -ExpandProperty SOURCES_NAME)

    $ace = $kilder | Where-Object { $_ -like "Microsoft.ACE.OLEDB*" } |
           Sort-Object -Descending | Select-Object -First 1
    if ($ace) { return $ace }

    $erAccdb = $Mdb -and ([System.IO.Path]::GetExtension($Mdb) -ieq ".accdb")
    if (($kilder -contains "Microsoft.Jet.OLEDB.4.0") -and -not $erAccdb) {
        return "Microsoft.Jet.OLEDB.4.0"
    }

    $bits = if ([Environment]::Is64BitProcess) { "64-bit" } else { "32-bit" }
    throw ("Ingen Access-driver i denne $bits PowerShell (hverken " +
           "Microsoft.ACE.OLEDB eller - for .mdb - Microsoft.Jet.OLEDB.4.0).")
}
