"""Al dataadgang for VASP-pluginnet.

Læser fra GeoPackagen (vasp_data.gpkg), der er eksporteret fra VASP
Access-databasen med tools/dump_access.ps1 + tools/build_gpkg.py.

Baggrund: QGIS' Python er 64-bit, men maskinen har kun 32-bit Access/ACE-
drivere (Office er 32-bit), så direkte pyodbc-adgang til .mdb er ikke mulig.
GeoPackagen omgår det helt og kræver ingen driver. Modulet bruger QGIS' egen
vektor-API og returnerer almindelige Python-objekter (dicts) til
processing-/UI-laget.
"""

from qgis.core import QgsVectorLayer, QgsFeatureRequest

from . import config


class VaspDbError(Exception):
    """Rejses ved data-/forbindelsesfejl, med en brugervenlig dansk besked."""


def _open_layer(table):
    """Åbn ét lag fra GeoPackagen som QgsVectorLayer."""
    uri = "%s|layername=%s" % (config.DEFAULT_GPKG_PATH, table)
    layer = QgsVectorLayer(uri, table, "ogr")
    if not layer.isValid():
        raise VaspDbError(
            "Datafilen er ikke bygget endnu.\n\n"
            "Vælg din VASP-database via knappen \"Vælg database …\" i "
            "VASP-integration-dialogen. Datafilen bygges automatisk, og "
            "handlingerne låses op."
        )
    return layer


def list_profiles():
    """Returnér profil-datalag (profiles) der har geokodede terrænpunkter.

    Hver post: dict med lgdid, navn, projektid, koordsysid, punkter.
    Sorteret efter navn.
    """
    layer = _open_layer("profiles")
    profiles = []
    for feat in layer.getFeatures():
        gid = feat["geocodegdsid"]
        profiles.append({
            "lgdid": feat["lgdid"],
            "navn": feat["navn"] or "(uden navn)",
            "projektid": feat["projektid"],
            "koordsysid": feat["koordsysid"],
            "punkter": feat["punkter"],
            # Den VANDLØBGIS-linje profilen er geokodet på (kan være None).
            "geocodegdsid": None if gid in (None, "") else int(gid),
        })
    profiles.sort(key=lambda p: (p["navn"] or "").lower())
    return profiles


def read_profile_points(lgdid):
    """Hent terrænpunkterne for ét profil-datalag (lgdid) fra terrain_points.

    Hver post: dict med tvpid, station, x, y, kote. Sorteret efter station.
    """
    layer = _open_layer("terrain_points")
    expr = '"lgdid" = %d' % int(lgdid)
    request = QgsFeatureRequest().setFilterExpression(expr)
    points = []
    for feat in layer.getFeatures(request):
        geom = feat.geometry()
        if geom.isEmpty():
            continue
        pt = geom.asPoint()
        points.append({
            "tvpid": feat["tvpid"],
            "station": feat["station"],
            "x": pt.x(),
            "y": pt.y(),
            "kote": feat["kote"],
        })
    points.sort(key=lambda p: (p["station"] is None, p["station"]))
    return points


# Mellempunkter (TVPTYPEKODE=1) udgør forløbets bundlinje langs vandløbet.
MELLEMPUNKT = 1


def read_profile_centerline(lgdid):
    """Hent forløbets bundlinje (mellempunkter) for ét profil-datalag.

    Returnerer kun punkter med TVPTYPEKODE = mellempunkt, sorteret efter
    station og uden dubletstationer (første vinder). Grundlag for
    resampling til faste stationeringspunkter. Hver post: dict med
    station, x, y, kote.
    """
    layer = _open_layer("terrain_points")
    expr = '"lgdid" = %d AND "tvptypekode" = %d' % (int(lgdid), MELLEMPUNKT)
    request = QgsFeatureRequest().setFilterExpression(expr)
    points = []
    for feat in layer.getFeatures(request):
        geom = feat.geometry()
        if geom.isEmpty():
            continue
        pt = geom.asPoint()
        points.append({
            "station": feat["station"],
            "x": pt.x(),
            "y": pt.y(),
            "kote": feat["kote"],
        })
    points = [p for p in points if p["station"] is not None]
    points.sort(key=lambda p: p["station"])
    # Fjern dubletstationer (kan ellers give nul-længde segmenter).
    deduped = []
    last = None
    for p in points:
        if last is None or p["station"] != last:
            deduped.append(p)
            last = p["station"]
    return deduped


def read_geocoded_line(gisdataid):
    """Hent punkterne for den VANDLØBGIS-linje en profil er geokodet på.

    Linjen er den geometri profilen faktisk hører til (LGDPROFHEADER.
    GEOCODEGDSID → VANDLØBGIS.GISDATAID). Den er typisk tættere end
    mellempunkterne, så resamplede stationeringspunkter følger vandløbets
    kurve. Punkterne returneres i digitaliseringsrækkefølge (seq), hver som
    dict med station, x, y, kote.
    """
    layer = _open_layer("geocoded_lines")
    expr = '"gisdataid" = %d' % int(gisdataid)
    request = QgsFeatureRequest().setFilterExpression(expr)
    points = []
    for feat in layer.getFeatures(request):
        geom = feat.geometry()
        if geom.isEmpty():
            continue
        pt = geom.asPoint()
        points.append({
            "seq": feat["seq"],
            "station": feat["station"],
            "x": pt.x(),
            "y": pt.y(),
            "kote": feat["kote"],
        })
    # Linjens egen rækkefølge er seq; station følger med fra BLOB'en.
    points.sort(key=lambda p: (p["seq"] is None, p["seq"]))
    return points


def list_gislinjer():
    """Returnér alle vandløbslinjer (VANDLØBGIS) med geometri.

    Hver post: dict med gisdataid, navn, vlbnavn (vandløbsnavn), laengde,
    koordsysid. Sorteret efter vandløb og linjenavn.
    """
    layer = _open_layer("gislinjer")
    linjer = []
    for feat in layer.getFeatures():
        linjer.append({
            "gisdataid": feat["gisdataid"],
            "navn": feat["navn"] or "(uden navn)",
            "vlbnavn": feat["vlbnavn"] or "",
            "laengde": feat["laengde"],
            "koordsysid": feat["koordsysid"],
        })
    linjer.sort(key=lambda l: ((l["vlbnavn"] or "").lower(),
                               (l["navn"] or "").lower()))
    return linjer


def read_gisline_points(gisdataid):
    """Hent en vandløbslinjes knudepunkter som ordnet liste (til LineString).

    Returnerer en liste af dicts med x, y, kote i digitaliseringsrækkefølge.
    """
    layer = _open_layer("geocoded_lines")
    expr = '"gisdataid" = %d' % int(gisdataid)
    request = QgsFeatureRequest().setFilterExpression(expr)
    points = []
    for feat in layer.getFeatures(request):
        geom = feat.geometry()
        if geom.isEmpty():
            continue
        pt = geom.asPoint()
        points.append({
            "seq": feat["seq"],
            "x": pt.x(),
            "y": pt.y(),
            "kote": feat["kote"],
        })
    points.sort(key=lambda p: (p["seq"] is None, p["seq"]))
    return points


def list_vsp_calcs():
    """Returnér vandspejlsberegninger (simpel + multi) til GUI-valg.

    Selve punkterne ligger i .ber-filer; her returneres kun de headers
    brugeren skal kunne søge og vælge imellem. Hver post: dict med berid,
    multi (0/1), navn, projektid, prjnavn, koordsysid, stmin, stmax.
    Sorteret efter projekt og navn.
    """
    layer = _open_layer("vsp_calcs")
    calcs = []
    for feat in layer.getFeatures():
        calcs.append({
            "berid": feat["berid"],
            "multi": bool(feat["multi"]),
            "navn": feat["navn"] or "(uden navn)",
            "projektid": feat["projektid"],
            "prjnavn": feat["prjnavn"] or "",
            "koordsysid": feat["koordsysid"],
            "stmin": feat["stmin"],
            "stmax": feat["stmax"],
        })
    calcs.sort(key=lambda c: ((c["prjnavn"] or "").lower(),
                              (c["navn"] or "").lower()))
    return calcs


def dbini_binpath():
    """Returnér DBINI.BINPATH (mappen hvor VASP gemmer .ber-filerne).

    Returnerer None hvis dbini-tabellen mangler eller BINPATH ikke er sat.
    """
    try:
        layer = _open_layer("dbini")
    except VaspDbError:
        return None
    for feat in layer.getFeatures():
        if (feat["aentry"] or "").strip().upper() == "BINPATH":
            val = (feat["value"] or "").strip()
            return val or None
    return None
