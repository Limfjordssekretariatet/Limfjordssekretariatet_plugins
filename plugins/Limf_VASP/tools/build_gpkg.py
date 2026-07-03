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

EPSG = 25832


def _to_float(s):
    if s is None or s == "":
        return None
    return float(s.replace(",", "."))


def _to_int(s):
    if s is None or s == "":
        return None
    return int(float(s.replace(",", ".")))


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
    pt_layer = ds.CreateLayer("terrain_points", srs, ogr.wkbPoint)
    pt_layer.CreateField(ogr.FieldDefn("tvpid", ogr.OFTInteger))
    pt_layer.CreateField(ogr.FieldDefn("lgdid", ogr.OFTInteger))
    pt_layer.CreateField(ogr.FieldDefn("station", ogr.OFTReal))
    pt_layer.CreateField(ogr.FieldDefn("kote", ogr.OFTReal))
    # TVPTYPEKODE: 0=Tværprofil, 1=Mellempunkt (= forløbets bundlinje), m.fl.
    pt_layer.CreateField(ogr.FieldDefn("tvptypekode", ogr.OFTInteger))

    pt_layer.StartTransaction()
    with open(POINTS_TSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        n = 0
        for row in reader:
            x = _to_float(row["koordx"])
            y = _to_float(row["koordy"])
            if x is None or y is None:
                continue
            feat = ogr.Feature(pt_layer.GetLayerDefn())
            feat.SetField("tvpid", _to_int(row["tvpid"]))
            feat.SetField("lgdid", _to_int(row["lgdid"]))
            feat.SetField("station", _to_float(row["station"]))
            feat.SetField("kote", _to_float(row["kote"]))
            feat.SetField("tvptypekode", _to_int(row["tvptypekode"]))
            pt = ogr.Geometry(ogr.wkbPoint)
            pt.AddPoint(x, y)
            feat.SetGeometry(pt)
            pt_layer.CreateFeature(feat)
            n += 1
            if n % 50000 == 0:
                print("  ... %d punkter" % n)
    pt_layer.CommitTransaction()
    print("terrain_points: %d rækker" % n)

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

    ds = None
    print("Færdig: %s" % GPKG)


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
