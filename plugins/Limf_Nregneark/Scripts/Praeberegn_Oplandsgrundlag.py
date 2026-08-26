#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Præberegner oplandsgrundlaget (trin 0-2) for ét eller flere oplande.

Konditioneringen af terrænet og D8-beregningen afhænger kun af terrænet,
referencedataene og parametrene — ikke af hvor projektområdet ligger. De kan
derfor laves én gang pr. opland og genbruges af alle projekter deri. Det er den
tunge del: for et rigtigt projekt er det minutter til timer, mens selve
oplandssporingen bagefter er sekunder.

Terrænet læses fra DHM-distributionens 1 km-fliser i 0,4 m (den mappe drevet
allerede indeholder), ikke fra Dataforsyningens WCS. Der er hverken token,
kvote eller timeout involveret, og de 0,4 m aggregeres til analyseopløsningen
med middel — præcis som modellen selv ville gøre.

Køres uden for QGIS' brugerflade:

    "C:/Program Files/QGIS 3.44.11/bin/python-qgis-ltr.bat" Praeberegn_Oplandsgrundlag.py \
        --oplande "\\\\server\\...\\oplande_1orden_region.shp" --navn "Smakmølle Å" \
        --bibliotek "G:/Oplandsgrundlag"

    ... --fid 601                  vælg med feltnummer i stedet for navn
    ... --udtryk "NETVRKNR = 150"  vælg med et attributudtryk
    ... --alle-i "OplandLimfjord.shp"   kør hele regionen igennem

Hver flise lander i <bibliotek>/<flise-id>/ med de fem rastere trin 3 skal bruge
og et manifest der siger hvad de er beregnet på. Fliser der allerede er lavet med
de samme parametre, springes over, så en afbrudt batch kan genoptages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent

# DHM-distributionen: 10 km-blokke med 100 GeoTIFF-fliser à 1 km i 0,4 m.
# Rødderne kan overskrives med --dhm.
DHM_ROEDDER = [
    r"\\s101444\g5fv\Limfjordssekretariatet\GIS\Nationalt\DHM\DTM\Syd",
    r"\\s101444\g5fv\Limfjordssekretariatet\GIS\Nationalt\DHM\DTM\Nord\Nord",
]
BLOK_MOENSTER = "DTM_{n10}_{e10}_TIF_UTM32-ETRS89"
FLISE_MOENSTER = "DTM_1km_{n}_{e}.tif"

# Margin uden om oplandet. Konditioneringen skal have terræn på den anden side af
# vandskellet at arbejde med, ellers bliver strømningsretningen på selve skellet
# bestemt af rasterkanten.
STANDARD_MARGIN_M = 500.0


def _indlaes(navn: str, filnavn: str):
    """Indlæser et modul fra DENNE mappe under et entydigt navn."""
    if navn in sys.modules:
        return sys.modules[navn]
    spec = importlib.util.spec_from_file_location(navn, SCRIPTS / filnavn)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[navn] = modul
    spec.loader.exec_module(modul)
    return modul


# ── QGIS ─────────────────────────────────────────────────────────────────────

def start_qgis():
    """Starter QGIS uden brugerflade og registrerer Processing."""
    from qgis.core import QgsApplication

    if QgsApplication.instance() is not None:
        app, vi_startede = QgsApplication.instance(), False
    else:
        app = QgsApplication([], False)
        app.initQgis()
        vi_startede = True
    plugin_sti = os.path.join(QgsApplication.prefixPath(), "python", "plugins")
    if plugin_sti not in sys.path:
        sys.path.append(plugin_sti)
    from processing.core.Processing import Processing
    Processing.initialize()
    return app, vi_startede


# ── terræn ───────────────────────────────────────────────────────────────────

def dhm_fliser(udstraekning, roedder):
    """Stierne til de 1 km-DHM-fliser der dækker udstrækningen.

    Fliserne slås op på navn frem for ved at læse mapperne igennem: der er 22.500
    af dem på et netværksdrev, og et opslag pr. flise er hurtigere end én
    gennemløbning af det hele.
    """
    xmin, ymin, xmax, ymax = udstraekning
    fundet, manglende = [], []
    for e in range(int(xmin // 1000), int(xmax // 1000) + 1):
        for n in range(int(ymin // 1000), int(ymax // 1000) + 1):
            blok = BLOK_MOENSTER.format(n10=n // 10, e10=e // 10)
            filnavn = FLISE_MOENSTER.format(n=n, e=e)
            for rod in roedder:
                sti = os.path.join(rod, blok, filnavn)
                if os.path.isfile(sti):
                    fundet.append(sti)
                    break
            else:
                manglende.append((n, e))
    return fundet, manglende


def byg_terraen_vrt(udstraekning, roedder, ud_vrt: Path, log):
    """Samler DHM-fliserne til én virtuel raster som modellen kan læse."""
    from osgeo import gdal

    fliser, manglende = dhm_fliser(udstraekning, roedder)
    if not fliser:
        raise SystemExit(
            "Ingen DHM-fliser fundet for udstrækningen. Kontrollér --dhm-stierne:\n  "
            + "\n  ".join(roedder))
    if manglende:
        log(f"  ADVARSEL: {len(manglende)} km-flise(r) mangler i DHM-distributionen — "
            "terrænet er hullet dér. Første fem: "
            + ", ".join(f"{n}_{e}" for n, e in manglende[:5]))
    log(f"  terræn: {len(fliser)} km-fliser à 0,4 m")
    vrt = gdal.BuildVRT(str(ud_vrt), fliser)
    if vrt is None:
        raise SystemExit(f"Kunne ikke bygge {ud_vrt}")
    vrt = None
    return ud_vrt


# ── oplandene der skal beregnes ──────────────────────────────────────────────

def _flise_id(navn, fid):
    """Et mappenavn der kan bruges på alle filsystemer og stadig kan læses."""
    grund = (navn or "").strip().lower()
    grund = (grund.replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
                  .replace("ä", "ae").replace("ö", "oe"))
    grund = re.sub(r"[^a-z0-9]+", "_", grund).strip("_")
    return f"{grund}_{fid}" if grund else f"opland_{fid}"


def vaelg_oplande(sti, fid=None, navn=None, udtryk=None, indenfor=None,
                  indenfor_andel=0.5, log=print):
    """Oplandene der skal præberegnes, som (flise_id, navn, geometri)."""
    from osgeo import gdal, ogr

    gdal.SetConfigOption("SHAPE_ENCODING", "ISO-8859-1")
    ds = ogr.Open(str(sti))
    if ds is None:
        raise SystemExit(f"Kunne ikke åbne {sti}")
    lag = ds.GetLayer(0)
    d = lag.GetLayerDefn()
    navnefelt = next((n for n in ("VLNAVN", "navn", "NAVN", "SUBNETNAVN")
                      if d.GetFieldIndex(n) >= 0), None)

    if indenfor:
        ds_omr = ogr.Open(str(indenfor))
        if ds_omr is None:
            raise SystemExit(f"Kunne ikke åbne {indenfor}")
        omr = None
        for f in ds_omr.GetLayer(0):
            g = f.GetGeometryRef().Clone()
            omr = g if omr is None else omr.Union(g)
        lag.SetSpatialFilter(omr)

    else:
        omr = None

    if udtryk:
        lag.SetAttributeFilter(udtryk)

    valgte = []
    if fid is not None:
        f = lag.GetFeature(fid)
        if f is None:
            raise SystemExit(f"Der er intet objekt med FID {fid} i {sti}")
        valgte.append(f)
    else:
        for f in lag:
            if navn and (f.GetField(navnefelt) if navnefelt else None) != navn:
                continue
            valgte.append(f.Clone())
    if not valgte:
        raise SystemExit("Ingen oplande matchede valget.")

    ud = []
    udenfor = 0
    for f in valgte:
        g = f.GetGeometryRef()
        if g is None or g.IsEmpty():
            continue
        # Det rumlige filter er "roerer ved". Nabooplande deler graense med
        # omraadet uden at hoere til det, og et par af dem er paa tusind km2 —
        # de ville fylde mere end alt det oevrige tilsammen.
        if omr is not None and indenfor_andel > 0:
            snit = g.Intersection(omr)
            andel = (snit.GetArea() / g.GetArea()) if (snit and g.GetArea()) else 0.0
            if andel < indenfor_andel:
                udenfor += 1
                continue
        f_navn = (f.GetField(navnefelt) if navnefelt else None) or ""
        ud.append((_flise_id(f_navn, f.GetFID()), f_navn, g.Clone()))
    ds = None
    if udenfor:
        log(f"{udenfor} nabo-opland(e) udeladt — under "
            f"{indenfor_andel*100:.0f} % af arealet ligger i omraadet")
    # Mindste opland foerst. En batch paa mange oplande giver saa resultater tidligt,
    # og gaar noget galt, opdages det paa et lille opland frem for efter en time.
    ud.sort(key=lambda t: t[2].GetArea())
    return ud



def saml_smaa(valgte, mindste_km2, maks_km2, maks_spaend_m=12000.0, log=print):
    """Slaar smaa nabooplande sammen til én flise.

    Et 1.-ordens opland kan vaere 6 ha. En flise saa lille kan ingen bruge til
    noget: projektomraadet skal ligge helt inden for flisens daekning, og et
    projekt er typisk stoerre end det. Oven i det stopper trin 2 med "ingen
    stroemningsveje", fordi der ikke er 250 ha opstroems nogen steder inden for
    oplandet.

    Sammenlaegning er hydrologisk uproblematisk netop her: hvert 1.-ordens opland
    afvander selv til fjorden, saa foreningen af flere er lige saa komplet som
    hver enkelt. Der samles paa naerhed, saa flisens rektangel ikke bliver
    fyldt med hav og nabooplande.
    """
    from osgeo import ogr

    store = [r for r in valgte if r[2].GetArea() / 1e6 >= mindste_km2]
    smaa = [r for r in valgte if r[2].GetArea() / 1e6 < mindste_km2]
    if not smaa:
        return valgte

    def midte(g):
        c = g.Centroid()
        return c.GetX(), c.GetY()

    rest = list(smaa)
    klynger = []
    while rest:
        rest.sort(key=lambda r: r[2].GetArea())
        froe = rest.pop(0)
        med = [froe]
        areal = froe[2].GetArea() / 1e6
        # Rektanglet om klyngen er det der skal beregnes paa. To smaa oplande i
        # hver sin ende af fjorden fylder 6 km2 og et raster paa 300 mio. celler,
        # saa spaendet er den bindende graense — ikke arealet.
        def spaend(dele):
            xs, ys = [], []
            for _, _, g in dele:
                xmin, xmax, ymin, ymax = g.GetEnvelope()
                xs += [xmin, xmax]
                ys += [ymin, ymax]
            return max(xs) - min(xs), max(ys) - min(ys)

        while rest:
            mx, my = midte(froe[2])
            rest.sort(key=lambda r: (midte(r[2])[0] - mx) ** 2
                      + (midte(r[2])[1] - my) ** 2)
            naeste = rest[0]
            if areal + naeste[2].GetArea() / 1e6 > maks_km2:
                break
            bx, by = spaend(med + [naeste])
            if bx > maks_spaend_m or by > maks_spaend_m:
                break
            med.append(rest.pop(0))
            areal += med[-1][2].GetArea() / 1e6
            if areal >= mindste_km2:
                break
        klynger.append(med)

    ud = list(store)
    for med in klynger:
        forening = med[0][2].Clone()
        for _, _, g in med[1:]:
            forening = forening.Union(g)
        # Navnet kommer fra det stoerste medlem, saa flisen kan genkendes paa kortet.
        stoerst = max(med, key=lambda r: r[2].GetArea())
        flise_id = stoerst[0] if len(med) == 1 else "samling_" + stoerst[0]
        navn = stoerst[1] if len(med) == 1 else f"{stoerst[1] or stoerst[0]} m.fl."
        ud.append((flise_id, navn, forening))
    samlede = sum(1 for m in klynger if len(m) > 1)
    log(f"{len(smaa)} opland(e) under {mindste_km2:g} km2 samlet til "
        f"{len(klynger)} flise(r) ({samlede} med flere oplande i)")
    ud.sort(key=lambda r: r[2].GetArea())
    return ud

def skriv_omraade(geom, ud: Path, epsg: int):
    """Gemmer oplandspolygonen som fil — modellen læser sit område fra disk."""
    from osgeo import ogr, osr

    drv = ogr.GetDriverByName("GPKG")
    if ud.exists():
        drv.DeleteDataSource(str(ud))
    ds = drv.CreateDataSource(str(ud))
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(epsg)
    lag = ds.CreateLayer("omraade", sr, ogr.wkbMultiPolygon)
    f = ogr.Feature(lag.GetLayerDefn())
    g = ogr.ForceToMultiPolygon(geom)
    f.SetGeometry(g)
    lag.CreateFeature(f)
    ds = None
    return ud


# ── selve præberegningen ─────────────────────────────────────────────────────

def udskift_mappe(ny, maal, forsoeg=5):
    """Saetter en faerdig flise paa plads, ogsaa naar Windows er langsom til at slippe.

    Baade rmtree og rename fejler paa Windows hvis en fil i mappen stadig holdes
    aaben — af virusscanneren, indekseringen, eller QGIS hvis flisen er indlaest.
    Derfor: flyt den gamle til side, saet den nye paa plads, ryd op bagefter.
    """
    import time as _t

    ny, maal = Path(ny), Path(maal)
    if maal.exists():
        henlagt = Path(str(maal) + ".gammel_" + dt.datetime.now().strftime("%H%M%S"))
        for i in range(forsoeg):
            try:
                maal.rename(henlagt)
                break
            except OSError:
                if i == forsoeg - 1:
                    raise SystemExit(
                        f"{maal} kunne ikke flyttes til side — er flisen aaben i QGIS?")
                _t.sleep(1.0)
    for i in range(forsoeg):
        try:
            ny.rename(maal)
            break
        except OSError:
            if i == forsoeg - 1:
                raise
            _t.sleep(1.0)
    for rest in maal.parent.glob(maal.name + ".gammel_*"):
        shutil.rmtree(rest, ignore_errors=True)


def skriv_daekning(maske, geotransform, epsg, ud):
    """Gemmer maskens udstraekning som polygon, saa opslaget kan spoerge geometrisk."""
    import numpy as np
    from osgeo import gdal, ogr, osr

    hoejde, bredde = maske.shape
    mem = gdal.GetDriverByName("MEM").Create("", bredde, hoejde, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(geotransform)
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(epsg)
    mem.SetProjection(sr.ExportToWkt())
    mem.GetRasterBand(1).WriteArray(maske.astype(np.uint8))

    drv = ogr.GetDriverByName("GPKG")
    if os.path.exists(ud):
        drv.DeleteDataSource(str(ud))
    ds = drv.CreateDataSource(str(ud))
    lag = ds.CreateLayer("daekning", sr, ogr.wkbPolygon)
    lag.CreateField(ogr.FieldDefn("vaerdi", ogr.OFTInteger))
    gdal.Polygonize(mem.GetRasterBand(1), mem.GetRasterBand(1), lag, 0)
    ds = None
    mem = None
    return ud


def rasteriser_maske(maske_fil, som_raster):
    """Maskepolygonen paa et rasters eget grid, som en bool-array."""
    from osgeo import gdal, ogr

    r = gdal.Open(str(som_raster))
    mem = gdal.GetDriverByName("MEM").Create("", r.RasterXSize, r.RasterYSize, 1,
                                             gdal.GDT_Byte)
    mem.SetGeoTransform(r.GetGeoTransform())
    mem.SetProjection(r.GetProjection())
    ds = ogr.Open(str(maske_fil))
    gdal.RasterizeLayer(mem, [1], ds.GetLayer(0), burn_values=[1])
    arr = mem.GetRasterBand(1).ReadAsArray().astype(bool)
    mem = None
    ds = None
    r = None
    return arr



def praeberegn(flise_id, navn, geom, bibliotek: Path, om, oplande, argumenter, log):
    """Kører trin 0-2 for ét opland og lægger resultatet i biblioteket."""
    from osgeo import gdal

    maal = Path(bibliotek) / flise_id
    xmin, xmax, ymin, ymax = geom.GetEnvelope()
    margin = argumenter.margin
    udstraekning = (xmin - margin, ymin - margin, xmax + margin, ymax + margin)
    km2 = geom.GetArea() / 1e6

    log("")
    log(f"=== {navn or flise_id}  ({km2:,.1f} km2, flise {flise_id})")

    arbejde = Path(argumenter.arbejdsmappe or tempfile.gettempdir()) / ("grundlag_" + flise_id)
    derived = arbejde / "mellemresultater"
    logmappe = arbejde / "log"
    for m in (arbejde, derived, logmappe):
        m.mkdir(parents=True, exist_ok=True)

    omraade_fil = skriv_omraade(geom, arbejde / "opland.gpkg", argumenter.epsg)

    stier = {
        'arbejdsmappe': arbejde, 'derived': derived, 'log': logmappe,
        'konf_fil': arbejde / "parametre_koersel.yml",
        'gpkg': arbejde / "ubrugt.gpkg",
    }
    konf = om.byg_konfiguration(
        epsg=argumenter.epsg, dem_sti="(terraenet samles nedenfor)",
        projekt=(omraade_fil, "omraade"),
        vandloeb=(Path(om.vandloeb_standard()), None) if om.vandloeb_standard() else None,
        tilpasninger=((Path(om.tilpasninger_standard()), None)
                      if om.tilpasninger_standard() else None),
        stier=stier, oploesning=argumenter.oploesning,
        burn_dybde=argumenter.burn_dybde, breach_dist=argumenter.breach_dist,
        stroem_taerskel_ha=argumenter.taerskel,
        fill_metode=argumenter.fill_metode or om.STD_FILL_METODE)
    konf['projekt']['navn'] = navn or flise_id
    noegle = om.konditioneringsnoegle(konf)

    gammelt = om.laes_manifest(maal)
    if (gammelt and gammelt.get('konditioneringsnoegle') == noegle
            and gammelt.get('grundlag_version') == om.GRUNDLAG_VERSION
            and all((maal / r).is_file() for r in om.GRUNDLAG_RASTERE)
            and not argumenter.tving):
        log(f"  findes allerede med samme parametre ({noegle}) — springes over")
        return "sprunget over"

    # Foerst nu samles terraenet. At bygge VRT'en over 200 km-fliser paa et
    # netvaerksdrev tager tid, og der er ingen grund til at goere det for en flise
    # der allerede er lavet.
    vrt = byg_terraen_vrt(udstraekning, argumenter.dhm, arbejde / "terraen.vrt", log)
    konf["input"]["dem"] = str(vrt)

    om.gem_konfiguration(konf, stier['konf_fil'])
    oplande.AKTIV_KONFIG = stier['konf_fil']

    koersel_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    motorlog = oplande.Log(logmappe / f"{koersel_id}.log", koersel_id)
    t0 = time.time()
    try:
        oplande.registrer_whitebox(motorlog)
        om.vaelg_fill(oplande, konf, motorlog)
        om.haardfoer_whitebox(oplande, motorlog)
        oplande.gem_parameterlog(konf, logmappe, koersel_id)
        dem_analyse = oplande.trin0_klargoer(konf, motorlog)
        om.undgaa_tomme_input(konf, dem_analyse, motorlog)
        dem_hydro = oplande.trin1_konditioner(konf, dem_analyse, motorlog)
        stroem = oplande.trin2_stroemning(konf, dem_hydro, motorlog)
    finally:
        motorlog.luk()
    sekunder = time.time() - t0

    from qgis.core import QgsCoordinateReferenceSystem
    import numpy as _np
    motorlog2 = oplande.Log(logmappe / f"{koersel_id}_linjer.log", koersel_id)

    # Oplandet spores foerst. Baade masken, klipningen af stroemningsvejene og
    # kontrollen af netlaengden skal bruge det, og det er modellens egen sporing —
    # ikke den kortlagte oplandsgraense — der bestemmer hvor vandet faktisk kommer
    # fra.
    opland_arr = rasteriser_maske(omraade_fil, derived / "02_d8_pointer.tif")
    form = opland_arr.shape
    graf = oplande.Stroemningsgraf(stroem["pointer"], dem_hydro, motorlog2)
    sporet = graf.opstroems(_np.flatnonzero(opland_arr.ravel()))
    kerne = ((sporet > 0) | opland_arr.ravel()).reshape(form)
    kerne_ha = graf.ha(int(kerne.sum()))
    log(f"  oplandets opstroems-areal: {kerne_ha:,.1f} ha "
        f"(kortlagt polygon: {km2 * 100:,.1f} ha)")
    del graf, sporet

    try:
        # 1) Nettet skal daekke de kortlagte vandloeb helt. Taersklen alene stopper
        #    for tidligt i toppen af hvert forloeb, hvor der er under 250 ha opstroems.
        foer = om.netdaekning(stroem, om.vandloeb_standard(), None, kerne)
        _, tilfoejede = om.udvid_stroemme_til_kortlagte(
            oplande, stroem, dem_hydro, om.vandloeb_standard(), None, motorlog2)

        # 2) Klip til oplandet. I margenen ligger stumper af nabooplandenes vandloeb,
        #    og de hoerer ikke til i denne flise. De stumper udvidelsen selv lagde
        #    til, fredes: de ligger paa en sporing fra et kortlagt vandloeb i netop
        #    dette opland, og uden dem er det beregnede net kortere end det kortlagte.
        om.klip_stroemme_til(stroem, kerne, motorlog2, fred=tilfoejede)
        daekning = om.netdaekning(stroem, om.vandloeb_standard(), None, kerne)
        log(f"  net i oplandet: {foer['beregnet_km']:,.1f} -> "
            f"{daekning['beregnet_km']:,.1f} km mod {daekning['kortlagt_km']:,.1f} km "
            f"kortlagt")
        if daekning['daekning_pct'] is not None:
            log(f"  daekning af de kortlagte vandloeb: "
                f"{foer['daekning_pct']:.1f} % -> {daekning['daekning_pct']:.1f} % "
                f"(inden for {daekning['afstand_m']:.0f} m, "
                f"{daekning['punkter']:,} punkter)")
            if daekning['daekning_pct'] < 99.0:
                log("  ADVARSEL: det beregnede net daekker ikke de kortlagte vandloeb "
                    "helt")

        # Et tomt net accepteres kun naar der ikke er noget kortlagt vandloeb at
        # sammenligne med. Er der ét, og fandt modellen intet, er der noget galt.
        antal, ordener = om.skriv_channel_networks(
            oplande, konf, derived, arbejde / om.CHANNEL_NETWORKS, stroem,
            koersel_id, QgsCoordinateReferenceSystem(f"EPSG:{argumenter.epsg}"),
            motorlog2, tillad_tomt=not daekning.get('kortlagt_km'))
        log(f"  stroemningsveje: {antal} straekninger, Strahler-orden "
            + ", ".join(f"{o}: {n}" for o, n in sorted(ordener.items(),
                                                       key=lambda kv: (kv[0] is None, kv[0]))))
    except Exception:
        motorlog2.luk()
        raise
    traef = None
    if om.vandloeb_standard():
        try:
            traef = om.traefprocent(stroem['akkumulering'], om.vandloeb_standard())
        except Exception as e:
            log(f"  træfprocenten kunne ikke måles: {e!r}")

    # Læg rasterne på plads. Der skrives til en midlertidig mappe og flyttes til
    # sidst, så en afbrudt kørsel ikke efterlader en flise der ser færdig ud.
    midlertidig = Path(str(maal) + ".ufaerdig")
    if midlertidig.exists():
        shutil.rmtree(midlertidig, ignore_errors=True)
    midlertidig.mkdir(parents=True)
    fylder = 0
    linjer_fil = arbejde / om.CHANNEL_NETWORKS
    if linjer_fil.is_file():
        shutil.copy2(linjer_fil, midlertidig / om.CHANNEL_NETWORKS)
        fylder += (midlertidig / om.CHANNEL_NETWORKS).stat().st_size

    # Kun det terraen der kan komme til at betyde noget, gemmes.
    #
    # Masken bygges paa MODELLENS EGEN sporing, ikke paa den kortlagte
    # oplandsgraense: de to er ikke enige helt ned i detaljen, og det er modellens
    # vandskel der bestemmer hvor et projekts opland naar hen. Sporer man opstroems
    # fra hele oplandet og lægger en margin til, er enhver flise per konstruktion
    # stor nok til ethvert projekt inde i den — der er ikke noget at kontrollere
    # bagefter.
    import numpy as _np

    _r0 = gdal.Open(str(derived / "01_dem_hydro.tif"))
    gt_daekning = _r0.GetGeoTransform()
    g0_opl = gt_daekning[1]
    _r0 = None
    udenfor_ha = None
    if argumenter.ingen_maske:
        maske_arr = _np.ones(form, dtype=bool)
    else:
        from scipy import ndimage
        celler = max(1, int(round(margin / abs(g0_opl))))
        afstand = ndimage.distance_transform_edt(~kerne)
        maske_arr = afstand <= celler
        udenfor_ha = 0.0
        del afstand

    raekker = _np.where(maske_arr.any(axis=1))[0]
    kolonner = _np.where(maske_arr.any(axis=0))[0]
    r0, r1 = int(raekker[0]), int(raekker[-1]) + 1
    c0, c1 = int(kolonner[0]), int(kolonner[-1]) + 1
    log(f"  gemmer {(r1-r0)*(c1-c0)/1e6:,.1f} mio. celler af {form[0]*form[1]/1e6:,.1f} "
        f"({100.0*maske_arr.sum()/(form[0]*form[1]):,.0f} % af rektanglet er inden for masken)")

    # NoData vaelges efter hvad rasteret betyder: -9999 er modellens markering i
    # terraenet, mens 0 i stroemningsretning og -akkumulering betyder "ingenting
    # her" og laeses uden videre af sporingen.
    nodata = {"00_dem_analyse.tif": -9999.0, "01_dem_hydro.tif": -9999.0,
              "02_d8_pointer.tif": 0, "02_d8_akkumulering.tif": 0,
              "02_streams.tif": 0}
    udsnit = maske_arr[r0:r1, c0:c1]
    for navn_raster in om.GRUNDLAG_RASTERE:
        kilde = derived / navn_raster
        if not kilde.is_file():
            raise SystemExit(f"Beregningen efterlod ikke {kilde}")
        kr = gdal.Open(str(kilde))
        baand = kr.GetRasterBand(1)
        arr = baand.ReadAsArray()[r0:r1, c0:c1]
        datatype = baand.DataType
        gt_k = kr.GetGeoTransform()
        projektion = kr.GetProjection()
        kr = None
        arr = _np.where(udsnit, arr, nodata[navn_raster])
        flydende = gdal.GetDataTypeName(datatype).startswith("Float")
        drv = gdal.GetDriverByName("GTiff")
        ud = drv.Create(str(midlertidig / navn_raster), c1 - c0, r1 - r0, 1, datatype,
                        options=["COMPRESS=DEFLATE", "TILED=YES",
                                 "PREDICTOR=" + ("3" if flydende else "2")])
        ud.SetGeoTransform((gt_k[0] + c0 * gt_k[1], gt_k[1], gt_k[2],
                            gt_k[3] + r0 * gt_k[5], gt_k[4], gt_k[5]))
        ud.SetProjection(projektion)
        b = ud.GetRasterBand(1)
        b.WriteArray(arr)
        b.SetNoDataValue(float(nodata[navn_raster]))
        ud = None
        fylder += (midlertidig / navn_raster).stat().st_size

    # Daekningen skrives som polygon. Uden den kunne opslaget kun sammenligne med
    # flisens rektangel, og et projektomraade i hjoernet — uden for det maskerede
    # opland — ville faa sit opland afkortet ved NoData. Kantkontaminerings-tjekket
    # fanger det ikke: det maaler kun celler paa selve rasterkanten.
    skriv_daekning(maske_arr[r0:r1, c0:c1],
                   (gt_daekning[0] + c0 * gt_daekning[1], gt_daekning[1], 0.0,
                    gt_daekning[3] + r0 * gt_daekning[5], 0.0, gt_daekning[5]),
                   argumenter.epsg, midlertidig / om.DAEKNING)
    fylder += (midlertidig / om.DAEKNING).stat().st_size

    motorlog2.luk()


    r = gdal.Open(str(derived / "01_dem_hydro.tif"))
    gt = r.GetGeoTransform()
    faktisk = [gt[0], gt[3] + gt[5] * r.RasterYSize,
               gt[0] + gt[1] * r.RasterXSize, gt[3]]
    celler = r.RasterXSize * r.RasterYSize
    r = None

    manifest = {
        "grundlag_version": om.GRUNDLAG_VERSION,
        "flise_id": flise_id,
        "navn": navn,
        "beregnet": dt.datetime.now().isoformat(timespec="seconds"),
        "koersel_id": koersel_id,
        "sekunder": round(sekunder, 1),
        "areal_km2": round(km2, 2),
        "udstraekning": [round(v, 1) for v in faktisk],
        "epsg": argumenter.epsg,
        "oploesning_m": argumenter.oploesning,
        "celler": celler,
        "margin_m": margin,
        "maskeret": not argumenter.ingen_maske,
        "maske": ("oplandets sporede opstroems-areal + margin"
                  if not argumenter.ingen_maske else "hele rektanglet"),
        "konditioneringsnoegle": noegle,
        "kilde": {
            "oplande": str(argumenter.oplande),
            "fid": int(flise_id.rsplit("_", 1)[-1]) if flise_id.rsplit("_", 1)[-1].isdigit() else None,
            "dhm": "DHM/DTM 1 km-fliser, 0,4 m",
            "vandloeb": om.vandloeb_standard(),
            "tilpasninger": om.tilpasninger_standard(),
        },
        "traefprocent": (round(traef["pct"], 1) if traef else None),
        "netlaengde_km": round(daekning["beregnet_km"], 1),
        "kortlagt_netlaengde_km": round(daekning["kortlagt_km"], 1),
        "daekning_af_kortlagte_pct": (round(daekning["daekning_pct"], 1)
                                      if daekning["daekning_pct"] is not None else None),
        "opstroems_areal_ha": round(kerne_ha, 1),
        "traefprocent_knuder": (traef["knuder"] if traef else None),
        "parametre": konf,
    }
    (midlertidig / om.MANIFEST).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    udskift_mappe(midlertidig, maal)

    if not argumenter.behold_arbejdsmappe:
        shutil.rmtree(arbejde, ignore_errors=True)

    log(f"  FÆRDIG paa {sekunder:,.0f} s | {celler/1e6:,.1f} mio. celler | "
        f"{fylder/1e6:,.0f} MB"
        + (f" | traefprocent {traef['pct']:.1f} %" if traef else ""))
    return "beregnet"


# ── kommandolinje ────────────────────────────────────────────────────────────

def koer_i_egen_proces(flise_id, navn, geom, argumenter, log):
    """Koerer én flise som en selvstaendig proces.

    Whitebox har to gange vist at den kan tage hele processen med sig: en
    mislykket allokering draeber den paa stedet, og efter en haandfuld koersler
    holder provideren op med at kunne oprette algoritmer ("Error creating
    algorithm from createInstance"). I én lang proces betyder det at resten af
    natten er spildt. Med en proces pr. flise koster en fejlet flise kun den ene.
    """
    import subprocess

    # Geometrien gives med som fil, ikke som et FID. En flise kan vaere flere
    # smaa oplande lagt sammen, og den findes ikke som ét objekt i kildelaget.
    valgmappe = Path(argumenter.arbejdsmappe or argumenter.bibliotek) / "_valgte"
    valgmappe.mkdir(parents=True, exist_ok=True)
    geometrifil = skriv_omraade(geom, valgmappe / f"{flise_id}.gpkg", argumenter.epsg)

    kommando = [sys.executable, str(Path(__file__).resolve()),
                "--oplande", str(argumenter.oplande),
                "--bibliotek", str(argumenter.bibliotek),
                "--geometri-fil", str(geometrifil),
                "--flise-id", flise_id,
                "--flise-navn", navn or "",
                "--i-samme-proces",
                # Ét forsoeg i barnet. Whitebox' provider kan holde op med at
                # kunne oprette algoritmer i en proces der har koert laenge, og
                # saa er et genforsoeg i samme proces spildt. Foraelderen proever
                # igen i en frisk proces.
                "--forsoeg", "1",
                "--oploesning", str(argumenter.oploesning),
                "--margin", str(argumenter.margin),
                "--epsg", str(argumenter.epsg),
                "--taerskel", str(argumenter.taerskel),
                "--burn-dybde", str(argumenter.burn_dybde),
                "--breach-dist", str(argumenter.breach_dist)]
    if argumenter.fill_metode:
        kommando += ["--fill-metode", str(argumenter.fill_metode)]
    if argumenter.arbejdsmappe:
        kommando += ["--arbejdsmappe", str(argumenter.arbejdsmappe)]
    if argumenter.ingen_maske:
        kommando.append("--ingen-maske")
    if argumenter.behold_arbejdsmappe:
        kommando.append("--behold-arbejdsmappe")
    if argumenter.tving:
        kommando.append("--tving")
    for rod in argumenter.dhm:
        kommando += ["--dhm", rod]

    # Tidsgraense. Netvaerksdrevet har leveret afkortede TIFF-striber, og GDAL
    # bliver ved med at proeve: én flise brugte 15 timer paa det, mens medianen er
    # et halvt minut. En flise maa ikke kunne aede natten.
    try:
        proces = subprocess.run(kommando, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=argumenter.tidsgraense)
    except subprocess.TimeoutExpired:
        log(f"  AFBRUDT efter {argumenter.tidsgraense/60:.0f} min — "
            f"formentlig laesefejl paa terraendrevet; proeves igen til sidst")
        return "fejlet"
    for linje in (proces.stdout or "").splitlines():
        if linje.strip() and not linje.startswith("["):
            log(linje)
    if proces.returncode != 0:
        for linje in (proces.stderr or "").splitlines()[-4:]:
            if linje.strip():
                log("    " + linje)
        log(f"  FEJLEDE (processen sluttede med {proces.returncode})")
        return "fejlet"
    return "sprunget over" if "springes over" in (proces.stdout or "") else "beregnet"


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Præberegn oplandsgrundlag (trin 0-2) til pluginnets bibliotek")
    p.add_argument("--oplande", required=True, help="shapefil med oplandspolygoner")
    p.add_argument("--bibliotek", required=True, help="mappe hvor fliserne lægges")
    p.add_argument("--fid", type=int, help="vælg ét opland på feltnummer")
    p.add_argument("--geometri-fil", dest="geometri_fil",
                   help="beregn præcis denne polygon i stedet for at vælge i --oplande")
    p.add_argument("--flise-id", dest="flise_id", help="flisens navn på disk, sammen med --geometri-fil")
    p.add_argument("--flise-navn", dest="flise_navn", default="", help="flisens visningsnavn")
    p.add_argument("--saml-under-km2", dest="saml_under_km2", type=float, default=0.0,
                   help="saml oplande under denne størrelse med naboerne til én flise")
    p.add_argument("--saml-op-til-km2", dest="saml_op_til_km2", type=float, default=25.0,
                   help="hvor store de samlede fliser må blive")
    p.add_argument("--navn", help="vælg oplande på navn (VLNAVN)")
    p.add_argument("--udtryk", help="vælg oplande med et attributudtryk")
    p.add_argument("--alle-i", dest="alle_i", help="kun oplande inden for denne polygon")
    p.add_argument("--min-km2", type=float, default=0.0, help="spring mindre oplande over")
    p.add_argument("--maks-km2", type=float, default=0.0, help="spring større oplande over")
    p.add_argument("--maks-celler", type=float, default=0.0,
                   help="spring oplande over hvis analyserasteret bliver større "
                        "end dette (celler). fill_depressions i Whitebox sprænger "
                        "hukommelsen et sted mellem 41 og 48 mio. celler")
    p.add_argument("--dhm", action="append", help="rod til DHM-fliser (kan gentages)")
    p.add_argument("--oploesning", type=float, default=None)
    p.add_argument("--margin", type=float, default=STANDARD_MARGIN_M,
                   help="margin uden om oplandet (m)")
    p.add_argument("--ingen-maske", dest="ingen_maske", action="store_true",
                   help="gem hele rektanglet i stedet for kun oplandet + margin")
    p.add_argument("--epsg", type=int, default=25832)
    p.add_argument("--taerskel", type=float, default=None, help="vandløbstærskel (ha)")
    p.add_argument("--burn-dybde", dest="burn_dybde", type=float, default=None)
    p.add_argument("--breach-dist", dest="breach_dist", type=int, default=None)
    p.add_argument("--fill-metode", dest="fill_metode", default=None,
                   choices=("fill", "wang_and_liu", "planchon_and_darboux"),
                   help="hvilken fill der rydder op efter breaching; "
                        "wang_and_liu naar fill_depressions loeber toer for hukommelse")
    p.add_argument("--arbejdsmappe", help="hvor mellemresultater lægges undervejs")
    p.add_argument("--behold-arbejdsmappe", dest="behold_arbejdsmappe",
                   action="store_true")
    p.add_argument("--tidsgraense", type=float, default=1800.0,
                   help="sekunder én flise må tage, før den afbrydes (standard 30 min)")
    p.add_argument("--forsoeg", type=int, default=2,
                   help="hvor mange gange de fejlede fliser prøves igen til sidst")
    p.add_argument("--tving", action="store_true", help="genberegn selv om flisen findes")
    p.add_argument("--i-samme-proces", dest="i_samme_proces", action="store_true",
                   help="koer fliserne i denne proces frem for én proces pr. flise")
    a = p.parse_args(argv)

    if not a.dhm:
        a.dhm = DHM_ROEDDER

    app, vi_startede = start_qgis()
    om = _indlaes("oplandsmodel_batch", "oplandsmodel.py")
    oplande = om.indlaes_oplande()

    if a.oploesning is None:
        a.oploesning = om.STD_OPLOESNING
    if a.taerskel is None:
        a.taerskel = om.STD_STROEM_TAERSKEL
    if a.burn_dybde is None:
        a.burn_dybde = om.STD_BURN_DYBDE
    if a.breach_dist is None:
        a.breach_dist = om.STD_BREACH_DIST

    bibliotek = Path(a.bibliotek)
    bibliotek.mkdir(parents=True, exist_ok=True)

    def log(besked=""):
        print(besked, flush=True)

    if a.geometri_fil:
        from osgeo import ogr as _ogr
        _ds = _ogr.Open(str(a.geometri_fil))
        if _ds is None:
            raise SystemExit(f"Kunne ikke åbne {a.geometri_fil}")
        _g = None
        for _f in _ds.GetLayer(0):
            _gg = _f.GetGeometryRef()
            if _gg is not None:
                _gg = _gg.Clone()
                _g = _gg if _g is None else _g.Union(_gg)
        _ds = None
        if _g is None:
            raise SystemExit(f"Ingen geometri i {a.geometri_fil}")
        valgte = [(a.flise_id or Path(a.geometri_fil).stem, a.flise_navn or "", _g)]
    else:
        valgte = vaelg_oplande(a.oplande, fid=a.fid, navn=a.navn, udtryk=a.udtryk,
                               indenfor=a.alle_i, log=log)
        if a.saml_under_km2:
            valgte = saml_smaa(valgte, a.saml_under_km2, a.saml_op_til_km2, log=log)
    if a.min_km2:
        valgte = [v for v in valgte if v[2].GetArea() / 1e6 >= a.min_km2]
    if a.maks_km2:
        valgte = [v for v in valgte if v[2].GetArea() / 1e6 <= a.maks_km2]
    if a.maks_celler:
        # Rektanglet om oplandet er det der skal beregnes paa, og et langstrakt
        # opland paa 55 km2 kan give et stoerre raster end et kompakt paa 200.
        import math

        def celler(geom):
            xmin, xmax, ymin, ymax = geom.GetEnvelope()
            bredde = math.ceil((xmax + a.margin) / 1000) - math.floor((xmin - a.margin) / 1000)
            hoejde = math.ceil((ymax + a.margin) / 1000) - math.floor((ymin - a.margin) / 1000)
            return bredde * hoejde * 1e6 / (a.oploesning ** 2)

        for_store = [v for v in valgte if celler(v[2]) > a.maks_celler]
        valgte = [v for v in valgte if celler(v[2]) <= a.maks_celler]
        if for_store:
            log(f"{len(for_store)} opland(e) springes over — analyserasteret ville "
                f"blive over {a.maks_celler/1e6:,.0f} mio. celler:")
            for flise_id, navn, geom in sorted(for_store, key=lambda v: -celler(v[2]))[:10]:
                log(f"   {(navn or flise_id)[:34]:34s} {geom.GetArea()/1e6:7.0f} km2 "
                    f"-> {celler(geom)/1e6:6.0f} mio. celler")
    samlet = sum(v[2].GetArea() / 1e6 for v in valgte)
    log(f"{len(valgte)} opland(e) valgt, i alt {samlet:,.0f} km2 "
        f"(anslået {samlet * 2.05 / 1000:.1f} GB)")

    resultat = {"beregnet": 0, "sprunget over": 0, "fejlet": 0}
    t0 = time.time()

    def koer_en(flise_id, navn, geom):
        if a.i_samme_proces:
            try:
                return praeberegn(flise_id, navn, geom, bibliotek, om, oplande,
                                  a, log)
            except SystemExit as e:
                log(f"  FEJLEDE: {e}")
                return "fejlet"
            except Exception as e:
                log(f"  FEJLEDE: {e!r}")
                return "fejlet"
        return koer_i_egen_proces(flise_id, navn, geom, a, log)

    # Fejlede fliser proeves igen til sidst. Laesefejlene paa terraendrevet er
    # forbigaaende, og en runde mere koster kun de fliser der faktisk fejlede.
    rest = list(valgte)
    forsoeg = max(1, a.forsoeg)
    for runde in range(1, forsoeg + 1):
        if runde > 1:
            if not rest:
                break
            log(f"\n=== forsoeg {runde} af {forsoeg}: {len(rest)} flise(r) der fejlede")
        naeste = []
        for i, (flise_id, navn, geom) in enumerate(rest, 1):
            log(f"\n[{i}/{len(rest)}]" + (f" (forsoeg {runde})" if runde > 1 else ""))
            svar = koer_en(flise_id, navn, geom)
            if svar == "fejlet" and runde < forsoeg:
                naeste.append((flise_id, navn, geom))
            else:
                resultat[svar] += 1
        rest = naeste

    log(f"\n=== {resultat['beregnet']} beregnet, {resultat['sprunget over']} sprunget over, "
        f"{resultat['fejlet']} fejlet paa {(time.time() - t0)/60:,.1f} min")
    log(f"    bibliotek: {bibliotek}")
    if vi_startede:
        app.exitQgis()
    return 1 if resultat["fejlet"] else 0


if __name__ == "__main__":
    sys.exit(main())
