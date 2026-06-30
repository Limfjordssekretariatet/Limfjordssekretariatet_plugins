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


def _find_powershell():
    """Find den 32-bit powershell.exe via fuld sti.

    Skrivningen bruger 32-bit ACE-driveren, så vi SKAL bruge 32-bit
    PowerShell. På 64-bit Windows ligger den i SysWOW64 (ikke System32, som
    er 64-bit). QGIS' Python er 64-bit, så uden eksplicit sti ville vi ramme
    den 64-bit PowerShell, der ikke har ACE-provideren.
    """
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        # SysWOW64 = 32-bit på 64-bit Windows.
        full = os.path.join(
            windir, "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.exists(full):
            return full
        # Fald tilbage til System32 (fx på et rent 32-bit system).
        full = os.path.join(
            windir, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.exists(full):
            return full
    from shutil import which
    return which("powershell.exe") or which("powershell")


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
