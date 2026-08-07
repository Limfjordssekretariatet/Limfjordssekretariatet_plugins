"""Trin 2 af eksporten (64-bit / QGIS-Python): bygger GeoPackagen ud fra de
TSV-filer som dump_access.ps1 (32-bit) lavede.

Laver tre lag i vasp_data.gpkg:
  - profiles        : attribut-tabel til GUI-valg (ingen geometri)
  - terrain_points  : punkt-geometri (EPSG:25832) med station og kote
  - geocoded_lines  : punkter (X, Y, station, kote) for de VANDLØBGIS-linjer
                      profilerne er geokodet på (én linje pr. gisdataid)

Kør med QGIS' Python:
  & "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis.bat" tools\\build_gpkg.py
eller direkte med apps\\Python312\\python.exe (GDAL er på PATH i QGIS-miljøet).
"""

import os
import csv
import sys

from osgeo import ogr, osr

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GPKG = os.path.join(PLUGIN_DIR, "vasp_data.gpkg")
PROFILES_TSV = os.path.join(PLUGIN_DIR, "_profiles.tsv")
POINTS_TSV = os.path.join(PLUGIN_DIR, "_points.tsv")
LINES_TSV = os.path.join(PLUGIN_DIR, "_lines.tsv")
GISLINJER_TSV = os.path.join(PLUGIN_DIR, "_gislinjer.tsv")
VSP_SIMPEL_TSV = os.path.join(PLUGIN_DIR, "_vsp_simpel.tsv")
VSP_MULTI_TSV = os.path.join(PLUGIN_DIR, "_vsp_multi.tsv")
DBINI_TSV = os.path.join(PLUGIN_DIR, "_dbini.tsv")
TVP_POINTS_TSV = os.path.join(PLUGIN_DIR, "_tvp_points.tsv")
TVP_PARAMS_TSV = os.path.join(PLUGIN_DIR, "_tvp_params.tsv")
LGD_HEADERS_TSV = os.path.join(PLUGIN_DIR, "_lgd_headers.tsv")

EPSG = 25832


def _to_float(s):
    if s is None or s == "":
        return None
    return float(s.replace(",", "."))


def _to_int(s):
    if s is None or s == "":
        return None
    return int(float(s.replace(",", ".")))


# Danske koter. Alt udenfor er sentinelværdier (fx -99900 cm = "ikke sat")
# eller skrald, ikke en højde.
KOTE_MIN, KOTE_MAKS = -50.0, 300.0

# TVPDATAEXT.TVPTYPEKODE
TYPE_TVAERPROFIL = 0      # bundkoten er laveste punkt i PKTDATA-blob'en
TYPE_PARAM1 = (1, 2, 3)   # mellempunkt, rør, brønd: bundkoten står i PARAM1
TYPE_PARAM2 = (4, 5)      # simpel/sammensat geometri: bundkoten står i PARAM2


def _bundkoter_fra_tvaersnit():
    """Laveste kote pr. tværsnit, fra de dekodede PKTDATA-punkter.

    Tværprofilerne (type 0) har ingen bundkote i en kolonne — den ligger i
    blob'en, som dump_access.ps1 allerede har pakket ud til _tvp_points.tsv.
    """
    bund = {}
    if not os.path.exists(TVP_POINTS_TSV):
        return bund
    with open(TVP_POINTS_TSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            tvpid = _to_int(row["tvpid"])
            kote = _to_float(row["kote"])
            if tvpid is None or kote is None:
                continue
            if tvpid not in bund or kote < bund[tvpid]:
                bund[tvpid] = kote
    return bund


def _kote_for_punkt(row, bundkoter):
    """Bundkoten for ét punkt i længdeprofilet, eller None.

    Koten står forskellige steder alt efter punkttype. DNNADDENT lægges
    IKKE til: brugeren har efterprøvet mod VASP på tvpid 924513 (station
    8176, DNNADDENT = -0,058), hvor den rigtige kote er -0,455 — altså den
    rå værdi. Korrektionen gemmes i sit eget felt, men indgår ikke i koten.
    """
    typekode = _to_int(row.get("tvptypekode"))

    if typekode == TYPE_TVAERPROFIL:
        kote = bundkoter.get(_to_int(row["tvpid"]))
    else:
        felt = "param1" if typekode in TYPE_PARAM1 else (
            "param2" if typekode in TYPE_PARAM2 else None)
        cm = _to_float(row.get(felt)) if felt else None
        kote = (cm / 100.0) if cm is not None else None

    if kote is None or not (KOTE_MIN <= kote <= KOTE_MAKS):
        return None
    return kote


def main():
    if os.path.exists(GPKG):
        os.remove(GPKG)

    drv = ogr.GetDriverByName("GPKG")
    ds = drv.CreateDataSource(GPKG)

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)

    # --- profiles (attribut-tabel) ---------------------------------------
    prof_layer = ds.CreateLayer("profiles", None, ogr.wkbNone)
    prof_layer.CreateField(ogr.FieldDefn("lgdid", ogr.OFTInteger))
    prof_layer.CreateField(ogr.FieldDefn("navn", ogr.OFTString))
    prof_layer.CreateField(ogr.FieldDefn("projektid", ogr.OFTInteger))
    prof_layer.CreateField(ogr.FieldDefn("koordsysid", ogr.OFTInteger))
    prof_layer.CreateField(ogr.FieldDefn("punkter", ogr.OFTInteger))
    # Hvilken VANDLØBGIS-linje profilen er geokodet på (kan være tom).
    prof_layer.CreateField(ogr.FieldDefn("geocodegdsid", ogr.OFTInteger))

    prof_layer.StartTransaction()
    with open(PROFILES_TSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        n = 0
        for row in reader:
            feat = ogr.Feature(prof_layer.GetLayerDefn())
            feat.SetField("lgdid", _to_int(row["lgdid"]))
            feat.SetField("navn", row["navn"])
            feat.SetField("projektid", _to_int(row["projektid"]))
            feat.SetField("koordsysid", _to_int(row["koordsysid"]))
            feat.SetField("punkter", _to_int(row["punkter"]))
            gid = _to_int(row.get("geocodegdsid"))
            if gid is not None:
                feat.SetField("geocodegdsid", gid)
            prof_layer.CreateFeature(feat)
            n += 1
    prof_layer.CommitTransaction()
    print("profiles: %d rækker" % n)

    # --- terrain_points (punkt-geometri) ---------------------------------
    bundkoter = _bundkoter_fra_tvaersnit()
    pt_layer = ds.CreateLayer("terrain_points", srs, ogr.wkbPoint)
    pt_layer.CreateField(ogr.FieldDefn("tvpid", ogr.OFTInteger))
    pt_layer.CreateField(ogr.FieldDefn("lgdid", ogr.OFTInteger))
    pt_layer.CreateField(ogr.FieldDefn("station", ogr.OFTReal))
    pt_layer.CreateField(ogr.FieldDefn("kote", ogr.OFTReal))
    # Datum-korrektionen gemmes for sig, så det kan ses hvad koten består af.
    pt_layer.CreateField(ogr.FieldDefn("dnnaddent", ogr.OFTReal))
    # TVPTYPEKODE: 0=Tværprofil, 1=Mellempunkt (= forløbets bundlinje), m.fl.
    pt_layer.CreateField(ogr.FieldDefn("tvptypekode", ogr.OFTInteger))

    pt_layer.StartTransaction()
    uden_kote = 0
    with open(POINTS_TSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        n = 0
        for row in reader:
            x = _to_float(row["koordx"])
            y = _to_float(row["koordy"])
            if x is None or y is None:
                continue
            kote = _kote_for_punkt(row, bundkoter)
            if kote is None:
                uden_kote += 1
            feat = ogr.Feature(pt_layer.GetLayerDefn())
            feat.SetField("tvpid", _to_int(row["tvpid"]))
            feat.SetField("lgdid", _to_int(row["lgdid"]))
            feat.SetField("station", _to_float(row["station"]))
            if kote is not None:
                feat.SetField("kote", kote)
            feat.SetField("dnnaddent", _to_float(row["dnnaddent"]))
            feat.SetField("tvptypekode", _to_int(row["tvptypekode"]))
            pt = ogr.Geometry(ogr.wkbPoint)
            pt.AddPoint(x, y)
            feat.SetGeometry(pt)
            pt_layer.CreateFeature(feat)
            n += 1
            if n % 50000 == 0:
                print("  ... %d punkter" % n)
    pt_layer.CommitTransaction()
    print("terrain_points: %d rækker (%d uden brugbar kote)" % (n, uden_kote))

    # --- geocoded_lines (punkter for de geokodede VANDLØBGIS-linjer) ------
    ln_layer = ds.CreateLayer("geocoded_lines", srs, ogr.wkbPoint)
    ln_layer.CreateField(ogr.FieldDefn("gisdataid", ogr.OFTInteger))
    ln_layer.CreateField(ogr.FieldDefn("seq", ogr.OFTInteger))
    ln_layer.CreateField(ogr.FieldDefn("station", ogr.OFTReal))
    ln_layer.CreateField(ogr.FieldDefn("kote", ogr.OFTReal))

    if os.path.exists(LINES_TSV):
        ln_layer.StartTransaction()
        with open(LINES_TSV, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter="\t")
            n = 0
            for row in reader:
                x = _to_float(row["x"])
                y = _to_float(row["y"])
                if x is None or y is None:
                    continue
                feat = ogr.Feature(ln_layer.GetLayerDefn())
                feat.SetField("gisdataid", _to_int(row["gisdataid"]))
                feat.SetField("seq", _to_int(row["seq"]))
                feat.SetField("station", _to_float(row["station"]))
                feat.SetField("kote", _to_float(row["kote"]))
                pt = ogr.Geometry(ogr.wkbPoint)
                pt.AddPoint(x, y)
                feat.SetGeometry(pt)
                ln_layer.CreateFeature(feat)
                n += 1
                if n % 50000 == 0:
                    print("  ... %d linjepunkter" % n)
        ln_layer.CommitTransaction()
        print("geocoded_lines: %d punkter" % n)
    else:
        print("geocoded_lines: _lines.tsv mangler (springer over)")

    # --- gis_lines (vandløbslinjer som LineString-geometri) ---------------
    # Byg én LineString pr. gisdataid ud fra punkterne i _lines.tsv (seq-orden).
    _build_gis_lines(ds, srs)

    # --- gislinjer (metadata-tabel til GUI-valg) -------------------------
    _build_gislinjer_table(ds)

    # --- vsp_calcs (vandspejlsberegninger til GUI-valg) ------------------
    _build_vsp_calcs_table(ds)

    # --- dbini (databasekonfiguration, bl.a. BINPATH til .ber-filer) -----
    _build_dbini_table(ds)

    # --- tværprofiler (til nedbrænding af vandløb i terrænmodellen) ------
    antal_tvp = _build_cross_sections(ds)
    antal_param = _build_cross_section_params(ds)
    _build_tvp_profiles(ds, antal_tvp, antal_param)

    ds = None
    print("Færdig: %s" % GPKG)


def _index(ds, table, column):
    """Læg et attribut-indeks på en kolonne, så opslag pr. profil er hurtige.

    Tabellerne har over en million rækker; uden indeks bliver hvert opslag
    en fuld scanning.
    """
    try:
        ds.ExecuteSQL('CREATE INDEX "idx_%s_%s" ON "%s" ("%s")'
                      % (table, column, table, column))
    except RuntimeError as exc:
        print("  (kunne ikke oprette indeks på %s.%s: %s)"
              % (table, column, exc))


def _build_cross_sections(ds):
    """Byg cross_sections: punkterne i de opmålte tværprofiler (TVPTYPEKODE 0).

    Ét punkt pr. række. 'afstand' er afstanden langs tværsnittet som den står
    i VASP (0 i profilets venstre ende), 'kote' er beregnet af nivellementet
    i dump_access.ps1. De rå nivellementstal er med, så koten kan efterregnes.

    Returnerer {lgdid: antal tværsnit}.
    """
    if not os.path.exists(TVP_POINTS_TSV):
        print("cross_sections: _tvp_points.tsv mangler (springer over)")
        return {}

    layer = ds.CreateLayer("cross_sections", None, ogr.wkbNone)
    for fld, typ in [("lgdid", ogr.OFTInteger), ("tvpid", ogr.OFTInteger),
                     ("station", ogr.OFTReal), ("x", ogr.OFTReal),
                     ("y", ogr.OFTReal), ("seq", ogr.OFTInteger),
                     ("afstand", ogr.OFTReal), ("kote", ogr.OFTReal),
                     ("raavaerdi", ogr.OFTReal), ("sigteplan", ogr.OFTReal),
                     # Markør-feltet er et rå heltal fra BLOB'en; betydningen
                     # er ikke afklaret, og enkelte værdier fylder mere end
                     # 32 bit. Gemmes som 64-bit så de kan tydes senere.
                     ("markoer", ogr.OFTInteger64),
                     ("dnnaddent", ogr.OFTReal)]:
        layer.CreateField(ogr.FieldDefn(fld, typ))

    tvp_pr_profil = {}
    sete_tvp = set()
    layer.StartTransaction()
    n = 0
    with open(TVP_POINTS_TSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            lgdid = _to_int(row["lgdid"])
            tvpid = _to_int(row["tvpid"])
            feat = ogr.Feature(layer.GetLayerDefn())
            feat.SetField("lgdid", lgdid)
            feat.SetField("tvpid", tvpid)
            feat.SetField("station", _to_float(row["station"]))
            feat.SetField("x", _to_float(row["x"]))
            feat.SetField("y", _to_float(row["y"]))
            feat.SetField("seq", _to_int(row["seq"]))
            feat.SetField("afstand", _to_float(row["afstand"]))
            feat.SetField("kote", _to_float(row["kote"]))
            feat.SetField("raavaerdi", _to_float(row["raavaerdi"]))
            sig = _to_float(row["sigteplan"])
            if sig is not None:
                feat.SetField("sigteplan", sig)
            feat.SetField("markoer", _to_int(row["markoer"]))
            feat.SetField("dnnaddent", _to_float(row["dnnaddent"]))
            layer.CreateFeature(feat)
            n += 1
            if (lgdid, tvpid) not in sete_tvp:
                sete_tvp.add((lgdid, tvpid))
                tvp_pr_profil[lgdid] = tvp_pr_profil.get(lgdid, 0) + 1
            if n % 100000 == 0:
                print("  ... %d tværprofilpunkter" % n)
    layer.CommitTransaction()
    _index(ds, "cross_sections", "lgdid")
    print("cross_sections: %d punkter i %d tværsnit (%d profiler)"
          % (n, len(sete_tvp), len(tvp_pr_profil)))
    return tvp_pr_profil


def _build_cross_section_params(ds):
    """Byg cross_section_params: parametriske profiler (TVPTYPEKODE 4 og 5).

    Type 4 (simpel geometri) omsættes til en profilform ved kørsel; type 5
    (sammensat) gemmes, men bruges ikke endnu — se geo/mike.py.

    Returnerer {lgdid: antal rækker}.
    """
    if not os.path.exists(TVP_PARAMS_TSV):
        print("cross_section_params: _tvp_params.tsv mangler (springer over)")
        return {}

    layer = ds.CreateLayer("cross_section_params", None, ogr.wkbNone)
    for fld, typ in [("tvpid", ogr.OFTInteger), ("lgdid", ogr.OFTInteger),
                     ("station", ogr.OFTReal), ("x", ogr.OFTReal),
                     ("y", ogr.OFTReal), ("dnnaddent", ogr.OFTReal),
                     ("typekode", ogr.OFTInteger)]:
        layer.CreateField(ogr.FieldDefn(fld, typ))
    for i in range(8):
        layer.CreateField(ogr.FieldDefn("p%d" % i, ogr.OFTReal))

    pr_profil = {}
    layer.StartTransaction()
    n = 0
    with open(TVP_PARAMS_TSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            lgdid = _to_int(row["lgdid"])
            feat = ogr.Feature(layer.GetLayerDefn())
            feat.SetField("tvpid", _to_int(row["tvpid"]))
            feat.SetField("lgdid", lgdid)
            feat.SetField("station", _to_float(row["station"]))
            x = _to_float(row["x"])
            y = _to_float(row["y"])
            if x is not None:
                feat.SetField("x", x)
            if y is not None:
                feat.SetField("y", y)
            feat.SetField("dnnaddent", _to_float(row["dnnaddent"]))
            feat.SetField("typekode", _to_int(row["typekode"]))
            for i in range(8):
                val = _to_float(row["p%d" % i])
                if val is not None:
                    feat.SetField("p%d" % i, val)
            layer.CreateFeature(feat)
            n += 1
            pr_profil[lgdid] = pr_profil.get(lgdid, 0) + 1
    layer.CommitTransaction()
    _index(ds, "cross_section_params", "lgdid")
    print("cross_section_params: %d rækker (%d profiler)" % (n, len(pr_profil)))
    return pr_profil


def _build_tvp_profiles(ds, antal_tvp, antal_param):
    """Byg tvp_profiles: valglisten over profiler der HAR tværprofiler.

    Slår antallet af tværsnit sammen med profilets navn og vandløbsnavn, så
    dialogen kan vise "vandløb — profilnavn (N tværsnit)".
    """
    if not os.path.exists(LGD_HEADERS_TSV):
        print("tvp_profiles: _lgd_headers.tsv mangler (springer over)")
        return

    layer = ds.CreateLayer("tvp_profiles", None, ogr.wkbNone)
    for fld, typ in [("lgdid", ogr.OFTInteger), ("navn", ogr.OFTString),
                     ("vlbnavn", ogr.OFTString), ("projektid", ogr.OFTInteger),
                     ("koordsysid", ogr.OFTInteger),
                     ("geocodegdsid", ogr.OFTInteger),
                     ("antal_tvp", ogr.OFTInteger),
                     ("antal_param", ogr.OFTInteger)]:
        layer.CreateField(ogr.FieldDefn(fld, typ))

    layer.StartTransaction()
    n = 0
    with open(LGD_HEADERS_TSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            lgdid = _to_int(row["lgdid"])
            n_tvp = antal_tvp.get(lgdid, 0)
            n_par = antal_param.get(lgdid, 0)
            # Kun profiler der faktisk har tværsnit skal kunne vælges.
            if n_tvp == 0 and n_par == 0:
                continue
            feat = ogr.Feature(layer.GetLayerDefn())
            feat.SetField("lgdid", lgdid)
            feat.SetField("navn", row["navn"])
            feat.SetField("vlbnavn", row["vlbnavn"])
            feat.SetField("projektid", _to_int(row["projektid"]))
            feat.SetField("koordsysid", _to_int(row["koordsysid"]))
            gid = _to_int(row["geocodegdsid"])
            if gid is not None:
                feat.SetField("geocodegdsid", gid)
            feat.SetField("antal_tvp", n_tvp)
            feat.SetField("antal_param", n_par)
            layer.CreateFeature(feat)
            n += 1
    layer.CommitTransaction()
    print("tvp_profiles: %d profiler med tværsnit" % n)


def _build_dbini_table(ds):
    """Byg dbini-tabellen: databasens DBINI-konfiguration (asection/aentry/value)."""
    if not os.path.exists(DBINI_TSV):
        print("dbini: _dbini.tsv mangler (springer over)")
        return
    layer = ds.CreateLayer("dbini", None, ogr.wkbNone)
    layer.CreateField(ogr.FieldDefn("asection", ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("aentry", ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("value", ogr.OFTString))
    layer.StartTransaction()
    n = 0
    with open(DBINI_TSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            feat = ogr.Feature(layer.GetLayerDefn())
            feat.SetField("asection", row["asection"])
            feat.SetField("aentry", row["aentry"])
            feat.SetField("value", row["value"])
            layer.CreateFeature(feat)
            n += 1
    layer.CommitTransaction()
    print("dbini: %d rækker" % n)


def _build_vsp_calcs_table(ds):
    """Byg vsp_calcs-tabellen: vandspejlsberegninger (simpel + multi) til GUI."""
    layer = ds.CreateLayer("vsp_calcs", None, ogr.wkbNone)
    for fld, typ in [("berid", ogr.OFTInteger), ("multi", ogr.OFTInteger),
                     ("navn", ogr.OFTString), ("projektid", ogr.OFTInteger),
                     ("koordsysid", ogr.OFTInteger), ("stmin", ogr.OFTReal),
                     ("stmax", ogr.OFTReal), ("prjnavn", ogr.OFTString)]:
        layer.CreateField(ogr.FieldDefn(fld, typ))
    total = 0
    for tsv in (VSP_SIMPEL_TSV, VSP_MULTI_TSV):
        if not os.path.exists(tsv):
            continue
        layer.StartTransaction()
        with open(tsv, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                feat = ogr.Feature(layer.GetLayerDefn())
                feat.SetField("berid", _to_int(row["berid"]))
                feat.SetField("multi", _to_int(row["multi"]))
                feat.SetField("navn", row["navn"])
                feat.SetField("projektid", _to_int(row["projektid"]))
                feat.SetField("koordsysid", _to_int(row["koordsysid"]))
                feat.SetField("stmin", _to_float(row["stmin"]))
                feat.SetField("stmax", _to_float(row["stmax"]))
                feat.SetField("prjnavn", row["prjnavn"])
                layer.CreateFeature(feat)
                total += 1
        layer.CommitTransaction()
    print("vsp_calcs: %d beregninger" % total)


def _build_gis_lines(ds, srs):
    """Byg gis_lines-laget: én LineString pr. vandløbslinje (gisdataid)."""
    if not os.path.exists(LINES_TSV):
        print("gis_lines: _lines.tsv mangler (springer over)")
        return
    # Saml punkter pr. gisdataid i seq-rækkefølge.
    lines = {}
    with open(LINES_TSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            x = _to_float(row["x"])
            y = _to_float(row["y"])
            if x is None or y is None:
                continue
            gid = _to_int(row["gisdataid"])
            lines.setdefault(gid, []).append(
                (_to_int(row["seq"]), x, y))

    layer = ds.CreateLayer("gis_lines", srs, ogr.wkbLineString)
    layer.CreateField(ogr.FieldDefn("gisdataid", ogr.OFTInteger))
    layer.StartTransaction()
    n = 0
    for gid, pts in lines.items():
        pts.sort(key=lambda t: t[0])
        if len(pts) < 2:
            continue
        geom = ogr.Geometry(ogr.wkbLineString)
        for _, x, y in pts:
            geom.AddPoint(x, y)
        feat = ogr.Feature(layer.GetLayerDefn())
        feat.SetField("gisdataid", gid)
        feat.SetGeometry(geom)
        layer.CreateFeature(feat)
        n += 1
    layer.CommitTransaction()
    print("gis_lines: %d linjer" % n)


def _build_gislinjer_table(ds):
    """Byg gislinjer-tabellen: metadata (navn, vandløb, længde) til GUI."""
    if not os.path.exists(GISLINJER_TSV):
        print("gislinjer: _gislinjer.tsv mangler (springer over)")
        return
    layer = ds.CreateLayer("gislinjer", None, ogr.wkbNone)
    layer.CreateField(ogr.FieldDefn("gisdataid", ogr.OFTInteger))
    layer.CreateField(ogr.FieldDefn("navn", ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("laengde", ogr.OFTReal))
    layer.CreateField(ogr.FieldDefn("koordsysid", ogr.OFTInteger))
    layer.CreateField(ogr.FieldDefn("vlbnavn", ogr.OFTString))
    layer.StartTransaction()
    n = 0
    with open(GISLINJER_TSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            feat = ogr.Feature(layer.GetLayerDefn())
            feat.SetField("gisdataid", _to_int(row["gisdataid"]))
            feat.SetField("navn", row["navn"])
            feat.SetField("laengde", _to_float(row["laengde"]))
            feat.SetField("koordsysid", _to_int(row["koordsysid"]))
            feat.SetField("vlbnavn", row["vlbnavn"])
            layer.CreateFeature(feat)
            n += 1
    layer.CommitTransaction()
    print("gislinjer: %d rækker" % n)


if __name__ == "__main__":
    sys.exit(main())
