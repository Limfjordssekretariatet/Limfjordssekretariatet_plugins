"""Tilbageskrivning af terræn-datalag til VASP Access-databasen.

Skriver de forskudte terrænpunkter (med DHM-kote) tilbage som en ny
LGDPROFHEADER + TVPDATAEXT-punkter. Selve skrivningen sker via et 32-bit
PowerShell-script (tools/write_terrain.ps1), fordi QGIS' Python er 64-bit og
kun 32-bit Access-driveren findes — samme grund som ved eksporten.

Skriver mod DEFAULT_DB_PATH (dummy-kopien). Tag en kopi til en rigtig
produktionsdatabase, når skrivningen er afprøvet.
"""

import os
import csv
import tempfile
import subprocess

from . import config


class WritebackError(Exception):
    """Rejses ved fejl under tilbageskrivning, med en dansk besked."""


def _har_ace(powershell):
    """True hvis den PowerShell-udgave har Access-driveren registreret."""
    kommando = ("(New-Object System.Data.OleDb.OleDbEnumerator).GetElements()"
                " | Select-Object -ExpandProperty SOURCES_NAME")
    try:
        svar = subprocess.run(
            [powershell, "-NoProfile", "-Command", kommando],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return "Microsoft.ACE.OLEDB" in (svar.stdout or "")


def _find_powershell():
    """Find den powershell.exe der har Access-driveren (ACE).

    Driveren findes kun i én arkitektur ad gangen, og hvilken afhænger af,
    om Office er 32- eller 64-bit. Derfor spørges begge udgaver i stedet for
    at antage 32-bit. På 64-bit Windows er SysWOW64 den 32-bit udgave og
    System32 den 64-bit — omvendt af hvad navnene antyder.
    """
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    kandidater = []
    if windir:
        for undermappe in ("System32", "SysWOW64"):
            sti = os.path.join(windir, undermappe, "WindowsPowerShell",
                               "v1.0", "powershell.exe")
            if os.path.exists(sti):
                kandidater.append(sti)
    from shutil import which
    fundet = which("powershell.exe") or which("powershell")
    if fundet and fundet not in kandidater:
        kandidater.append(fundet)

    for sti in kandidater:
        if _har_ace(sti):
            return sti
    # Ingen af dem har driveren. Returnér den første, så kaldet fejler med
    # PowerShells egen besked i stedet for at gætte forkert i tavshed.
    return kandidater[0] if kandidater else None


def terrain_layer_name(source_navn, side_left):
    """Byg navnet til det nye terræn-datalag.

    '<kildens navn>_terrænVenstre' eller '..._terrænHøjre'.
    """
    suffix = "_terrænVenstre" if side_left else "_terrænHøjre"
    return "%s%s" % (source_navn, suffix)


def write_terrain(source_lgdid, navn, points, db_path=None):
    """Skriv et terræn-datalag tilbage til VASP-databasen.

    source_lgdid: kildeprofilens LGDID (felter kopieres herfra).
    navn:         navn til den nye profil.
    points:       liste af dicts med 'station', 'x', 'y' og 'z' (DHM-kote).
                  Punkter uden 'z' springes over.

    Returnerer den nye LGDID ved succes. Rejser WritebackError ved fejl.
    """
    db_path = db_path or config.db_path()

    rows = [p for p in points if p.get("z") is not None]
    if not rows:
        raise WritebackError("Ingen punkter med terrænkote at skrive.")

    script = os.path.join(config.PLUGIN_DIR, "tools", "write_terrain.ps1")
    if not os.path.exists(script):
        raise WritebackError("Mangler skrive-scriptet:\n%s" % script)

    powershell = _find_powershell()
    if powershell is None:
        raise WritebackError("Kunne ikke finde powershell.exe.")

    # Skriv punkterne til en midlertidig TSV (kote skrives som 'kote').
    fd, tsv = tempfile.mkstemp(suffix=".tsv", prefix="vasp_wb_")
    os.close(fd)
    try:
        with open(tsv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["station", "x", "y", "kote"])
            for p in rows:
                w.writerow([
                    repr(p["station"]), repr(p["x"]),
                    repr(p["y"]), repr(p["z"])])

        result = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", script,
             "-Mdb", db_path,
             "-SourceLgdid", str(int(source_lgdid)),
             "-Navn", navn,
             "-PointsTsv", tsv,
             "-BackupDir", config.BACKUP_DIR],
            capture_output=True,  # som bytes; PowerShells konsol-output er
            creationflags=getattr(  # ikke altid gyldig UTF-8.
                subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        raise WritebackError("Kunne ikke starte skrivningen:\n%s" % exc)
    finally:
        try:
            os.remove(tsv)
        except OSError:
            pass

    def _dec(b):
        return (b or b"").decode("utf-8", "replace")

    out = _dec(result.stdout) + "\n" + _dec(result.stderr)
    if result.returncode != 0 or "NEW_LGDID=" not in out:
        # Find en ERROR-linje hvis der er en.
        msg = out.strip()
        for line in out.splitlines():
            if line.startswith("ERROR:"):
                msg = line
                break
        raise WritebackError("Tilbageskrivning fejlede:\n%s" % msg[-1500:])

    for line in out.splitlines():
        if line.startswith("NEW_LGDID="):
            return int(line.split("=", 1)[1])
    return None  # bør ikke ske; "NEW_LGDID=" blev fundet ovenfor
