"""Konfiguration og konstanter for VASP-pluginnet.

Holder fælles værdier (DB-sti, CRS-mapping, feltnavne) ét sted, så
hverken databasekoden eller geometri-/UI-koden hardkoder dem.
"""

import os

# Plugin-mappen og datakilder. GeoPackagen er den datakilde pluginnet læser
# fra (eksporteret fra Access, så QGIS slipper for Access-driveren).
PLUGIN_DIR = os.path.dirname(__file__)
DEFAULT_DB_PATH = os.path.join(PLUGIN_DIR, "VASPdatabase_Dummykopi.mdb")
DEFAULT_GPKG_PATH = os.path.join(PLUGIN_DIR, "vasp_data.gpkg")
# Mappe til database-backup før tilbageskrivning (én backup pr. session).
BACKUP_DIR = os.path.join(PLUGIN_DIR, "backups")

# Nøgle i QgsSettings hvor den valgte VASP-database huskes mellem sessioner.
_DB_SETTING_KEY = "VASP/database_path"


def db_path():
    """Returnér den valgte VASP-database (huskes mellem sessioner).

    Default er dummy-kopien første gang; derefter altid den sidst valgte.
    """
    from qgis.core import QgsSettings
    saved = QgsSettings().value(_DB_SETTING_KEY, "")
    if saved and os.path.exists(saved):
        return saved
    return DEFAULT_DB_PATH


def set_db_path(path):
    """Gem den valgte VASP-database, så den huskes mellem sessioner."""
    from qgis.core import QgsSettings
    QgsSettings().setValue(_DB_SETTING_KEY, path)


# Vandspejlsberegninger gemmes som .ber-filer under en PRJDATA-mappe. Hvor den
# ligger, fortæller databasen selv via DBINI.BINPATH (mappen der indeholder
# PRJDATA). Falder tilbage til 'PRJDATA ved siden af .mdb-filen', hvis BINPATH
# ikke er sat/tilgængelig. Brugeren skal kun vælge databasen.
def prjdata_path():
    """Returnér PRJDATA-mappen (PRJ<id>-undermapper med .ber-filer).

    1) DBINI.BINPATH fra databasen + 'PRJDATA' (den autoritative kilde).
    2) Fallback: 'PRJDATA' i samme mappe som .mdb-filen.
    """
    try:
        from . import dbaccess
        binpath = dbaccess.dbini_binpath()
    except Exception:
        binpath = None
    if binpath:
        return os.path.join(binpath, "PRJDATA")
    return os.path.join(os.path.dirname(db_path()), "PRJDATA")


def ber_path(projektid, ber_id, multi=False):
    """Byg stien til en .ber-fil: PRJDATA\\PRJ<projektid>\\BER\\[MUL]BER<id>.ber."""
    navn = ("MULBER%d.ber" % ber_id) if multi else ("BER%d.ber" % ber_id)
    return os.path.join(prjdata_path(), "PRJ%d" % projektid, "BER", navn)

# Oversættelse fra VASP's koordinatsystem-id (KOORDSYSKODER.DDHKOORDSYSID /
# LGDPROFHEADER.KOORDSYSID) til EPSG-kode.
#   0 = Ikke oplyst, 1 = UTM32 (ED50), 2 = UTM32 (EUREF89)
KOORDSYS_TO_EPSG = {
    1: 23032,   # UTM Zone 32 (ED50)
    2: 25832,   # UTM Zone 32 (EUREF89/ETRS89)
}
# Bruges når KOORDSYSID er 0/ukendt. EUREF89 er det almindelige i nyere data.
FALLBACK_EPSG = 25832

# --- DHM (Danmarks Højdemodel) via WCS fra Dataforsyningen ----------------
# Terrænmodellen (dhm_terraen) leveres i EPSG:25832, 0,4 m opløsning, GTiff.
DHM_WCS_BASE = "https://api.dataforsyningen.dk/dhm_wcs_DAF"
DHM_WCS_TOKEN = "beb3a5dbb9713dd74c1ef7c24e819faa"
DHM_COVERAGE = "dhm_terraen"   # terrænmodel (bar jord); dhm_overflade = DSM
DHM_RESOLUTION = 0.4           # meter pr. pixel (WCS native)
DHM_EPSG = 25832

# WCS'en afviser for store udtræk (over ~4 mio. pixels) og rate-limiter mange
# hurtige kald. Vi henter derfor DHM i mindre fliser (tiles) langs forløbet.
# 1024 px ved 0,4 m = 409,6 m pr. flise, ~1 mio. pixels — godt under grænsen.
DHM_TILE_PIXELS = 1024
DHM_TILE_PAUSE = 0.3           # sekunders pause mellem flise-kald (rate-limit)

# Genforsøg ved forbigående serverfejl (504/502/503, timeout). Ventetiden
# fordobles for hvert forsøg (1 s, 2 s, 4 s …).
DHM_MAX_RETRIES = 4
DHM_RETRY_BASE_DELAY = 1.0     # sekunder; ganges med 2^forsøg
DHM_HTTP_TIMEOUT = 120         # sekunder pr. HTTP-kald

# Vinkelret forskydning af stationeringspunkter (meter). VaspExcel bruger ±10.
OFFSET_DISTANCE = 10.0
