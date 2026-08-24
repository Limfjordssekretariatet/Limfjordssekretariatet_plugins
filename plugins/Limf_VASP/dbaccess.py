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


def datafil_har_rigtige_koter():
    """True hvis datafilen er bygget efter kote-rettelsen.

    Indtil da stod datum-korrektionen DNNADDENT i kote-feltet, så alle
    længdeprofiler kom ind med koter omkring nul. Den rettede eksport har
    et selvstændigt dnnaddent-felt; findes det, er koten en rigtig højde.
    """
    try:
        layer = _open_layer("terrain_points")
    except VaspDbError:
        return True   # ingen datafil endnu; så er der intet at advare om
    return layer.fields().indexFromName("dnnaddent") >= 0


def list_profiles():
    """Returnér profil-datalag (profiles) der har geokodede terrænpunkter.

    Hver post: dict med lgdid, navn, projektid, prjnavn, vlbnavn,
    koordsysid, punkter. Sorteret efter vandløb og navn, som de øvrige
    valglister.
    """
    layer = _open_layer("profiles")
    felter = layer.fields()
    har_navne = (felter.indexFromName("vlbnavn") >= 0
                 and felter.indexFromName("prjnavn") >= 0)
    profiles = []
    for feat in layer.getFeatures():
        gid = feat["geocodegdsid"]
        profiles.append({
            "lgdid": feat["lgdid"],
            "navn": feat["navn"] or "(uden navn)",
            "projektid": feat["projektid"],
            # Tomme i datafiler bygget før navnene kom med i eksporten.
            "prjnavn": (feat["prjnavn"] or "") if har_navne else "",
            "vlbnavn": (feat["vlbnavn"] or "") if har_navne else "",
            "koordsysid": feat["koordsysid"],
            "punkter": feat["punkter"],
            # Den VANDLØBGIS-linje profilen er geokodet på (kan være None).
            "geocodegdsid": None if gid in (None, "") else int(gid),
        })
    profiles.sort(key=lambda p: ((p["vlbnavn"] or "").lower(),
                                 (p["navn"] or "").lower()))
    return profiles


def read_profile_points(lgdid):
    """Hent terrænpunkterne for ét profil-datalag (lgdid) fra terrain_points.

    Hver post: dict med tvpid, station, x, y, kote samt punktets eget
    indhold fra VASP — punkttype, bemærkning, plottekst og bygværk.
    Sorteret efter station.
    """
    layer = _open_layer("terrain_points")
    felter = layer.fields()
    # Tomme i datafiler bygget før teksterne kom med i eksporten.
    har_tekst = felter.indexFromName("plottekst") >= 0
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
            "punkttype": (feat["punkttype"] or "") if har_tekst else "",
            "bemaerkning": (feat["bemaerkning"] or "") if har_tekst else "",
            "plottekst": (feat["plottekst"] or "") if har_tekst else "",
            "bygvaerk": (feat["bygvaerk"] or "") if har_tekst else "",
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
    multi (0/1), navn, projektid, prjnavn, vlbnavn, koordsysid, stmin,
    stmax. Sorteret efter vandløb, projekt og navn.
    """
    layer = _open_layer("vsp_calcs")
    har_vlb = layer.fields().indexFromName("vlbnavn") >= 0
    calcs = []
    for feat in layer.getFeatures():
        calcs.append({
            "berid": feat["berid"],
            "multi": bool(feat["multi"]),
            "navn": feat["navn"] or "(uden navn)",
            "projektid": feat["projektid"],
            "prjnavn": feat["prjnavn"] or "",
            # Tom i datafiler bygget før vandløbet kom med i eksporten.
            "vlbnavn": (feat["vlbnavn"] or "") if har_vlb else "",
            "koordsysid": feat["koordsysid"],
            "stmin": feat["stmin"],
            "stmax": feat["stmax"],
        })
    calcs.sort(key=lambda c: ((c["vlbnavn"] or "").lower(),
                              (c["prjnavn"] or "").lower(),
                              (c["navn"] or "").lower()))
    return calcs


# --- Tværprofiler (til nedbrænding af vandløb i terrænmodellen) -----------

# TVPDATAEXT.TVPTYPEKODE, jf. TVPBASISTYPER i VASP.
TVP_OPMAALT = 0        # "Tværprofil" — opmålte punkter (PKTDATA-BLOB)
TVP_SIMPEL = 4         # "Simpel geometri" — bundkote/bundbredde/anlæg
TVP_SAMMENSAT = 5      # "Sammensat geometri" — med afsats


def list_tvp_profiles():
    """Returnér de profil-datalag der har tværprofiler, til GUI-valg.

    Hver post: dict med lgdid, navn, vlbnavn, projektid, koordsysid,
    geocodegdsid, antal_tvp (opmålte tværsnit), antal_param (parametriske).
    Sorteret efter vandløb og profilnavn.
    """
    layer = _open_layer("tvp_profiles")
    profiles = []
    for feat in layer.getFeatures():
        gid = feat["geocodegdsid"]
        profiles.append({
            "lgdid": feat["lgdid"],
            "navn": feat["navn"] or "(uden navn)",
            "vlbnavn": feat["vlbnavn"] or "",
            "projektid": feat["projektid"],
            "koordsysid": feat["koordsysid"],
            "geocodegdsid": None if gid in (None, "") else int(gid),
            "antal_tvp": feat["antal_tvp"] or 0,
            "antal_param": feat["antal_param"] or 0,
        })
    profiles.sort(key=lambda p: ((p["vlbnavn"] or "").lower(),
                                 (p["navn"] or "").lower()))
    return profiles


def tvp_profile(lgdid):
    """Slå ét enkelt tværprofil-datalag op på lgdid. None hvis det ikke findes."""
    for prof in list_tvp_profiles():
        if prof["lgdid"] == int(lgdid):
            return prof
    return None


def read_cross_sections(lgdid):
    """Hent de opmålte tværprofiler for ét profil-datalag.

    Punkterne samles pr. tværsnit (tvpid). Hver post: dict med tvpid,
    station, x, y og punkter = liste af (afstand, kote) i profilets egen
    rækkefølge. Sorteret efter station.
    """
    layer = _open_layer("cross_sections")
    expr = '"lgdid" = %d' % int(lgdid)
    request = QgsFeatureRequest().setFilterExpression(expr)
    sektioner = {}
    for feat in layer.getFeatures(request):
        tvpid = feat["tvpid"]
        sekt = sektioner.get(tvpid)
        if sekt is None:
            sekt = {
                "tvpid": tvpid,
                "station": feat["station"],
                "x": feat["x"],
                "y": feat["y"],
                "punkter": [],
            }
            sektioner[tvpid] = sekt
        sekt["punkter"].append(
            (feat["seq"], feat["afstand"], feat["kote"]))

    ud = []
    for sekt in sektioner.values():
        # Behold profilets egen rækkefølge (seq) og smid punkter uden
        # brugbare tal væk.
        sekt["punkter"].sort(key=lambda t: (t[0] is None, t[0]))
        # Enkelte punkter i VASP's PKTDATA rummer sentinel-/skraldværdier.
        # De filtreres i eksporten, men gentages her, så et gammelt datafil-
        # udtræk ikke kan sende koter på 1e306 videre i beregningen.
        sekt["punkter"] = [
            (a, k) for _, a, k in sekt["punkter"]
            if a is not None and k is not None
            and -1e4 < a < 1e4 and -50.0 <= k <= 300.0]
        if len(sekt["punkter"]) >= 2:
            ud.append(sekt)
    ud.sort(key=lambda s: (s["station"] is None, s["station"]))
    return ud


def read_cross_section_params(lgdid):
    """Hent de parametriske tværprofiler (simpel/sammensat) for ét datalag.

    Hver post: dict med tvpid, station, x, y, typekode og p0…p7.
    Sorteret efter station.
    """
    layer = _open_layer("cross_section_params")
    expr = '"lgdid" = %d' % int(lgdid)
    request = QgsFeatureRequest().setFilterExpression(expr)
    rows = []
    for feat in layer.getFeatures(request):
        row = {
            "tvpid": feat["tvpid"],
            "station": feat["station"],
            "x": feat["x"],
            "y": feat["y"],
            "typekode": feat["typekode"],
        }
        for i in range(8):
            row["p%d" % i] = feat["p%d" % i]
        rows.append(row)
    rows.sort(key=lambda r: (r["station"] is None, r["station"]))
    return rows


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
