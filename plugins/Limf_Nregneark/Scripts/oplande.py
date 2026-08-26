#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Oplandsanalyse: direkte opland og vandloebsoplande TIL et projektomraade.

Beregner oplande ud fra en projektpolygon og en terraenmodel med Whitebox Workflows i QGIS.
Trinrækkefølgen følger PLAN.md og er autoritativ. Begreberne totalopland, vandloebsopland og
direkte opland er defineret i CLAUDE.md — de blandes rutinemæssigt sammen, saa slaa dem op.

Trin 0-6 er implementeret: klargoering, konditionering, stroemning, totalopland,
vandloebsoplande, direkte opland, leverance og QA.

Koersel:
    # standalone
    "C:/Program Files/QGIS 3.44.11/bin/python-qgis-ltr.bat" oplande.py --trin 0

    # i QGIS' Python-konsol
    exec(open(r"C:/Users/n1mhro/Downloads/oplande/oplande.py").read())
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable

PROJEKT_ROD = Path(__file__).resolve().parent
STANDARD_KONFIG = PROJEKT_ROD / "config" / "parametre.yml"

# Den konfiguration der faktisk er i brug. Saettes i main(); genberegningstjek maa
# sammenligne mod denne, ikke mod standardstien.
AKTIV_KONFIG: Path = STANDARD_KONFIG

# Providerens ID og navnekonvention er verificeret mod Whitebox Workflows 2.1.3.
# Se .claude/skills/whitebox-hydrologi for de fulde signaturer.
WB_PROVIDER = "whitebox_workflows"


class OplandsFejl(RuntimeError):
    """Fejl der skal stoppe koerslen. Et tomt eller usandsynligt resultat er en fejl, ikke et svar."""


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

def indlaes_konfiguration(sti: Path) -> dict:
    """Laeser parametre.yml. Ingen stier eller parametre hoerer hjemme i denne fil."""
    if not sti.exists():
        raise OplandsFejl(f"Konfigurationen findes ikke: {sti}")
    import yaml  # PyYAML er bundlet med QGIS
    with open(sti, encoding="utf-8") as f:
        konf = yaml.safe_load(f)
    if not isinstance(konf, dict):
        raise OplandsFejl(f"Konfigurationen er tom eller ugyldig: {sti}")
    return konf


def hent(konf: dict, noeglesti: str, standard: Any = "__kraevet__") -> Any:
    """Slaar en punktsepareret noegle op, fx 'analyse.oplaesning_m'."""
    vaerdi: Any = konf
    for del_ in noeglesti.split("."):
        if not isinstance(vaerdi, dict) or del_ not in vaerdi:
            if standard == "__kraevet__":
                raise OplandsFejl(f"Manglende noegle i konfigurationen: {noeglesti}")
            return standard
        vaerdi = vaerdi[del_]
    return vaerdi


def absolut(sti: str | os.PathLike) -> Path:
    """Loeser en sti relativt til projektroden, saa koerselsmappen er ligegyldig."""
    p = Path(sti)
    return p if p.is_absolute() else (PROJEKT_ROD / p)


def valgfri_sti(konf: dict, noeglesti: str) -> Path | None:
    """
    Returnerer stien hvis den er angivet OG findes, ellers None.

    Bruges til de datasaet analysen kan koere uden, men ikke boer. Kaldere skal raabe op —
    et manglende lag maa ikke bare forsvinde stille ud af kaeden.
    """
    raa = hent(konf, noeglesti, None)
    if not raa:
        return None
    sti = absolut(raa)
    return sti if sti.exists() else None


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

class Log:
    """
    Skriver til baade skaerm og logfil. Koerslen skal kunne rekonstrueres om et aar.

    `ekstra` er en valgfri modtager af hver linje. Den bruges af QGIS-vaerktoejet til at
    sende logen videre til Processing-panelet, saa brugeren ser det samme som filen
    indeholder — ikke en forkortet udgave.
    """

    def __init__(self, logfil: Path, koersel_id: str, ekstra=None):
        self.koersel_id = koersel_id
        self.logfil = logfil
        self.ekstra = ekstra
        logfil.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(logfil, "a", encoding="utf-8")
        self.skriv(f"=== koersel {koersel_id} startet {dt.datetime.now().isoformat(timespec='seconds')} ===")

    def skriv(self, besked: str) -> None:
        linje = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {besked}"
        print(linje, flush=True)
        self._f.write(linje + "\n")
        self._f.flush()
        if self.ekstra is not None:
            self.ekstra(besked)

    def advar(self, besked: str) -> None:
        self.skriv("ADVARSEL: " + besked)

    def luk(self) -> None:
        self.skriv("=== koersel afsluttet ===")
        self._f.close()


# ---------------------------------------------------------------------------
# QGIS og Whitebox
# ---------------------------------------------------------------------------

def start_qgis() -> tuple[Any, bool]:
    """
    Initialiserer QGIS hvis det ikke allerede koerer.

    Returnerer (app, vi_startede_den). I QGIS' Python-konsol er alt sat op i forvejen;
    standalone skal Processing-frameworket registreres eksplicit.
    """
    from qgis.core import QgsApplication

    eksisterende = QgsApplication.instance()
    if eksisterende is not None:
        _init_processing()
        return eksisterende, False

    app = QgsApplication([], False)
    app.initQgis()
    _init_processing()
    return app, True


def _init_processing() -> None:
    """Registrerer native algoritmer. Idempotent."""
    from qgis.core import QgsApplication

    plugin_sti = os.path.join(QgsApplication.prefixPath(), "python", "plugins")
    if plugin_sti not in sys.path:
        sys.path.append(plugin_sti)
    from processing.core.Processing import Processing
    Processing.initialize()


def registrer_whitebox(log: Log) -> None:
    """
    Registrerer Whitebox Workflows-provideren.

    Plugin'ets classFactory kan ikke bruges headless — den kraever en GUI-iface. Provideren
    instantieres derfor direkte. refreshAlgorithms() er ikke valgfri: uden den er provideren
    registreret, men leverer nul algoritmer.
    """
    from qgis.core import QgsApplication

    reg = QgsApplication.processingRegistry()
    if reg.providerById(WB_PROVIDER) is not None:
        return

    bruger_plugins = Path(os.path.expandvars(
        r"%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins"))
    if str(bruger_plugins) not in sys.path:
        sys.path.insert(0, str(bruger_plugins))

    try:
        from whitebox_workflows_for_qgis.provider import WhiteboxProcessingProvider
    except ImportError as e:
        raise OplandsFejl(
            "Whitebox Workflows-plugin'et kunne ikke importeres. Forventet i "
            f"{bruger_plugins}. Oprindelig fejl: {e!r}"
        ) from e

    provider = WhiteboxProcessingProvider(include_pro=True, tier="open", iface=None)
    reg.addProvider(provider)
    provider.refreshAlgorithms()

    antal = len([a for a in reg.algorithms() if a.id().startswith(WB_PROVIDER + ":")])
    if antal == 0:
        raise OplandsFejl(
            "Whitebox-provideren blev registreret, men leverer nul algoritmer. "
            "Er python-pakken 'whitebox_workflows' installeret?"
        )
    log.skriv(f"Whitebox Workflows registreret: {antal} algoritmer")


def kraev_algoritme(vaerktoej: str) -> str:
    """
    Verificerer at et Whitebox-vaerktoej findes, og returnerer dets fulde ID.

    Hardcodede ID'er fejler foerst efter tyve minutters beregning. Tjek dem paa forhaand.
    """
    from qgis.core import QgsApplication

    aid = f"{WB_PROVIDER}:{vaerktoej}"
    if QgsApplication.processingRegistry().algorithmById(aid) is None:
        raise OplandsFejl(f"Algoritmen findes ikke i registret: {aid}")
    return aid


# ---------------------------------------------------------------------------
# Koerselshjaelpere
# ---------------------------------------------------------------------------

def _feedback(log: Log):
    from qgis.core import QgsProcessingFeedback

    class LogFeedback(QgsProcessingFeedback):
        def reportError(self, fejl, fatalError=False):  # noqa: N803
            log.skriv("  processing-fejl: " + str(fejl))

        def pushWarning(self, advarsel):
            log.advar("  " + str(advarsel))

    return LogFeedback()


def koer(alg_id: str, parametre: dict, log: Log, beskrivelse: str = "") -> dict:
    """
    Kalder processing.run med feedback tilkoblet og verificerer at der faktisk kom noget ud.

    processing.run rejser ikke altid ved fejl. Et tomt resultat der foeres videre giver en
    fejl fem trin senere, hvor aarsagen er umulig at finde.
    """
    import processing

    log.skriv(f"  -> {alg_id} {beskrivelse}".rstrip())
    for n, v in sorted(parametre.items()):
        log.skriv(f"       {n} = {v!r}")

    start = time.time()
    resultat = processing.run(alg_id, parametre, feedback=_feedback(log))
    log.skriv(f"     ({time.time() - start:.1f} s)")

    ud = parametre.get("OUTPUT") or parametre.get("output")
    if isinstance(ud, str) and not ud.startswith("TEMPORARY"):
        sti = Path(ud)
        if not sti.exists():
            raise OplandsFejl(f"{alg_id} meldte ikke fejl, men skabte ikke sit output: {sti}")
        if sti.stat().st_size == 0:
            raise OplandsFejl(f"{alg_id} skabte en tom fil: {sti}")
    return resultat


NODATA = -9999.0

# De otte naboretninger som (raekke, kolonne). Raekke voksende = mod syd.
_NABOER = {"NØ": (-1, 1), "Ø": (0, 1), "SØ": (1, 1), "S": (1, 0),
           "SV": (1, -1), "V": (0, -1), "NV": (-1, -1), "N": (-1, 0)}


def d8_afkodning(pointer_sti: Path, dem_sti: Path, log: Log) -> dict[int, tuple[int, int]]:
    """
    Udleder hvad hver vaerdi i D8-rasteret betyder — empirisk, ikke fra dokumentationen.

    Den dokumenterede WhiteboxTools-konvention (1=Ø, 2=NØ, 4=N, ...) passer IKKE paa dette
    build: verificeret paa Mollerup-data gav den 0,51 nedstroems-andel, altså rent moentkast,
    mens den faktiske kodning (1=NØ, 2=Ø, 4=SØ, 8=S, 16=SV, 32=V, 64=NV, 128=N) gav 1,0000.

    En forkert afkodning giver ikke en fejl — den giver et opland der ser plausibelt ud og er
    tilfaeldigt. Derfor udledes den her ved at finde den nabo der konsekvent ligger lavest, og
    resultatet verificeres bagefter.
    """
    import numpy as np
    from osgeo import gdal

    ds = gdal.Open(str(pointer_sti))
    pntr = ds.GetRasterBand(1).ReadAsArray().astype("int64")
    ds = None
    ds = gdal.Open(str(dem_sti))
    dem = ds.GetRasterBand(1).ReadAsArray().astype("float64")
    ds = None
    if pntr.shape != dem.shape:
        raise OplandsFejl("D8-raster og DEM har forskellige dimensioner")

    hoejde, bredde = pntr.shape
    r_idx, c_idx = np.indices(pntr.shape)
    rng = np.random.default_rng(0)
    afkodning: dict[int, tuple[int, int]] = {}

    for vaerdi in (v for v in np.unique(pntr) if v != 0):
        m = pntr == vaerdi
        idx = np.argwhere(m)
        if idx.shape[0] < 100:
            continue
        if idx.shape[0] > 200_000:
            idx = idx[rng.choice(idx.shape[0], 200_000, replace=False)]
        rr, cc = idx[:, 0], idx[:, 1]
        egen = dem[rr, cc]
        bedst, bedst_andel = None, -1.0
        for (dr, dc) in _NABOER.values():
            r2, c2 = rr + dr, cc + dc
            ok = (r2 >= 0) & (r2 < hoejde) & (c2 >= 0) & (c2 < bredde)
            nabo = np.full(egen.shape, np.inf)
            nabo[ok] = dem[r2[ok], c2[ok]]
            andel = float((nabo <= egen).mean())
            if andel > bedst_andel:
                bedst, bedst_andel = (dr, dc), andel
        afkodning[int(vaerdi)] = bedst

    # Selvkontrol: naesten alle celler skal stroemme til en celle paa eller under egen kote
    ned = np.full(pntr.size, -1, dtype=np.int64)
    for vaerdi, (dr, dc) in afkodning.items():
        m = pntr == vaerdi
        rr, cc = r_idx[m] + dr, c_idx[m] + dc
        ok = (rr >= 0) & (rr < hoejde) & (cc >= 0) & (cc < bredde)
        ned[(r_idx[m] * bredde + c_idx[m])[ok]] = (rr * bredde + cc)[ok]

    har = ned >= 0
    flad = dem.ravel()
    nedad = float((flad[ned[har]] <= flad[np.flatnonzero(har)]).mean())
    log.skriv(f"  D8-afkodning udledt, {nedad:.4f} af cellerne stroemmer nedad")
    if nedad < 0.999:
        raise OplandsFejl(
            f"D8-afkodningen kunne ikke fastlaegges paalideligt ({nedad:.4f} stroemmer nedad, "
            "forventet ~1,0). Uden korrekt afkodning bliver oplandene tilfaeldige."
        )
    return afkodning


def raster_regn(ud: Path, funk, log: Log, beskrivelse: str, **rastere: Path) -> Path:
    """
    Cellevis rasterberegning med eksplicit NoData-haandtering.

    Bevidst uden om processing: gdal:rastercalculator kalder gdal_calc gennem en shell, hvor
    '>' i formlen bliver til omdirigering paa Windows, og native:rastercalc propagerer NoData
    som 3.4e38 uden at melde fejl. Begge dele giver et resultat der ser plausibelt ud og er
    forkert. Her er NoData-reglen eksplicit: outputcellen er NoData hvis NOGEN input er det.

    Verificerer samtidig at alle input ligger paa samme grid — forskudte grids giver
    forskydninger paa under en celle, hvilket er nok til at al senere rastermatematik fejler.
    """
    import numpy as np
    from osgeo import gdal

    log.skriv(f"  -> rasterregning {beskrivelse}")

    vaerdier: dict[str, Any] = {}
    maske = None
    ref = None
    for navn, sti in rastere.items():
        ds = gdal.Open(str(sti))
        if ds is None:
            raise OplandsFejl(f"rasterregning: kunne ikke aabne {navn}: {sti}")
        if ref is None:
            ref = ds
        elif (ds.RasterXSize, ds.RasterYSize) != (ref.RasterXSize, ref.RasterYSize):
            raise OplandsFejl(
                f"rasterregning: {navn} har dimensionerne {ds.RasterXSize}x{ds.RasterYSize}, "
                f"men referencen har {ref.RasterXSize}x{ref.RasterYSize}. "
                "Rasteriser med base-parameteren, ikke med cell_size."
            )
        baand = ds.GetRasterBand(1)
        arr = baand.ReadAsArray().astype("float64")
        nd = baand.GetNoDataValue()
        if nd is not None:
            m = arr == nd
            if m.any():
                maske = m if maske is None else (maske | m)
        vaerdier[navn] = arr

    resultat = np.asarray(funk(**vaerdier), dtype="float32")
    if maske is not None:
        resultat[maske] = NODATA
        log.skriv(f"     {int(maske.sum()):,} NoData-celler foert igennem")

    drv = gdal.GetDriverByName("GTiff")
    ud_ds = drv.Create(str(ud), ref.RasterXSize, ref.RasterYSize, 1, gdal.GDT_Float32,
                       options=["COMPRESS=DEFLATE", "TILED=YES"])
    ud_ds.SetGeoTransform(ref.GetGeoTransform())
    ud_ds.SetProjection(ref.GetProjection())
    ud_baand = ud_ds.GetRasterBand(1)
    ud_baand.WriteArray(resultat)
    ud_baand.SetNoDataValue(NODATA)
    ud_ds = None

    if not ud.exists() or ud.stat().st_size == 0:
        raise OplandsFejl(f"rasterregning skabte ikke sit output: {ud}")
    return ud


def spring_over(output: Path, *input: Path) -> bool:
    """
    Sandt hvis output findes og er nyere end alle input. Kaeden indeholder trin der tager timer.

    Konfigurationen OG selve scriptet regnes altid med som input: aendres en parameter eller
    et beregningstrin, er mellemresultaterne foraeldede. Uden det bliver en kodeaendring
    stiltiende ignoreret, og man fejlsoeger paa output fra den forrige version.
    """
    if not output.exists():
        return False
    ud_tid = output.stat().st_mtime
    for i in (*input, AKTIV_KONFIG, Path(__file__).resolve()):
        if i.exists() and i.stat().st_mtime > ud_tid:
            return False
    return True


# ---------------------------------------------------------------------------
# Validering af input
# ---------------------------------------------------------------------------

def _kilde(sti: Path, lag: str | None) -> str:
    """
    Bygger en QGIS-datakildestreng; GeoPackages kraever lagnavn hvis der er flere lag.

    KUN til QGIS (QgsVectorLayer og processing). OGR forstaar ikke '|layername=' og aabner
    slet ikke strengen — brug _ogr_lag() dér.
    """
    return f"{sti}|layername={lag}" if lag else str(sti)


def _ogr_lag(sti: Path, lag: str | None):
    """
    Aabner et vektorlag med OGR.

    Returnerer (datakilde, lag) — og begge SKAL beholdes af kalderen. Frigives datakilden,
    bliver laget ugyldigt midt i brugen, og fejlen kommer et helt andet sted fra.
    """
    from osgeo import ogr
    ds = ogr.Open(str(sti))
    if ds is None:
        raise OplandsFejl(f"kunne ikke aabne {sti} med OGR")
    l = ds.GetLayerByName(lag) if lag else ds.GetLayer(0)
    if l is None:
        raise OplandsFejl(f"laget {lag!r} findes ikke i {sti}")
    return ds, l


def _qgs_geometri(ogr_geometri):
    """OGR-geometri til QgsGeometry via WKB, med WKT som reserve."""
    from qgis.core import QgsGeometry

    if ogr_geometri is None:
        return None
    g = QgsGeometry()
    g.fromWkb(bytes(ogr_geometri.ExportToWkb()))
    if g.isNull():
        g = QgsGeometry.fromWkt(ogr_geometri.ExportToWkt())
    return None if g.isNull() else g


def _frigiv(sti: Path) -> None:
    """
    Sletter en mellemfil foer den skrives igen.

    Kan den ikke slettes, holder noget den aabent, og saa er den naeste skrivning enten
    en fejl eller — vaerre — en stille blanding af to koersler. Det skal siges hoejt.
    """
    if not sti.exists():
        return
    try:
        sti.unlink()
    except OSError as e:
        raise OplandsFejl(
            f"kunne ikke slette mellemfilen {sti}: {e}\n"
            "Filen er aaben i et andet program — eller i QGIS' lagpanel. Fjern laget og "
            "koer igen; ellers ville resultatet blive en blanding af to koersler.") from e


def linjedele(geometri) -> list[list[tuple[float, float]]]:
    """
    Punktlisterne for hver enkelt linjedel i en OGR-geometri.

    Uundvaerlig, fordi `GetPointCount()` returnerer 0 paa en MultiLineString. Laeses samme
    linjelag fra en shapefil og fra en GeoPackage, kommer det foerste ud som LineString og
    det andet som MultiLineString — og en naiv gennemloebning taber saa hele laget uden at
    fejle. Praecis den fejl kostede en fuld koersel: 430 tilpasningslinjer blev talt som
    "manglende koter", selv om koterne var der.
    """
    from osgeo import ogr

    if geometri is None:
        return []
    navn = ogr.GeometryTypeToName(geometri.GetGeometryType()).lower()
    if "multi" in navn or "collection" in navn:
        dele = []
        for i in range(geometri.GetGeometryCount()):
            dele.extend(linjedele(geometri.GetGeometryRef(i)))
        return dele
    if geometri.GetPointCount() < 2:
        return []
    return [[geometri.GetPoint_2D(i) for i in range(geometri.GetPointCount())]]


def aabn_vektor(sti: Path, lag: str | None, navn: str, epsg: int, afvis: bool, log: Log):
    from qgis.core import QgsVectorLayer

    if not sti.exists():
        raise OplandsFejl(f"{navn}: filen findes ikke: {sti}")
    v = QgsVectorLayer(_kilde(sti, lag), navn, "ogr")
    if not v.isValid():
        raise OplandsFejl(f"{navn}: laget kunne ikke aabnes: {sti} (lag={lag})")
    if v.featureCount() == 0:
        raise OplandsFejl(f"{navn}: laget er tomt: {sti}")

    fundet = v.crs().authid()
    if fundet != f"EPSG:{epsg}":
        besked = f"{navn}: CRS er {fundet}, forventet EPSG:{epsg}"
        if afvis:
            raise OplandsFejl(besked + " (afvis_afvigende=true i konfigurationen)")
        log.advar(besked + " — reprojiceres ikke automatisk, ret kildedata")
    log.skriv(f"  {navn}: {v.featureCount()} objekter, {fundet}")
    return v


def aabn_raster(sti: Path, navn: str, epsg: int, afvis: bool, log: Log):
    from qgis.core import QgsRasterLayer

    if not sti.exists():
        raise OplandsFejl(f"{navn}: filen findes ikke: {sti}")
    r = QgsRasterLayer(str(sti), navn)
    if not r.isValid():
        raise OplandsFejl(f"{navn}: rasteret kunne ikke aabnes: {sti}")

    fundet = r.crs().authid()
    if fundet != f"EPSG:{epsg}":
        besked = f"{navn}: CRS er {fundet}, forventet EPSG:{epsg}"
        if afvis:
            raise OplandsFejl(besked + " (afvis_afvigende=true i konfigurationen)")
        log.advar(besked)

    opl = r.rasterUnitsPerPixelX()
    log.skriv(f"  {navn}: {r.width()}x{r.height()} celler, {opl:.3f} m, {fundet}")
    return r


# ---------------------------------------------------------------------------
# Trin 0 — klargoering
# ---------------------------------------------------------------------------

def trin0_klargoer(konf: dict, log: Log) -> Path:
    """
    Validerer input, fastlaegger analyseudstraekning og producerer analyserasteret.

    Bufferen omkring projektomraadet er et GAET. Trin 3 koerer edge_contamination og afgoer
    om den rakte — roerer oplandet rasterkanten, skal den op, og trin 0-3 koeres igen.
    """
    log.skriv("--- TRIN 0: klargoering ---")

    epsg = int(hent(konf, "crs.epsg"))
    afvis = bool(hent(konf, "crs.afvis_afvigende", True))
    derived = absolut(hent(konf, "output.derived_dir"))
    derived.mkdir(parents=True, exist_ok=True)

    projekt_sti = absolut(hent(konf, "input.projektomraade"))
    dem_sti = absolut(hent(konf, "input.dem"))

    projekt = aabn_vektor(projekt_sti, hent(konf, "input.projektomraade_lag", None),
                          "projektomraade", epsg, afvis, log)
    dem = aabn_raster(dem_sti, "dem", epsg, afvis, log)

    # Vandloeb og hydrologiske tilpasninger valideres her, saa fejl opdages foer de tunge trin.
    # Begge er valgfri for trin 0-3, men konsekvenserne er alvorlige nok til at raabe op om.
    if valgfri_sti(konf, "input.vandloeb") is None:
        log.advar(
            "INTET VANDLOEBSNETVAERK.\n"
            "     - vandloebsoplande kan ikke beregnes (trin 4), og dermed er direkte opland\n"
            "       heller ikke defineret: det ER totalopland minus vandloebsoplande\n"
            "     - ingen stream burning: i fladt terraen vandrer de beregnede vandloeb vaek\n"
            "       fra de faktiske, og oplandsgraenserne foelger med\n"
            "     Kun totaloplandet kan leveres. Skaf GeoDanmark vandloebsmidte eller VP3."
        )
    else:
        aabn_vektor(absolut(hent(konf, "input.vandloeb")), hent(konf, "input.vandloeb_lag", None),
                    "vandloeb", epsg, afvis, log)

    if bool(hent(konf, "input.dem_er_hydrologisk_tilpasset", False)):
        log.skriv("  DEM angivet som hydrologisk tilpasset — tilpasningslinjer forventes ikke")
    elif valgfri_sti(konf, "input.hydro_tilpasninger") is None:
        log.advar(
            "INGEN HYDROLOGISKE TILPASNINGER (DHM/Hydro).\n"
            "     Hver vejdaemning, bane og rundkoersel bliver et ubrudt vandskel, fordi\n"
            "     laserscanningen ser daemningen men ikke roeret under den. Oplandene bliver\n"
            "     systematisk for smaa, og graenserne foelger infrastruktur.\n"
            "     Det er ikke en unoejagtighed — det er et forkert resultat der ser rigtigt ud."
        )
    else:
        aabn_vektor(absolut(hent(konf, "input.hydro_tilpasninger")),
                    hent(konf, "input.hydro_tilpasninger_lag", None),
                    "hydro_tilpasninger", epsg, afvis, log)

    ud = derived / "00_dem_analyse.tif"
    if spring_over(ud, dem_sti, projekt_sti, AKTIV_KONFIG):
        log.skriv(f"  springer over, {ud.name} er opdateret")
        return ud

    # Analyseudstraekning. buffer_m = null betyder "brug hele terraenmodellen".
    # Det er det rigtige valg naar DEM'ens margener er asymmetriske: en symmetrisk buffer
    # skaeres ned til den mindste margen og smider dermed daekning vaek i de retninger hvor
    # oplandet faktisk straekker sig.
    from qgis.core import QgsRectangle

    dem_ext = dem.extent()
    buffer_raa = hent(konf, "analyse.buffer_m", None)

    if buffer_raa is None:
        ext = QgsRectangle(dem_ext)
        log.skriv(f"  analyseudstraekning: hele terraenmodellen, "
                  f"{ext.width():.0f} x {ext.height():.0f} m")
        for navn, margen in (("vest", projekt.extent().xMinimum() - dem_ext.xMinimum()),
                             ("oest", dem_ext.xMaximum() - projekt.extent().xMaximum()),
                             ("syd", projekt.extent().yMinimum() - dem_ext.yMinimum()),
                             ("nord", dem_ext.yMaximum() - projekt.extent().yMaximum())):
            log.skriv(f"     margen {navn:5s}: {margen:8.0f} m fra projektomraadet")
    else:
        buffer_m = float(buffer_raa)
        ext = projekt.extent()
        ext.grow(buffer_m)
        log.skriv(f"  analyseudstraekning: {ext.width():.0f} x {ext.height():.0f} m "
                  f"(buffer {buffer_m:.0f} m)")

        # DEM'en skal daekke hele analyseudstraekningen. Goer den ikke det, fylder warpen
        # stiltiende op med NoData, og oplandet bliver afskaaret uden fejlmeddelelse —
        # faldgrube 1 i CLAUDE.md. Det skal fejle her, ikke opdages i trin 3.
        if not dem_ext.contains(ext):
            mangler = {
                "vest": dem_ext.xMinimum() - ext.xMinimum(),
                "oest": ext.xMaximum() - dem_ext.xMaximum(),
                "syd": dem_ext.yMinimum() - ext.yMinimum(),
                "nord": ext.yMaximum() - dem_ext.yMaximum(),
            }
            for_lidt = ", ".join(f"{r} {v:.0f} m" for r, v in mangler.items() if v > 0)
            raise OplandsFejl(
                f"Terraenmodellen daekker ikke analyseudstraekningen. Mangler: {for_lidt}.\n"
                "Enten skaffes en stoerre DEM, saenkes analyse.buffer_m, eller saettes den til\n"
                "null for at bruge hele modellen. Klip ikke bare alligevel: oplandet bliver\n"
                "afskaaret ved rasterkanten uden fejlmeddelelse, og resultatet ser rigtigt ud."
            )

    # Snap til et rent grid ved analyseoploesningen, saa celleraendene ligger paa runde tal.
    # Det goer rasteriseringer i senere trin forudsigelige.
    maal = float(hent(konf, "analyse.oplaesning_m"))
    import math
    x0 = math.floor(ext.xMinimum() / maal) * maal
    y0 = math.floor(ext.yMinimum() / maal) * maal
    x1 = math.ceil(ext.xMaximum() / maal) * maal
    y1 = math.ceil(ext.yMaximum() / maal) * maal
    if buffer_raa is None:
        # Kryb indad, saa vi ikke beder om celler DEM'en ikke daekker
        x0, y0 = max(x0, math.ceil(dem_ext.xMinimum() / maal) * maal), \
                 max(y0, math.ceil(dem_ext.yMinimum() / maal) * maal)
        x1, y1 = min(x1, math.floor(dem_ext.xMaximum() / maal) * maal), \
                 min(y1, math.floor(dem_ext.yMaximum() / maal) * maal)

    kilde_opl = dem.rasterUnitsPerPixelX()
    agg_type = str(hent(konf, "analyse.aggregering", "mean")).lower()
    # gdal:warpreproject RESAMPLING: 5=Average, 8=Minimum (verificeret mod registret)
    resampling = {"mean": 5, "minimum": 8, "min": 8, "maximum": 7, "median": 9}
    if agg_type not in resampling:
        raise OplandsFejl(f"Ukendt aggregering: {agg_type}. Vaelg en af {sorted(resampling)}")
    if agg_type in ("min", "minimum"):
        log.advar("aggregering=minimum bevarer groefter, men opfinder kanaler hvor der ingen er")

    # Warp frem for aggregate_raster: den streamer (Whitebox laeser hele rasteret i RAM, og
    # kilden her er 4 GB), haandterer skaeve kildeoploesninger, og giver et rent maalgrid.
    warp = derived / "00_dem_warp.tif"
    koer("gdal:warpreproject", {
        "INPUT": str(dem_sti),
        "SOURCE_CRS": f"EPSG:{epsg}",
        "TARGET_CRS": f"EPSG:{epsg}",
        "RESAMPLING": resampling[agg_type],
        "NODATA": NODATA,
        "TARGET_RESOLUTION": maal,
        "TARGET_EXTENT": f"{x0},{x1},{y0},{y1} [EPSG:{epsg}]",
        "DATA_TYPE": 6,          # Float32
        "MULTITHREADING": True,
        "CREATION_OPTIONS": "COMPRESS=DEFLATE|TILED=YES",
        "OUTPUT": str(warp),
    }, log, f"resample {kilde_opl:.4f} m -> {maal:.2f} m ({agg_type}) og klip")

    if bool(hent(konf, "analyse.maskeer_vandflader", True)):
        log.skriv("  -> maskeer vandflader (recipient, ikke opland)")
        # Rasteriser projektomraadet paa analysegridet, saa det kan undtages fra maskeringen
        from osgeo import gdal as _gdal, ogr as _ogr
        _ds = _gdal.Open(str(warp))
        _gt, _w, _h = _ds.GetGeoTransform(), _ds.RasterXSize, _ds.RasterYSize
        _ds = None
        _mem = _gdal.GetDriverByName("MEM").Create("", _w, _h, 1, _gdal.GDT_Byte)
        _mem.SetGeoTransform(_gt)
        _src, _src_lag = _ogr_lag(projekt_sti, hent(konf, "input.projektomraade_lag", None))
        _gdal.RasterizeLayer(_mem, [1], _src_lag, burn_values=[1])
        _projmaske = _mem.GetRasterBand(1).ReadAsArray().astype(bool)
        _mem = None
        _src = None
        maskeer_vandflader(warp, ud, konf, log, undtag=_projmaske)
    else:
        log.advar("vandflademaskering slaaet fra — breaching kan blive uhaandterligt langsom "
                  "hvis omraadet indeholder fjord eller soe")
        shutil.copyfile(warp, ud)

    r = aabn_raster(ud, "dem_analyse", epsg, False, log)
    log.skriv(f"  analyseraster: {r.width():,} x {r.height():,} = "
              f"{r.width() * r.height():,} celler")
    return ud


# ---------------------------------------------------------------------------
# Trin 1 — hydrologisk konditionering
# ---------------------------------------------------------------------------

def _rasteriser_linjer(vektor_sti: Path, lag: str | None, base: Path, ud: Path,
                       log: Log, hvad: str) -> Path:
    """
    Rasteriserer et linjelag paa analysegridet.

    base-parameteren er vigtig: saettes cell_size i stedet, bliver rasteret forskudt,
    og al senere rastermatematik fejler stille.
    """
    koer(kraev_algoritme("vector_lines_to_raster"), {
        "input": _kilde(vektor_sti, lag),
        "base": str(base),
        "zero_background": True,
        "output": str(ud),
    }, log, f"rasteriser {hvad}")
    return ud


def maskeer_vandflader(dem_sti: Path, ud: Path, konf: dict, log: Log,
                       undtag: "np.ndarray | None" = None) -> Path:
    """
    Saetter store, lave, helt flade vandoverflader til NoData.

    Hydrologisk begrundelse: fjord og hav er recipient, ikke opland. Vand skal ikke rutes hen
    over en vandoverflade, og NoData fungerer som udloeb — stroemningen terminerer korrekt der.

    Praktisk begrundelse: breaching kvaeles i store flader. Maalt paa dette datasaet steg
    koeretiden fra 0,4 s (stoerste flade 724 celler) til 588 s (138.028 celler), og det fulde
    raster med en flade paa 6,7 mio. celler naaede aldrig igennem. Koeretiden foelger fladens
    stoerrelse, ikke rasterets.

    Kriterierne er bevidst snaevre — helt flad OG lav OG stor — saa lavtliggende land ikke
    rammes. Reelt terraen er aldrig fladt til under en millimeter over 3x3 celler.
    """
    import numpy as np
    from osgeo import gdal
    from scipy import ndimage

    maks_kote = float(hent(konf, "analyse.vandflade_maks_kote_m", 1.0))
    min_areal_ha = float(hent(konf, "analyse.vandflade_min_areal_ha", 5.0))
    relief_tol = float(hent(konf, "analyse.vandflade_relief_m", 0.001))

    ds = gdal.Open(str(dem_sti))
    gt = ds.GetGeoTransform()
    bredde, hoejde = ds.RasterXSize, ds.RasterYSize
    baand = ds.GetRasterBand(1)
    dem = baand.ReadAsArray().astype("float64")
    nodata_ind = baand.GetNoDataValue()
    proj = ds.GetProjection()
    ds = None

    res = gt[1]
    celle_ha = res * res / 1e4
    gyldig = np.ones(dem.shape, bool) if nodata_ind is None else (dem != nodata_ind)

    mx = np.full(dem.shape, -np.inf)
    mn = np.full(dem.shape, np.inf)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            n = np.roll(np.roll(dem, di, axis=0), dj, axis=1)
            mx = np.maximum(mx, n)
            mn = np.minimum(mn, n)
    kandidat = ((mx - mn) <= relief_tol) & (dem <= maks_kote) & gyldig

    # Projektomraadet maa aldrig maskeres. Ligger der en soe i det — og det goer der ofte i
    # vaadomraadeprojekter — er den en del af analysens maal, ikke en recipient at kaste vaek.
    if undtag is not None and undtag.any():
        ramt_i_projekt = kandidat & undtag
        if ramt_i_projekt.any():
            log.skriv(f"  {int(ramt_i_projekt.sum()):,} flade celler ligger i projektomraadet "
                      "og maskeres ikke")
            kandidat &= ~undtag

    maerket, antal = ndimage.label(kandidat)
    if antal == 0:
        log.skriv("  ingen vandflader fundet — DEM kopieres uaendret")
        shutil.copyfile(dem_sti, ud)
        return ud

    stoerrelser = np.bincount(maerket.ravel())
    stoerrelser[0] = 0
    min_celler = int(min_areal_ha / celle_ha)
    store = np.flatnonzero(stoerrelser >= min_celler)
    maske = np.isin(maerket, store) if store.size else np.zeros(dem.shape, bool)

    # Bemaerk: her maa IKKE returneres tidligt selv om intet maskeres — vippet nedenfor er
    # det vigtigste af de to, og springes det over, staar de flade vandflader tilbage.
    if maske.any():
        log.skriv(f"  vandflader maskeret: {int(maske.sum()):,} celler = "
                  f"{maske.sum() * celle_ha:,.1f} ha ({100.0 * maske.mean():.1f} % af rasteret)")
        log.skriv(f"     {store.size} sammenhaengende flade(r) over {min_areal_ha} ha, "
                  f"stoerste {stoerrelser.max() * celle_ha:,.1f} ha")
        log.skriv(f"     koter i det maskerede: "
                  f"{dem[maske].min():.2f} .. {dem[maske].max():.2f} m")
    else:
        log.skriv(f"  ingen flade over {min_areal_ha:,.0f} ha maskeres — alle vandflader vippes")

    resultat = np.where(maske, NODATA, dem).astype("float64")

    # De vandflader der IKKE maskeres — typisk indlandssoeer — vippes umaerkeligt mod deres
    # udloeb i stedet for at blive skaaret vaek.
    #
    # Begrundelsen er baade hydrologisk og praktisk. Hydrologisk: en soe opstroems modtager
    # vand og sender det videre, saa maskerer man den, brydes stroemningsvejen og hele oplandet
    # ovenfor falder ud. Praktisk: en helt flad soe er lige saa patologisk for breaching som
    # fjorden — maalt her tog breaching 9 s med alle flader maskeret og over 10 min med soeer
    # paa 12-131 ha tilbage.
    #
    # Vippet er millimeterstort og aendrer ikke oplandsgraenserne, men fjerner den vilkaarlige
    # stroemningsretning der goer flader dyre.
    vip = float(hent(konf, "analyse.vandflade_vip_m", 0.01))
    smaa = [i for i in np.flatnonzero(stoerrelser > 0)
            if i not in set(store.tolist()) and stoerrelser[i] * celle_ha >= 1.0]
    if vip > 0 and smaa:
        kant = np.zeros(dem.shape, bool)
        kant[0, :] = kant[-1, :] = kant[:, 0] = kant[:, -1] = True
        udsnit = ndimage.find_objects(maerket)
        vippet_ialt, via_kant = 0, 0
        for i in smaa:
            sl = udsnit[i - 1]
            if sl is None:
                continue
            # arbejd i fladens bounding box med en celles margen
            r0 = max(sl[0].start - 1, 0); r1 = min(sl[0].stop + 1, dem.shape[0])
            c0 = max(sl[1].start - 1, 0); c1 = min(sl[1].stop + 1, dem.shape[1])
            lokal = maerket[r0:r1, c0:c1] == i
            lokal_dem = dem[r0:r1, c0:c1]
            lokal_kant = kant[r0:r1, c0:c1]

            # Roerer fladen rasterkanten, ER kanten udloebet — det er dér vandet forlader
            # analyseomraadet. Ellers er udloebet den laveste nabocelle uden for fladen.
            froe = np.ones(lokal.shape, bool)
            if (lokal & lokal_kant).any():
                froe[lokal & lokal_kant] = False
                via_kant += 1
            else:
                rand = ndimage.binary_dilation(lokal) & ~lokal & (lokal_dem > -9000)
                if not rand.any():
                    continue
                idx = np.argmin(np.where(rand, lokal_dem, np.inf))
                froe[np.unravel_index(idx, lokal.shape)] = False

            afstand = ndimage.distance_transform_edt(froe)
            d_max = afstand[lokal].max() or 1.0
            lokal_resultat = resultat[r0:r1, c0:c1]
            lokal_resultat[lokal] = lokal_dem[lokal] + vip * (afstand[lokal] / d_max)
            resultat[r0:r1, c0:c1] = lokal_resultat
            vippet_ialt += int(lokal.sum())

        log.skriv(f"  vandflader vippet mod udloeb: {vippet_ialt:,} celler = "
                  f"{vippet_ialt * celle_ha:,.1f} ha i {len(smaa)} flade(r), fald {vip} m"
                  + (f" ({via_kant} vippet mod rasterkanten)" if via_kant else ""))

    resultat = resultat.astype("float32")
    ud_ds = gdal.GetDriverByName("GTiff").Create(
        str(ud), bredde, hoejde, 1, gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES"])
    ud_ds.SetGeoTransform(gt)
    ud_ds.SetProjection(proj)
    ud_baand = ud_ds.GetRasterBand(1)
    ud_baand.WriteArray(resultat)
    ud_baand.SetNoDataValue(NODATA)
    ud_ds = None
    return ud


def braend_tilpasningskoter(dem_sti: Path, linje_sti: Path, lag: str | None,
                            ud: Path, konf: dict, log: Log) -> Path:
    """
    Paatrykker DHM-tilpasningslagets koter paa terraenmodellen.

    DHMLinje angiver for hver linje en START_Z og en END_Z; terraenet skal foelge en lineaer
    profil mellem dem. Koterne ERSTATTER DEM'ens vaerdier — de traekkes ikke fra. Omkring 6 %
    af tilpasningerne i Mollerup-omraadet HAEVER terraenet (daemningsreparationer der lukker et
    falsk gennembrud), og en min()-operation ville tabe dem.

    Det afloeser den tidligere braending med en gaettet fast dybde: her kommer koterne fra
    datasaettet, saa der er ingen parameter at vaelge forkert.
    """
    import numpy as np
    from osgeo import gdal, ogr, osr

    SENTINEL = -99999.0

    ds = gdal.Open(str(dem_sti))
    gt = ds.GetGeoTransform()
    bredde, hoejde = ds.RasterXSize, ds.RasterYSize
    dem_baand = ds.GetRasterBand(1)
    dem = dem_baand.ReadAsArray().astype("float64")
    nodata_ind = dem_baand.GetNoDataValue()
    proj = ds.GetProjection()
    ds = None

    x0, y1 = gt[0], gt[3]
    res = gt[1]
    x1, y0 = x0 + bredde * res, y1 - hoejde * res

    felt_start = str(hent(konf, "konditionering.tilpasning_felt_start", "START_Z"))
    felt_slut = str(hent(konf, "konditionering.tilpasning_felt_slut", "END_Z"))
    medtag_usikre = bool(hent(konf, "konditionering.tilpasning_medtag_usikre", True))

    kilde, src_lag = _ogr_lag(linje_sti, lag)
    for felt in (felt_start, felt_slut):
        if src_lag.GetLayerDefn().GetFieldIndex(felt) < 0:
            raise OplandsFejl(
                f"tilpasningslaget mangler feltet {felt}. Fundne felter: "
                + ", ".join(src_lag.GetLayerDefn().GetFieldDefn(i).GetName()
                            for i in range(src_lag.GetLayerDefn().GetFieldCount()))
            )
    src_lag.SetSpatialFilterRect(x0, y0, x1, y1)

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(hent(konf, "crs.epsg")))
    mem = ogr.GetDriverByName("MEM").CreateDataSource("")
    lag3d = mem.CreateLayer("tilpasninger", srs, ogr.wkbLineString25D)

    antal, sprunget, usikre = 0, 0, 0
    src_lag.ResetReading()
    for f in src_lag:
        z_start, z_slut = f.GetField(felt_start), f.GetField(felt_slut)
        if z_start is None or z_slut is None or z_start < -1000 or z_slut < -1000:
            sprunget += 1
            continue
        navn = (f.GetField("LAYER_NAME") or "") if \
            src_lag.GetLayerDefn().GetFieldIndex("LAYER_NAME") >= 0 else ""
        if "uncertain" in navn.lower():
            usikre += 1
            if not medtag_usikre:
                continue

        dele = linjedele(f.GetGeometryRef())
        if not dele:
            sprunget += 1
            continue

        # Interpolér koten langs linjen efter tilbagelagt afstand. Er linjen flerdelt,
        # faar hver del hele profilet fra start- til slutkote — delene er stykker af den
        # samme underfoering, og der er ingen oplysning om hvor snittene ligger i profilet.
        for punkter in dele:
            afstande = [0.0]
            for i in range(1, len(punkter)):
                (ax, ay), (bx, by) = punkter[i - 1], punkter[i]
                afstande.append(afstande[-1] + ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
            samlet = afstande[-1] or 1.0

            linje = ogr.Geometry(ogr.wkbLineString25D)
            for (px, py), d in zip(punkter, afstande):
                linje.AddPoint(px, py, z_start + (z_slut - z_start) * (d / samlet))

            ny = ogr.Feature(lag3d.GetLayerDefn())
            ny.SetGeometry(linje)
            lag3d.CreateFeature(ny)
        antal += 1

    i_udsnit = src_lag.GetFeatureCount()
    log.skriv(f"  tilpasningslinjer i udstraekningen: {antal}"
              + (f", heraf {usikre} markeret 'uncertain'" if usikre else "")
              + (f" ({sprunget} sprunget over, manglende koter)" if sprunget else ""))
    if antal == 0:
        # Fejlbeskeden skal selv kunne afgoere hvilken af de tre aarsager det er, uden at
        # nogen skal grave i logfilen bagefter.
        src_lag.SetSpatialFilterRect(-1e15, -1e15, 1e15, 1e15)
        lag_ext = src_lag.GetExtent()
        overlap = not (lag_ext[1] < x0 or lag_ext[0] > x1
                       or lag_ext[3] < y0 or lag_ext[2] > y1)
        linjer = [f"Ingen brugbare tilpasningslinjer inden for analyseudstraekningen.",
                  f"    analyseudstraekning : {x0:.0f} {y0:.0f} .. {x1:.0f} {y1:.0f} "
                  f"(EPSG:{hent(konf, 'crs.epsg')})",
                  f"    tilpasningslaget    : {lag_ext[0]:.0f} {lag_ext[2]:.0f} .. "
                  f"{lag_ext[1]:.0f} {lag_ext[3]:.0f}, {src_lag.GetFeatureCount()} linjer",
                  f"    linjer i udsnittet  : {i_udsnit}, heraf {sprunget} uden brugbare "
                  f"koter i {felt_start}/{felt_slut}"]
        if not overlap:
            linjer.append("    De to udstraekninger OVERLAPPER IKKE. Enten daekker laget "
                          "ikke omraadet, eller ogsaa er det i et andet koordinatsystem.")
        elif i_udsnit == 0:
            linjer.append("    Udstraekningerne overlapper, men der ligger ingen linjer i "
                          "netop dette udsnit. Det kan vaere rigtigt for et lille omraade.")
        else:
            linjer.append(f"    Alle {i_udsnit} linjer i udsnittet blev sprunget over. "
                          f"Hedder kotefelterne {felt_start} og {felt_slut}, eller staar "
                          "koterne som -9999?")
        linjer.append("    Analysen kan koere uden tilpasningslinjer — lad feltet staa tomt "
                      "under Avanceret — men saa bliver hver vejdaemning et kunstigt "
                      "vandskel, og oplandene systematisk for smaa.")
        kilde = None
        raise OplandsFejl("\n".join(linjer))
    kilde = None

    # BURN_VALUE_FROM=Z interpolerer koten mellem knudepunkterne langs linjen.
    # burn_values=[0] er ikke valgfri: Z LAEGGES til braendevaerdien, og udelades den,
    # bruger GDAL 255 som standard — saa bliver resultatet 255 + kote uden nogen fejl.
    maal = gdal.GetDriverByName("MEM").Create("", bredde, hoejde, 1, gdal.GDT_Float64)
    maal.SetGeoTransform(gt)
    maal.SetProjection(proj)
    baand = maal.GetRasterBand(1)
    baand.Fill(SENTINEL)
    gdal.RasterizeLayer(maal, [1], lag3d, burn_values=[0], options=["BURN_VALUE_FROM=Z"])
    tilp = baand.ReadAsArray().astype("float64")
    maal = None
    mem = None

    # Tilpasninger maa ikke skrives ind i maskerede celler: nogle sluse- og broliner ligger
    # ude paa vandfladen, og et paatryk dér ville genindfoere terraen midt i recipienten.
    ramt = tilp != SENTINEL
    if nodata_ind is not None:
        paa_nodata = ramt & (dem == nodata_ind)
        if paa_nodata.any():
            log.skriv(f"  {int(paa_nodata.sum()):,} tilpasningsceller ligger paa maskeret "
                      "vandflade og springes over")
            ramt &= ~paa_nodata

    if not ramt.any():
        raise OplandsFejl("Tilpasningslinjerne ramte ingen brugbare celler ved rasteriseringen")

    aendring = tilp[ramt] - dem[ramt]
    log.skriv(f"  celler paatrykt tilpasningskoter: {int(ramt.sum()):,}")
    log.skriv(f"     saenket: {int((aendring < -0.05).sum()):,}   "
              f"haevet: {int((aendring > 0.05).sum()):,}   "
              f"uaendret: {int((np.abs(aendring) <= 0.05).sum()):,}")
    log.skriv(f"     median {np.median(aendring):+.2f} m, "
              f"yderpunkter {aendring.min():+.2f} / {aendring.max():+.2f} m")

    resultat = np.where(ramt, tilp, dem).astype("float32")

    ud_ds = gdal.GetDriverByName("GTiff").Create(
        str(ud), bredde, hoejde, 1, gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES"])
    ud_ds.SetGeoTransform(gt)
    ud_ds.SetProjection(proj)
    ud_baand = ud_ds.GetRasterBand(1)
    ud_baand.WriteArray(resultat)
    ud_baand.SetNoDataValue(NODATA)
    ud_ds = None
    return ud


def braend_vandloeb_monotont(dem_sti: Path, linje_sti: Path, lag: str | None,
                             ud: Path, konf: dict, log: Log) -> Path:
    """
    Braender det kortlagte vandloebsnetvaerk ind som GARANTEREDE stroemningsveje.

    Naiv braending — traek en fast dybde fra langs linjen — virker ikke: den bevarer terraenets
    smaa stigninger undervejs, og de modfaldsstraekninger fylder fill_depressions bagefter, saa
    kanalen doer. Maalt paa AIS-netvaerket baar kun 22 % af de kortlagte celler vand.

    Her paatvinges i stedet et monotont faldende profil langs hver linje: koten kan aldrig stige
    i nedstroems retning, og der kraeves et minimumsfald pr. meter. Saa ER linjen en
    sammenhaengende stroemningsvej, uanset hvad terraenmodellen mener lokalt.

    Stroemningsretningen bestemmes af terraenet i linjens to ender, ikke af digitaliseringens
    retning — den er vilkaarlig i de fleste datasaet.
    """
    import numpy as np
    from osgeo import gdal, ogr, osr

    SENTINEL = 1e20
    dybde = float(hent(konf, "konditionering.stream_burn_dybde_m", 1.0))
    fald_pr_m = float(hent(konf, "konditionering.stream_burn_fald_pr_m", 0.0001))

    ds = gdal.Open(str(dem_sti))
    gt = ds.GetGeoTransform()
    bredde, hoejde = ds.RasterXSize, ds.RasterYSize
    dem_baand = ds.GetRasterBand(1)
    dem = dem_baand.ReadAsArray().astype("float64")
    nodata_ind = dem_baand.GetNoDataValue()
    proj = ds.GetProjection()
    ds = None

    x0, y1, res = gt[0], gt[3], gt[1]
    x1, y0 = x0 + bredde * res, y1 - hoejde * res

    # "Kapring": profilet laegges paa det LAVESTE terraen inden for en radius, ikke paa
    # linjens eget terraen. Ligger AIS-linjen paa en skraent nogle meter over den virkelige
    # dalbund, er en rende paa 1 m under linjen stadig hoejere end dalen — og saa bliver
    # vandet i dalen. Med kapring laegges renden i dalbundens niveau, saa den bliver det
    # laveste punkt i omraadet og opsamler afstroemningen.
    kapre_m = float(hent(konf, "konditionering.stream_burn_kapre_m", 0.0))
    if kapre_m > 0:
        from scipy import ndimage
        rad = max(int(round(kapre_m / res)), 1)
        profil_dem = ndimage.minimum_filter(dem, size=2 * rad + 1, mode="nearest")
        log.skriv(f"  kapring: profilet laegges paa laveste terraen inden for {kapre_m:.0f} m")
    else:
        profil_dem = dem

    def kote(px, py):
        c, r = int((px - x0) / res), int((y1 - py) / res)
        if 0 <= r < hoejde and 0 <= c < bredde:
            v = profil_dem[r, c]
            if nodata_ind is None or v != nodata_ind:
                return float(v)
        return None

    kilde, src_lag = _ogr_lag(linje_sti, lag)
    src_lag.SetSpatialFilterRect(x0, y0, x1, y1)

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(hent(konf, "crs.epsg")))

    braend = np.full(dem.shape, SENTINEL)
    mem_ras = gdal.GetDriverByName("MEM").Create("", bredde, hoejde, 1, gdal.GDT_Float64)
    mem_ras.SetGeoTransform(gt)
    mem_ras.SetProjection(proj)

    antal, vendt, sprunget = 0, 0, 0
    src_lag.ResetReading()
    for f in src_lag:
        # Hver del af et flerdelt forloeb behandles for sig. Delene haenger ikke fysisk
        # sammen, saa ét faelles monotont profil ville paatvinge et fald hen over springet
        # mellem dem og grave en kanal der ikke findes.
        brugt = False
        for pkt in linjedele(f.GetGeometryRef()):
            z_terr = [kote(px, py) for px, py in pkt]
            if sum(z is not None for z in z_terr) < 2:
                continue
            # huller udfyldes med naboens kote, saa profilet er komplet
            for i, z in enumerate(z_terr):
                if z is None:
                    foer = next((v for v in z_terr[i::-1] if v is not None), None)
                    efter = next((v for v in z_terr[i:] if v is not None), None)
                    z_terr[i] = foer if foer is not None else efter

            # Retningen afgoeres af terraenet i enderne, ikke af digitaliseringsretningen
            k = max(len(z_terr) // 10, 1)
            if float(np.mean(z_terr[:k])) < float(np.mean(z_terr[-k:])):
                pkt = list(reversed(pkt)); z_terr.reverse(); vendt += 1

            # Monotont faldende profil med paakraevet minimumsfald
            z_ny = [z_terr[0]]
            for i in range(1, len(pkt)):
                (ax, ay), (bx, by) = pkt[i - 1], pkt[i]
                seg = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
                loft = z_ny[-1] - fald_pr_m * seg
                z_ny.append(min(z_terr[i], loft))

            linje = ogr.Geometry(ogr.wkbLineString25D)
            for (px, py), z in zip(pkt, z_ny):
                linje.AddPoint(px, py, z - dybde)

            mem_vek = ogr.GetDriverByName("MEM").CreateDataSource("")
            vlag = mem_vek.CreateLayer("l", srs, ogr.wkbLineString25D)
            nyf = ogr.Feature(vlag.GetLayerDefn())
            nyf.SetGeometry(linje)
            vlag.CreateFeature(nyf)

            mem_ras.GetRasterBand(1).Fill(SENTINEL)
            gdal.RasterizeLayer(mem_ras, [1], vlag, burn_values=[0],
                                options=["BURN_VALUE_FROM=Z"])
            denne = mem_ras.GetRasterBand(1).ReadAsArray()
            # Flere vandloeb kan dele celler ved tiloeb — den laveste kote vinder
            braend = np.minimum(braend, denne)
            mem_vek = None
            brugt = True
        if brugt:
            antal += 1
        else:
            sprunget += 1

    kilde = None
    mem_ras = None

    ramt = braend < SENTINEL / 2
    if nodata_ind is not None:
        ramt &= dem != nodata_ind
    if not ramt.any():
        raise OplandsFejl(
            f"Vandloebsbraendingen ramte ingen celler: {antal} forloeb blev laest, men ingen "
            "af dem faldt paa analyserasteret.\n"
            "Daekker vandloebslaget overhovedet dette omraade? Et landsdelsdaekkende lag "
            "(fx AIS for Jylland) er tomt i resten af landet, og feltet er forudfyldt — "
            "saa det ser rigtigt ud lige indtil her.")

    # Tragt omkring linjen (AGREE-princippet). Uden den graver braendingen en 1 celle bred
    # slidse, og ligger den virkelige dal 10-50 m derfra, bliver den ikke fanget: vandet
    # bliver i sin egen dal, og man ser to parallelle vandveje. Tragten haelder terraenet
    # jaevnt mod linjen inden for en korridor, saa nabodalen draenes ind i det kortlagte forloeb.
    korridor_m = float(hent(konf, "konditionering.stream_burn_korridor_m", 0.0))
    korridor_dybde = float(hent(konf, "konditionering.stream_burn_korridor_dybde_m", 0.0))
    if korridor_m > 0 and korridor_dybde > 0:
        from scipy import ndimage
        afstand = ndimage.distance_transform_edt(~ramt, sampling=res)
        tragt = np.clip(1.0 - afstand / korridor_m, 0.0, 1.0)
        i_korridor = tragt > 0
        if nodata_ind is not None:
            i_korridor &= dem != nodata_ind
        dem = np.where(i_korridor, dem - korridor_dybde * tragt, dem)
        # Braendingen skal ligge UNDER tragtens bund. Ellers vinder tragten paa selve linjen,
        # og det monotone profil — hele pointen — bliver overskrevet af terraenets egen form.
        braend = np.where(ramt, braend - korridor_dybde, braend)
        log.skriv(f"     tragt: {int(i_korridor.sum()):,} celler inden for {korridor_m:.0f} m "
                  f"haeldes op til {korridor_dybde:.1f} m mod linjen")

    log.skriv(f"  vandloeb braendt: {antal} forloeb ({vendt} vendt efter terraen"
              + (f", {sprunget} sprunget over" if sprunget else "") + ")")
    saenkning = dem[ramt] - braend[ramt]
    log.skriv(f"     {int(ramt.sum()):,} celler, saenkning median {np.median(saenkning):.2f} m, "
              f"maks {saenkning.max():.2f} m")

    # Braending maa kun saenke — aldrig loefte terraenet
    resultat = np.where(ramt, np.minimum(dem, braend), dem).astype("float32")

    ud_ds = gdal.GetDriverByName("GTiff").Create(
        str(ud), bredde, hoejde, 1, gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES"])
    ud_ds.SetGeoTransform(gt)
    ud_ds.SetProjection(proj)
    ud_baand = ud_ds.GetRasterBand(1)
    ud_baand.WriteArray(resultat)
    if nodata_ind is not None:
        ud_baand.SetNoDataValue(nodata_ind)
    ud_ds = None
    return ud


def trin1_konditioner(konf: dict, dem_analyse: Path, log: Log) -> Path:
    """
    Braend tilpasninger -> stream burning -> breaching -> fill.

    Raekkefoelgen er ikke ombyttelig. Braendes vandloebene efter breaching, graver breaching
    kanaler der konkurrerer med de braendte. Fyldes foer breaching, er der ingen depressioner
    tilbage at grave igennem, og resultatet bliver identisk med ren fill.
    """
    log.skriv("--- TRIN 1: hydrologisk konditionering ---")

    derived = absolut(hent(konf, "output.derived_dir"))
    ud = derived / "01_dem_hydro.tif"
    # Vandloeb og tilpasninger regnes med som input: aendres selve filen uden at stien goer
    # det, ville konditioneringen ellers blive genbrugt paa et forkert grundlag.
    kilder = [dem_analyse, AKTIV_KONFIG]
    kilder += [p for p in (valgfri_sti(konf, "input.vandloeb"),
                           valgfri_sti(konf, "input.hydro_tilpasninger")) if p]
    if spring_over(ud, *kilder):
        log.skriv(f"  springer over, {ud.name} er opdateret")
        return ud

    aktuel = dem_analyse

    # 1a. Braend hydrologiske tilpasninger (broer, roerunderfoeringer, vejdaemninger).
    #     Uden dem bliver hver vej og bane et kunstigt vandskel.
    tilp_sti = valgfri_sti(konf, "input.hydro_tilpasninger")
    if bool(hent(konf, "input.dem_er_hydrologisk_tilpasset", False)):
        log.skriv("  DEM er allerede hydrologisk tilpasset — braending springes over "
                  "(ellers saenkes terraenet to gange)")
    elif tilp_sti is None:
        log.advar("ingen hydrologiske tilpasninger — infrastruktur bliver kunstige vandskel")
    else:
        tilpasset = derived / "01_dem_tilpasset.tif"
        log.skriv("  -> paatryk DHM-tilpasningskoter")
        braend_tilpasningskoter(
            aktuel, tilp_sti, hent(konf, "input.hydro_tilpasninger_lag", None),
            tilpasset, konf, log)
        aktuel = tilpasset

    # 1b. Stream burning: saenk DEM'en langs det kortlagte vandloebsnetvaerk, saa det beregnede
    #     netvaerk foelger det virkelige. Uden dette vandrer beregnede vandloeb vaek fra de
    #     faktiske i fladt terraen, og oplandsgraenserne foelger med.
    #
    #     Whitebox' fill_burn (Saunders) bruges IKKE, selv om den findes i registret:
    #     wrapperen erklaerer 'streams' som RasterLayer, mens backenden kraever en vektor, saa
    #     kaldet er umuligt at faa igennem. Kaldes backenden direkte, afviser den med
    #     "no stream cells were rasterized" trods korrekt overlap og faelles EPSG:25832.
    #     Konstantdybde-braending her giver samme effekt paa oplandsgraenserne: den efterfoelgende
    #     breaching retter de modfaldsstraekninger som Saunders' algoritme ellers ville udjaevne,
    #     og dybden bliver en parameter man kan se og justere frem for en black box.
    vl_sti = valgfri_sti(konf, "input.vandloeb")
    if not bool(hent(konf, "konditionering.stream_burn", True)):
        log.skriv("  stream burning slaaet fra i konfigurationen")
    elif vl_sti is None:
        log.advar("intet vandloebsnetvaerk — ingen stream burning. De beregnede vandloeb "
                  "bestemmes alene af terraenet og kan afvige fra de faktiske i fladt land")
    else:
        burn = derived / "01_dem_burn.tif"
        log.skriv("  -> stream burning med paatvunget monotont fald")
        braend_vandloeb_monotont(
            aktuel, vl_sti, hent(konf, "input.vandloeb_lag", None), burn, konf, log)
        aktuel = burn

    # 1c. Breaching foer fill. Ren fill laver plateauer med vilkaarlig stroemningsretning,
    #     og i dansk lavland bliver oplandsgraenserne saa algoritmeartefakter.
    breach = derived / "01_dem_breach.tif"
    breach_param = {
        "dem": str(aktuel),
        "max_dist": int(hent(konf, "konditionering.breach_dist_celler", 100)),
        "flat_increment": float(hent(konf, "konditionering.flat_increment", 0.0001)),
        "fill_deps": bool(hent(konf, "konditionering.breach_fill_rest", True)),
        "minimize_dist": False,
        "output": str(breach),
    }
    maks_cost = hent(konf, "konditionering.breach_max_cost", None)
    if maks_cost is not None:
        breach_param["max_cost"] = float(maks_cost)
    koer(kraev_algoritme("breach_depressions_least_cost"), breach_param, log,
         "breach depressions least-cost")
    aktuel = breach

    # 1d. Fill som sidste sikkerhedsnet, saa DEM'en garanteret er draenende.
    if bool(hent(konf, "konditionering.fill_til_sidst", True)):
        # flat_resolution: 0=garbrecht_martz, 1=natural. Default i vaerktoejet er 0, og den
        # efterlader lavninger staaende: maalt paa dette datasaet 37.098 pits mod 0 med
        # 'natural'. Sluk betyder afbrudt afvanding, og saa bliver oplandene for smaa.
        koer(kraev_algoritme("fill_depressions"), {
            "dem": str(aktuel),
            "fix_flats": True,
            "flat_increment": float(hent(konf, "konditionering.flat_increment", 0.0001)),
            "flat_resolution": int(hent(konf, "konditionering.flat_resolution", 1)),
            "output": str(ud),
        }, log, "fill depressions (oprydning)")
    else:
        shutil.copyfile(aktuel, ud)

    # Differenceraster til accepttesten. Aendringer skal ligge langs vandloeb,
    # tilpasningslinjer og reelle lavninger — store aendrede flader betyder forkert
    # konditionering, og det ses ikke paa selve DEM'en.
    diff = derived / "01_diff_hydro.tif"
    raster_regn(diff, lambda hydro, raa: hydro - raa, log,
                "differenceraster dem_hydro - dem_analyse",
                hydro=ud, raa=dem_analyse)

    log.skriv(f"  ACCEPTTEST: inspicer {diff.name} inden du gaar videre. "
              "Aendringer skal foelge vandloeb, tilpasningslinjer og lavninger — ikke store flader.")
    return ud


# ---------------------------------------------------------------------------
# Trin 2 — stroemningsretning og -akkumulering
# ---------------------------------------------------------------------------

def trin2_stroemning(konf: dict, dem_hydro: Path, log: Log) -> dict[str, Path]:
    """
    D8-pointer, akkumulering og beregnet vandloebsnetvaerk.

    Akkumuleringen skrives i CELLER, ikke i specifikt bidragsareal. Vaerktoejets default er
    'sca', og sammenlignes den med en taerskel i hektar, bliver netvaerket meningsloest.
    """
    log.skriv("--- TRIN 2: stroemningsretning og -akkumulering ---")

    derived = absolut(hent(konf, "output.derived_dir"))
    pointer = derived / "02_d8_pointer.tif"
    akkum = derived / "02_d8_akkumulering.tif"
    streams = derived / "02_streams.tif"

    if not spring_over(pointer, dem_hydro, AKTIV_KONFIG):
        koer(kraev_algoritme("d8_pointer"), {
            "dem": str(dem_hydro),
            "esri_pntr": False,  # hold konsistent gennem hele kaeden
            "output": str(pointer),
        }, log, "D8 stroemningsretning")
    else:
        log.skriv(f"  springer over, {pointer.name} er opdateret")

    if not spring_over(akkum, dem_hydro, AKTIV_KONFIG):
        koer(kraev_algoritme("d8_flow_accum"), {
            "input": str(dem_hydro),
            "out_type": 0,  # 0=cells, 1=ca, 2=sca. Default er sca — det er ikke det vi vil have.
            "log_transform": False,
            "clip": False,
            "input_is_pointer": False,
            "esri_pntr": False,
            "output": str(akkum),
        }, log, "D8 akkumulering (celler)")
    else:
        log.skriv(f"  springer over, {akkum.name} er opdateret")

    # Taerskel i hektar omregnes til celleantal, saa enheden matcher akkumuleringsrasteret.
    # Oploesningen laeses af rasteret, ikke af konfigurationen: er de uenige, er det rasteret
    # der bestemmer, og en taerskel regnet paa den oenskede celle ville vaere forkert.
    from qgis.core import QgsRasterLayer
    _r = QgsRasterLayer(str(dem_hydro), "dem_hydro")
    opl = _r.rasterUnitsPerPixelX()
    onsket = float(hent(konf, "analyse.oplaesning_m"))
    if abs(opl - onsket) > 1e-6:
        log.advar(f"analyserasterets celle er {opl:.6f} m, konfigurationen oensker {onsket} m — "
                  "taerskler regnes paa rasterets faktiske celle")
    taerskel_ha = float(hent(konf, "stroemning.vandloeb_taerskel_ha"))
    taerskel_celler = taerskel_ha * 10_000.0 / (opl * opl)
    log.skriv(f"  vandloebstaerskel: {taerskel_ha} ha = {taerskel_celler:.0f} celler "
              f"ved {opl} m")

    if not spring_over(streams, akkum, AKTIV_KONFIG):
        koer(kraev_algoritme("extract_streams"), {
            "flow_accumulation": str(akkum),
            "threshold": taerskel_celler,
            "zero_background": True,
            "output": str(streams),
        }, log, "udled vandloebsnetvaerk")
    else:
        log.skriv(f"  springer over, {streams.name} er opdateret")

    log.skriv("  ACCEPTTEST: det beregnede netvaerk skal falde sammen med det kortlagte "
              "inden for faa celler. Goer det ikke det, virkede stream burning i trin 1 ikke.")
    return {"pointer": pointer, "akkumulering": akkum, "streams": streams}


# ---------------------------------------------------------------------------
# Stroemningsgrafen — grundlaget for trin 3-5
# ---------------------------------------------------------------------------

class Stroemningsgraf:
    """
    D8-nettet som et fladt indeksarray: for hver celle indekset paa den celle vandet loeber
    videre til, eller -1 hvis der ikke er nogen (rasterkant eller sluk).

    Whitebox' egne `watershed` og `unnest_basins` kunne bruges i stedet, men de kraever pour
    points som fil og snapper selv. Her udledes pour points af selve D8-rasteret og ligger
    dermed PAA stroemningsnettet per konstruktion — faldgrube 2 i CLAUDE.md (mislykket snap)
    kan ikke opstaa. Til gengaeld skal afkodningen vaere rigtig, og den udledes empirisk med
    selvkontrol i d8_afkodning().
    """

    def __init__(self, pointer_sti: Path, dem_sti: Path, log: Log):
        import numpy as np
        from osgeo import gdal

        afkodning = d8_afkodning(pointer_sti, dem_sti, log)

        ds = gdal.Open(str(pointer_sti))
        self.gt = ds.GetGeoTransform()
        self.proj = ds.GetProjection()
        pntr = ds.GetRasterBand(1).ReadAsArray()
        ds = None

        self.hoejde, self.bredde = pntr.shape
        self.n = int(pntr.size)
        self.opl = abs(self.gt[1])
        self.celleareal = abs(self.gt[1] * self.gt[5])

        # Flade indeks frem for np.indices: sidstnaevnte allokerer to 64-bit arrays paa
        # rasterets stoerrelse, og ved 41 mio. celler er det 660 MB der ikke er brug for.
        pf = pntr.ravel()
        ned = np.full(self.n, -1, dtype=np.int32)
        for vaerdi, (dr, dc) in afkodning.items():
            idx = np.flatnonzero(pf == vaerdi)
            rr = idx // self.bredde + dr
            cc = idx % self.bredde + dc
            ok = (rr >= 0) & (rr < self.hoejde) & (cc >= 0) & (cc < self.bredde)
            ned[idx[ok]] = (rr[ok] * self.bredde + cc[ok]).astype(np.int32)
        del pntr, pf

        self.ned = ned

        # Omvendt opslag: hvilke celler loeber TIL en given celle. Gemt som to sorterede
        # arrays i stedet for lister, saa en hel bfs-boelge kan slaas op med searchsorted.
        har = ned >= 0
        kilder = np.flatnonzero(har).astype(np.int32)
        maal = ned[har]
        orden = np.argsort(maal, kind="stable")
        self.kilder = kilder[orden]
        self.maal_s = maal[orden]
        del kilder, maal, orden

        self.uden_nedstroems = int((~har).sum())
        log.skriv(f"  stroemningsgraf: {self.n:,} celler, "
                  f"{self.uden_nedstroems:,} uden nedstroems nabo (rasterkant eller sluk)")

    # -- geometri ----------------------------------------------------------

    def koordinat(self, indeks: int) -> tuple[float, float]:
        """Cellecentrets koordinat i kortenheder."""
        r, c = divmod(int(indeks), self.bredde)
        return (self.gt[0] + (c + 0.5) * self.gt[1],
                self.gt[3] + (r + 0.5) * self.gt[5])

    def ha(self, celler: int) -> float:
        return celler * self.celleareal / 1e4

    def celle_ved(self, x: float, y: float):
        """Fladt indeks for et kortkoordinat, eller None hvis det ligger uden for rasteret."""
        c = int((x - self.gt[0]) / self.gt[1])
        r = int((y - self.gt[3]) / self.gt[5])
        if 0 <= r < self.hoejde and 0 <= c < self.bredde:
            return r * self.bredde + c
        return None

    # -- sporing -----------------------------------------------------------

    def opstroems(self, start, etiketter=None, spaerre=None):
        """
        Alle celler hvis stroemningsvej ender i en af startcellerne.

        Returnerer et etiketarray hvor 0 betyder "ikke opstroems". Med flere startpunkter
        udbreder etiketterne sig samtidig, og en celle beholder den foerste etiket der naar
        den. Det goer nestede oplande disjunkte helt af sig selv: ligger punkt B opstroems
        for punkt A, er afstanden fra B altid mindre end fra A langs enhver stroemningsvej
        gennem B, saa B's etiket vinder overalt opstroems for B. Det svarer til
        `unnest_basins`, men uden et ekstra gennemloeb.

        `spaerre` er en maske af celler sporingen ikke maa passere igennem — i praksis
        projektomraadet. Uden den fortsaetter sporingen fra et indloeb op gennem
        projektomraadet og ud paa den anden side, og opsamler alt det terraen der loeber
        ind i projektomraadet LAENGERE OPPE. Det vand er for laengst naaet ind som direkte
        tilstroemning, men bliver tildelt indloebet nedstroems.

        Det er ikke en detalje. For et projektomraade der ligger som en korridor langs
        vandloebet — den almindelige form for et vaadomraadeprojekt — kryber vandloebet ind
        og ud af omraadet mange gange, og saa er det stoerstedelen af det direkte opland der
        havner det forkerte sted. At fjerne projektcellerne BAGEFTER hjaelper ikke: de celler
        der ligger opstroems for dem, er allerede maerket.
        """
        import numpy as np

        lab = np.zeros(self.n, dtype=np.int32)
        start = np.asarray(start, dtype=np.int64)
        if start.size == 0:
            raise OplandsFejl("opstroems() kaldt uden startceller")
        lab[start] = 1 if etiketter is None else np.asarray(etiketter, dtype=np.int32)

        front = np.unique(start.astype(np.int32))
        while front.size:
            lo = np.searchsorted(self.maal_s, front, "left")
            hi = np.searchsorted(self.maal_s, front, "right")
            antal = (hi - lo).astype(np.int64)
            har = antal > 0
            if not har.any():
                break
            lo, antal, front = lo[har], antal[har], front[har]

            # Ragged gather: alle boelgens kildeceller i ét greb frem for en loekke pr. celle.
            i_alt = int(antal.sum())
            offset = np.cumsum(antal) - antal
            pos = np.repeat(lo, antal) + (np.arange(i_alt, dtype=np.int64) - np.repeat(offset, antal))
            nye = self.kilder[pos]
            arv = lab[np.repeat(front, antal)]

            # Hver celle har praecis én nedstroems nabo, saa nye indeholder ingen dubletter.
            # Filteret paa lab == 0 er det der beskytter allerede tildelte (nestede) punkter,
            # og det er samtidig garantien mod uendelig loekke hvis grafen har en cykel.
            fri = lab[nye] == 0
            if spaerre is not None:
                # Spaerrede celler maerkes ikke OG udforskes ikke videre opstroems
                fri &= ~spaerre[nye]
            nye, arv = nye[fri], arv[fri]
            if nye.size == 0:
                break
            lab[nye] = arv
            front = np.sort(nye)
        return lab

    def nedstroems_traef(self, start: int, maal: set) -> list:
        """
        Hvilke af maal-cellerne stroemningsvejen fra start passerer, i raekkefoelge nedstroems.

        Bruges til at afgoere om ét indloebspunkt ligger opstroems for et andet. Det er en
        vandring langs én kaede, ikke en soegning, saa det koster ingenting sammenlignet med
        at delineere oplandet for hvert punkt og se efter overlap.
        """
        ud = []
        i = int(self.ned[start])
        skridt = 0
        while i >= 0 and skridt < self.n:
            if i in maal:
                ud.append(i)
            i = int(self.ned[i])
            skridt += 1
        return ud

    def forlader_igen(self, start: int, maske_flad):
        """
        Foelger stroemningsvejen fra en celle og finder ud af om vandet forlader masken igen.

        Returnerer den foerste celle uden for masken efter at have vaeret inde, eller None
        hvis vandet bliver. Skelnen mellem et tilloeb og en gennemstroemning kan ikke
        traeffes ved at se opstroems — begge dele ligner et indloeb derfra.
        """
        i = int(self.ned[start])
        inde = False
        skridt = 0
        while i >= 0 and skridt < self.n:
            if maske_flad[i]:
                inde = True
            elif inde:
                return i
            i = int(self.ned[i])
            skridt += 1
        return None

    def peger_ind_i(self, maske_flad):
        """Celler uden for masken hvis stroemning gaar direkte ind i den — indloebscellerne."""
        import numpy as np
        ud = np.zeros(self.n, dtype=bool)
        kandidat = (~maske_flad) & (self.ned >= 0)
        ud[kandidat] = maske_flad[self.ned[kandidat]]
        return ud


# ---------------------------------------------------------------------------
# Raster- og vektorhjaelpere til trin 3-5
# ---------------------------------------------------------------------------

def _rasteriser_maske(vektor_sti: Path, lag: str | None, graf: Stroemningsgraf,
                      log: Log, hvad: str):
    """Rasteriserer et vektorlag paa analysegridet og returnerer en boolsk maske."""
    from osgeo import gdal

    mem = gdal.GetDriverByName("MEM").Create("", graf.bredde, graf.hoejde, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(graf.gt)
    mem.SetProjection(graf.proj)
    ds, l = _ogr_lag(vektor_sti, lag)
    gdal.RasterizeLayer(mem, [1], l, burn_values=[1])
    arr = mem.GetRasterBand(1).ReadAsArray().astype(bool)
    mem = None
    ds = None
    if not arr.any():
        raise OplandsFejl(
            f"{hvad} rasteriserede til nul celler. Enten ligger laget uden for "
            "analyseudstraekningen, eller ogsaa er CRS forskelligt fra rasterets.")
    log.skriv(f"  {hvad}: {int(arr.sum()):,} celler ({graf.ha(int(arr.sum())):.1f} ha)")
    return arr


def _skriv_raster(arr, graf: Stroemningsgraf, ud: Path, gdal_type, nodata=0) -> Path:
    """Skriver et array paa analysegridet. NoData saettes, saa vektorisering springer 0 over."""
    from osgeo import gdal
    ds = gdal.GetDriverByName("GTiff").Create(
        str(ud), graf.bredde, graf.hoejde, 1, gdal_type,
        options=["COMPRESS=DEFLATE", "TILED=YES"])
    ds.SetGeoTransform(graf.gt)
    ds.SetProjection(graf.proj)
    baand = ds.GetRasterBand(1)
    baand.WriteArray(arr.reshape(graf.hoejde, graf.bredde))
    baand.SetNoDataValue(nodata)
    ds = None
    if not ud.exists() or ud.stat().st_size == 0:
        raise OplandsFejl(f"kunne ikke skrive {ud}")
    return ud


def _ryd_op_maske(maske, graf: Stroemningsgraf, min_m2: float, log: Log, hvad: str):
    """
    Fjerner isolerede oeer og lukker huller under minimumsarealet.

    Koeres KUN paa totaloplandet. Ryddes vandloebsoplandene ogsaa op hver for sig, holder
    arealbalancen i trin 5 ikke laengere: en fjernet oe i ét lag dukker op som restareal i
    et andet. Alt efterfoelgende udledes af det oprydede totalopland.
    """
    import numpy as np
    from scipy import ndimage

    min_celler = max(1, int(round(min_m2 / graf.celleareal)))
    maske = maske.reshape(graf.hoejde, graf.bredde)

    # 8-forbundethed for oplandet, 4 for baggrunden: den duale kombination er den eneste
    # der ikke kan give baade en "oe" og et "hul" paa samme diagonale beroering.
    lab, antal = ndimage.label(maske, structure=np.ones((3, 3), bool))
    if antal > 1:
        stoerrelse = np.bincount(lab.ravel(), minlength=antal + 1)
        stoerrelse[0] = 0
        smaa = (stoerrelse < min_celler) & (stoerrelse > 0)
        if smaa.any():
            fjern = smaa[lab]
            maske = maske & ~fjern
            log.skriv(f"  {hvad}: fjernede {int(smaa.sum())} oeer under {min_m2:.0f} m2 "
                      f"({int(fjern.sum()):,} celler)")
        elif antal > 1:
            log.skriv(f"  {hvad}: {antal} sammenhaengende dele, alle over minimumsarealet")

    hul, hn = ndimage.label(~maske, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool))
    if hn:
        stoerrelse = np.bincount(hul.ravel(), minlength=hn + 1)
        stoerrelse[0] = 0
        kandidat = (stoerrelse < min_celler) & (stoerrelse > 0)
        # Baggrund der roerer rasterkanten er ikke et hul, uanset hvor lille den er
        for raekke in (hul[0, :], hul[-1, :], hul[:, 0], hul[:, -1]):
            kandidat[np.unique(raekke)] = False
        if kandidat.any():
            luk = kandidat[hul]
            maske = maske | luk
            log.skriv(f"  {hvad}: lukkede {int(kandidat.sum())} huller under {min_m2:.0f} m2 "
                      f"({int(luk.sum()):,} celler)")
    return maske.ravel()


def _polygoniser(raster_sti: Path, ud_gpkg: Path, graf: Stroemningsgraf,
                 celletal: dict, log: Log, hvad: str) -> dict:
    """
    Polygoniserer et heltalsraster (0 = NoData = intet) til én geometri pr. vaerdi.

    Firforbundethed med vilje: med otte kan GDAL producere polygoner der roerer sig selv i et
    diagonalt naaleoeje, og de er ugyldige. Med fire bliver et diagonalt naaleoeje til to
    polygoner der moedes i et punkt — gyldigt, og med praecis samme areal.

    Arealkontrollen til sidst er ikke pynt. Polygonarealet SKAL svare til celleantallet;
    afviger det, har vektoriseringen tabt et hul eller lagt polygoner oven i hinanden, og saa
    holder arealbalancen i trin 5 ikke — uden at noget som helst melder fejl.
    """
    from qgis.core import QgsGeometry

    _frigiv(ud_gpkg)
    koer("gdal:polygonize", {
        "INPUT": str(raster_sti),
        "BAND": 1,
        "FIELD": "vaerdi",
        "EIGHT_CONNECTEDNESS": False,
        "EXTRA": "",
        "OUTPUT": str(ud_gpkg),
    }, log, f"vektoriser {hvad}")

    # Laeses med OGR frem for QgsVectorLayer, saa datakilden kan lukkes paa kommando.
    # Et QgsVectorLayer holder filen aaben indtil Python samler det op, og i QGIS lever
    # processen videre mellem koersler — anden koersel kunne saa ikke slette sin egen
    # mellemfil. Fra kommandolinjen ses det aldrig, for dér er hver koersel en ny proces.
    ds, lag = _ogr_lag(ud_gpkg, None)
    if lag.GetFeatureCount() == 0:
        raise OplandsFejl(f"vektoriseringen af {hvad} gav nul polygoner")

    dele: dict[int, list] = {}
    ugyldige = 0
    for f in lag:
        raa = f.GetField("vaerdi")
        if raa is None:
            continue
        v = int(raa)
        if v == 0:
            continue
        g = _qgs_geometri(f.GetGeometryRef())
        if g is None:
            continue
        if not g.isGeosValid():
            g = g.makeValid()
            ugyldige += 1
        dele.setdefault(v, []).append(g)
    ds = None
    if ugyldige:
        log.skriv(f"  {hvad}: {ugyldige} polygoner rettet med makeValid")

    ud = {}
    for v, gs in sorted(dele.items()):
        flade = []
        for g in gs:
            flade.extend(g.asGeometryCollection() if g.isMultipart() else [g])
        geom = QgsGeometry.collectGeometry(flade)
        forventet = celletal[v] * graf.celleareal
        afvig = abs(geom.area() - forventet) / max(forventet, 1.0)
        if afvig > 1e-6:
            raise OplandsFejl(
                f"vektoriseringen af {hvad} (vaerdi {v}) gav {geom.area():,.1f} m2, "
                f"men rasteret har {celletal[v]:,} celler = {forventet:,.1f} m2 "
                f"(afvigelse {afvig:.2%}). Arealbalancen kan ikke holde med et saadant output.")
        ud[v] = geom
    log.skriv(f"  {hvad}: {len(ud)} geometrier, areal stemmer med celleantallet")
    return ud


def _felt(navn: str, slags: str):
    """QMetaType frem for QVariant — QVariant-konstruktoeren er deprecated fra QGIS 3.38."""
    from qgis.core import QgsField
    from qgis.PyQt.QtCore import QMetaType
    typer = {"tekst": QMetaType.Type.QString,
             "tal": QMetaType.Type.Double,
             "heltal": QMetaType.Type.Int}
    return QgsField(navn, typer[slags])


def _skriv_lag(gpkg: Path, lagnavn: str, geom_type: str, epsg: int,
               felter: list[tuple[str, str]], raekker: list[tuple], log: Log,
               foerste: bool = False):
    """Bygger et hukommelseslag og skriver det som ét lag i leverance-GeoPackagen."""
    from qgis.core import (QgsVectorLayer, QgsFeature, QgsVectorFileWriter,
                           QgsCoordinateTransformContext)

    lag = QgsVectorLayer(f"{geom_type}?crs=EPSG:{epsg}", lagnavn, "memory")
    dp = lag.dataProvider()
    dp.addAttributes([_felt(n, s) for n, s in felter])
    lag.updateFields()

    objekter = []
    for geom, vaerdier in raekker:
        f = QgsFeature(lag.fields())
        f.setGeometry(geom)
        f.setAttributes(list(vaerdier))
        objekter.append(f)
    if not dp.addFeatures(objekter):
        raise OplandsFejl(f"kunne ikke tilfoeje objekter til {lagnavn}")
    lag.updateExtents()

    opt = QgsVectorFileWriter.SaveVectorOptions()
    opt.driverName = "GPKG"
    opt.layerName = lagnavn
    opt.fileEncoding = "UTF-8"
    # CreateOrOverwriteFile sletter hele filen og fejler hvis den er aaben et andet sted.
    # Findes GeoPackagen i forvejen, erstattes lagene ét ad gangen i stedet: det virker
    # ogsaa mens leverancen ligger i QGIS' lagpanel, og lagene opdaterer sig selv.
    opt.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if foerste
                                else QgsVectorFileWriter.CreateOrOverwriteLayer)
    svar = QgsVectorFileWriter.writeAsVectorFormatV3(
        lag, str(gpkg), QgsCoordinateTransformContext(), opt)
    if svar[0] != QgsVectorFileWriter.NoError:
        raise OplandsFejl(
            f"kunne ikke skrive laget {lagnavn} til {gpkg}: {svar[1]}\n"
            "Er filen aaben i et andet program? En GeoPackage der er laast, kan hverken "
            "erstattes eller udvides.")
    log.skriv(f"  skrev lag {lagnavn}: {len(objekter)} objekter")


# ---------------------------------------------------------------------------
# Trin 3 — totalopland til projektomraadet
# ---------------------------------------------------------------------------

def trin3_totalopland(konf: dict, graf: Stroemningsgraf, log: Log) -> dict:
    """
    Alle celler hvis stroemningsvej ender inde i projektomraadet, projektomraadet selv medregnet.

    Projektomraadet er et polygon, ikke et punkt, saa sporingen starter fra samtlige celler i
    det paa én gang. Det er baade hurtigere og mere korrekt end at finde indloebsceller foerst:
    en celle der loeber ind over graensen og straks ud igen taelles ikke to gange.
    """
    import numpy as np
    from osgeo import gdal

    log.skriv("--- TRIN 3: totalopland ---")
    derived = absolut(hent(konf, "output.derived_dir"))

    projekt_sti = absolut(hent(konf, "input.projektomraade"))
    projekt = _rasteriser_maske(projekt_sti, hent(konf, "input.projektomraade_lag", None),
                                graf, log, "projektomraade").ravel()

    lab = graf.opstroems(np.flatnonzero(projekt))
    total = (lab > 0) | projekt
    log.skriv(f"  sporet opstroems: {int(total.sum()):,} celler = {graf.ha(int(total.sum())):.1f} ha")

    min_m2 = float(hent(konf, "oplande.min_polygon_areal_m2", 100.0))
    total = _ryd_op_maske(total, graf, min_m2, log, "totalopland")
    # Projektomraadet er per definition en del af totaloplandet, ogsaa hvis oprydningen
    # skulle have ramt en tynd udloeber af det.
    total = total | projekt

    # ACCEPTTEST: kantkontaminering. Roerer oplandet rasterkanten, er det afskaaret, og
    # arealet er et minimum. Faldgrube 1 i CLAUDE.md — den fejler uden fejlmeddelelse.
    total2d = total.reshape(graf.hoejde, graf.bredde)
    kant = np.zeros_like(total2d)
    kant[0, :] = kant[-1, :] = kant[:, 0] = kant[:, -1] = True
    roerer = total2d & kant
    kant_celler = int(roerer.sum())
    kant_detaljer = {}
    if kant_celler:
        ys, xs = np.where(roerer)
        for navn, m in (("nord", ys == 0), ("syd", ys == graf.hoejde - 1),
                        ("vest", xs == 0), ("oest", xs == graf.bredde - 1)):
            if m.any():
                kant_detaljer[navn] = int(m.sum())
        besked = ("TOTALOPLANDET ER AFSKAARET VED RASTERKANTEN: "
                  + ", ".join(f"{k} {v} celler" for k, v in kant_detaljer.items())
                  + f". Arealet {graf.ha(int(total.sum())):.1f} ha er et MINIMUM, ikke et facit. "
                    "Skaf en terraenmodel der raekker laengere i de retninger og koer forfra.")
        if bool(hent(konf, "qa.kraev_nul_kantkontaminering", True)):
            raise OplandsFejl(besked + "\n(qa.kraev_nul_kantkontaminering=true)")
        log.advar(besked)
    else:
        log.skriv("  ACCEPTTEST kantkontaminering: BESTAAET — oplandet roerer ikke rasterkanten")

    _skriv_raster(total.astype(np.uint8), graf, derived / "03_totalopland.tif", gdal.GDT_Byte)

    return {"projekt": projekt, "total": total,
            "kant_celler": kant_celler, "kant_detaljer": kant_detaljer}


# ---------------------------------------------------------------------------
# Trin 4 — vandloebsoplande
# ---------------------------------------------------------------------------

def trin4_vandloebsoplande(konf: dict, graf: Stroemningsgraf, trin3: dict, log: Log) -> dict:
    """
    Ét opland pr. punkt hvor et kortlagt vandloeb krydser projektgraensen indadgaaende.

    Indloebspunkterne udledes af D8-rasteret, ikke af vektorgeometrien: en celle er et indloeb
    hvis den ligger uden for projektomraadet OG dens stroemningsretning peger ind i det.
    Dermed er retningen afgjort af hydrologien (faldgrube 5), og punktet ligger allerede paa
    stroemningsnettet (faldgrube 2).

    ALLE tilloeb udpeges foerst, og foerst derefter afgoeres hvilke der sker gennem et kortlagt
    vandloeb. Raekkefoelgen er vigtig: soeger man kun taet paa de kortlagte linjer, forsvinder
    et tilloeb der loeber i en groeft AIS ikke daekker, ind i det direkte opland uden at nogen
    kontrol fejler. De ukortlagte tilloeb leveres derfor som punkter med deres opland, saa det
    er synligt hvor det direkte opland i virkeligheden koncentrerer sig.
    """
    import numpy as np
    from osgeo import gdal

    log.skriv("--- TRIN 4: vandloebsoplande ---")
    derived = absolut(hent(konf, "output.derived_dir"))
    projekt, total = trin3["projekt"], trin3["total"]

    vl_sti = valgfri_sti(konf, "input.vandloeb")
    if vl_sti is None:
        raise OplandsFejl(
            "Vandloebsoplande kraever et kortlagt vandloebsnetvaerk (input.vandloeb). "
            "Uden det er direkte opland heller ikke defineret — det ER totaloplandet minus "
            "vandloebsoplandene.")
    vl_lag = hent(konf, "input.vandloeb_lag", None)

    # 1. Alle indloebsceller paa projektgraensen
    indloeb = graf.peger_ind_i(projekt) & total
    log.skriv(f"  indloebsceller i alt paa graensen: {int(indloeb.sum()):,}")
    if not indloeb.any():
        raise OplandsFejl("ingen celler stroemmer ind i projektomraadet — er D8-rasteret gyldigt?")

    ds = gdal.Open(str(derived / "02_d8_akkumulering.tif"))
    akk = ds.GetRasterBand(1).ReadAsArray().ravel()
    ds = None

    # 2. Udpeg de enkelte tilloeb. Naboceller langs samme kanal baerer naesten samme
    #    akkumulering, saa der tages den stoerste og undertrykkes alt inden for
    #    minimumsafstanden — to tilloeb taettere paa hinanden end det er ét tilloeb.
    rapport_ha = float(hent(konf, "oplande.indloeb_rapport_ha", 10.0))
    min_afstand_m = float(hent(konf, "oplande.indloeb_min_afstand_m", 50.0))
    min_celler = min_afstand_m / graf.opl

    idx = np.flatnonzero(indloeb)
    ha_pr_celle = akk[idx] * graf.celleareal / 1e4
    orden = idx[np.argsort(-ha_pr_celle)]
    valgt: list[int] = []
    valgt_rc: list[tuple[int, int]] = []
    for i in orden:
        if graf.ha(float(akk[i])) < rapport_ha:
            break
        r, c = divmod(int(i), graf.bredde)
        if any((r - r2) ** 2 + (c - c2) ** 2 < min_celler ** 2 for r2, c2 in valgt_rc):
            continue
        valgt.append(int(i))
        valgt_rc.append((r, c))
    log.skriv(f"  {len(valgt)} selvstaendige tilloeb over {rapport_ha:g} ha "
              f"(mindste indbyrdes afstand {min_afstand_m:g} m)")
    if not valgt:
        raise OplandsFejl(
            f"Ingen tilloeb over {rapport_ha} ha paa projektgraensen, selv om "
            f"{int(indloeb.sum()):,} celler stroemmer ind. Kontrollér akkumuleringsrasteret.")

    # 3. Klassificér: sker tilloebet gennem et KORTLAGT vandloeb? Afstanden maales i
    #    vektorrummet mod den faktiske linje, ikke mod en rasterisering af den — en
    #    rasterisering ville laegge en halv celles vilkaarlighed oven i graensen.
    from qgis.core import QgsVectorLayer, QgsSpatialIndex, QgsGeometry, QgsPointXY
    ais = QgsVectorLayer(_kilde(vl_sti, vl_lag), "vandloeb", "ogr")
    if not ais.isValid():
        raise OplandsFejl(f"vandloebslaget kunne ikke aabnes: {vl_sti}")
    sindeks = QgsSpatialIndex(ais.getFeatures(), flags=QgsSpatialIndex.FlagStoreFeatureGeometries)
    navnefelt = next((n for n in ("VLNAVN", "navn", "NAVN", "vandloebsnavn")
                      if ais.fields().indexOf(n) >= 0), None)
    maks_afstand = float(hent(konf, "stroemning.snap_radius_celler", 5)) * graf.opl

    alle: list[dict] = []
    for i in valgt:
        x, y = graf.koordinat(i)
        pgeom = QgsGeometry.fromPointXY(QgsPointXY(x, y))
        navn, orden_vl, afstand = "-", None, float("inf")
        naboer = sindeks.nearestNeighbor(pgeom, 1)
        if naboer:
            f = ais.getFeature(naboer[0])
            navn = (str(f[navnefelt]) if navnefelt and f[navnefelt] is not None else "unavngivet")
            if ais.fields().indexOf("ORDEN") >= 0 and f["ORDEN"] is not None:
                orden_vl = float(f["ORDEN"])
            afstand = pgeom.distance(f.geometry())
        alle.append({"celle": i, "x": x, "y": y, "navn": navn, "orden": orden_vl,
                     "afstand_m": afstand, "akk_ha": graf.ha(float(akk[i])),
                     "type": "vandloeb" if afstand <= maks_afstand else "terraen"})

    min_ha = float(hent(konf, "oplande.min_vandloebsopland_ha", 1.0))
    vandloebstilloeb = [d for d in alle if d["type"] == "vandloeb" and d["akk_ha"] >= min_ha]
    ukortlagte = [d for d in alle if d["type"] != "vandloeb"]

    log.skriv(f"  tilloeb gennem et kortlagt vandloeb: {len(vandloebstilloeb)}")
    for d in sorted(vandloebstilloeb, key=lambda d: -d["akk_ha"]):
        log.skriv(f"    {d['navn']:<22s} ({d['x']:.0f}, {d['y']:.0f})  "
                  f"{d['akk_ha']:9.1f} ha, {d['afstand_m']:.1f} m fra det kortlagte forloeb")
    if not vandloebstilloeb:
        raise OplandsFejl(
            "Intet kortlagt vandloeb loeber ind i projektomraadet. Enten er der reelt ingen "
            "vandloebstilloeb — og saa er hele totaloplandet direkte opland — eller ogsaa "
            "daekker vandloebslaget ikke omraadet. Kontrollér foer du gaar videre.")

    if ukortlagte:
        # Det er ikke en fejl: definitionen i CLAUDE.md siger KORTLAGT vandloeb, og AIS
        # indeholder kun de stoerre forloeb. Men et tilloeb paa 100 ha er ikke diffus
        # afstroemning, og forskellen betyder noget for hvordan vandet skal haandteres.
        log.advar(
            f"{len(ukortlagte)} tilloeb paa {min(d['akk_ha'] for d in ukortlagte):.0f}"
            f"-{max(d['akk_ha'] for d in ukortlagte):.0f} ha loeber ind i projektomraadet UDEN "
            f"at ligge paa et kortlagt vandloeb (naermeste er "
            f"{min(d['afstand_m'] for d in ukortlagte):.0f}"
            f"-{max(d['afstand_m'] for d in ukortlagte):.0f} m vaek).\n"
            "     De taeller som DIREKTE opland, jf. definitionen — men de er koncentreret "
            "afstroemning i groefter, ikke diffus terraennaer afstroemning.\n"
            "     Deres samlede areal opgoeres i trin 5; akkumuleringerne nedenfor kan "
            "overlappe og maa ikke laegges sammen.")
        for d in sorted(ukortlagte, key=lambda d: -d["akk_ha"]):
            log.skriv(f"    ukortlagt tilloeb ({d['x']:.0f}, {d['y']:.0f})  "
                      f"{d['akk_ha']:9.1f} ha, naermeste kortlagte er {d['navn']} "
                      f"{d['afstand_m']:.0f} m vaek")

    # 3b. Loeber tilloebet IGENNEM projektomraadet, eller ender det der?
    #     Afgoerende for hvordan tallet skal laeses, og det kan ikke ses opstroems fra:
    #     et vandloeb der passerer tvaers over arealet, ligner et hvilket som helst andet
    #     indloeb. Et forloeb med 27 km² opland, der loeber ind i den ene ende og ud i den
    #     anden, er gennemstroemning i en defineret kanal — ikke opland der afvander til
    #     fladen. Arealet er det samme; det er tolkningen der er en anden.
    indloebsdata = []
    for nr, d in enumerate(sorted(vandloebstilloeb, key=lambda d: -d["akk_ha"]), start=1):
        d["id"] = nr
        udloeb = graf.forlader_igen(d["celle"], projekt)
        if udloeb is None:
            d.update(karakter="ender i omraadet", udloeb_x=None, udloeb_y=None,
                     udloeb_akk_ha=None)
        else:
            ux, uy = graf.koordinat(udloeb)
            d.update(karakter="gennemstroemning", udloeb_x=ux, udloeb_y=uy,
                     udloeb_akk_ha=graf.ha(float(akk[udloeb])))
            log.advar(
                f"{d['navn']} loeber IGENNEM projektomraadet: ind med {d['akk_ha']:.0f} ha "
                f"ved ({d['x']:.0f}, {d['y']:.0f}), ud igen med {d['udloeb_akk_ha']:.0f} ha "
                f"ved ({ux:.0f}, {uy:.0f}).\n"
                "     Oplandet bag det taeller som opland TIL projektomraadet, jf. "
                "definitionen — men vandet passerer i en kanal og afvander ikke til fladen.\n"
                "     Se attributten karakter paa vandloebsoplande og laget udloebspunkter.")
        indloebsdata.append(d)

    # 4. Ét gennemloeb udbreder alle etiketter samtidig og goer nestede oplande disjunkte.
    #    Spaerren ved projektomraadet er afgoerende: uden den fortsaetter sporingen op
    #    gennem omraadet og tager alt det med, der loeber ind laengere oppe.
    lab = graf.opstroems([d["celle"] for d in indloebsdata],
                         [d["id"] for d in indloebsdata], spaerre=projekt)
    lab[~total] = 0

    # Hvad spaerren flyttede. Tallet er vaerd at se hver gang: det er areal hvis vand loeber
    # ind i projektomraadet ET ANDET STED end gennem indloebet, og som derfor er direkte
    # opland. For et projektomraade der ligger som en korridor langs vandloebet, kan det
    # vaere stoerstedelen af det direkte opland.
    uden_spaerre = graf.opstroems([d["celle"] for d in indloebsdata],
                                  [d["id"] for d in indloebsdata])

    # Der maa IKKE fjernes noget her. Tallet skal kunne holdes op mod Whitebox'
    # akkumulering, og den taeller samtlige opstroems celler — ogsaa dem inde i
    # projektomraadet. Fjernes projektcellerne foerst, sammenlignes to forskellige
    # stoerrelser, og QA-tjekket fejler paa et fuldstaendig korrekt resultat. Paa et
    # kompakt projektomraade er forskellen promiller; paa en korridor langs vandloebet er
    # den tocifrede procenter, fordi en stor del af det opstroemsliggende areal ER
    # projektomraadet.
    celletal_uspaerret = {d["id"]: int((uden_spaerre == d["id"]).sum())
                          for d in indloebsdata}

    # Til diagnosen er det derimod kun arealet UDEN FOR projektomraadet der er flyttet
    # fra vandloebsopland til direkte opland.
    flyttet = int(((uden_spaerre > 0) & (lab == 0) & total & (~projekt)).sum())
    del uden_spaerre
    if flyttet:
        log.skriv(f"  spaerren ved projektomraadet holdt {graf.ha(flyttet):.1f} ha ude af "
                  "vandloebsoplandene — areal der naar projektomraadet opstroems for "
                  "indloebet og altsaa ikke kommer ind gennem vandloebet")
    else:
        log.skriv("  spaerren ved projektomraadet flyttede intet — vandloebet loeber ikke "
                  "ind og ud af omraadet")

    # Projektomraadet selv er ikke opland via vandloeb. Det kan komme med hvis en
    # projektcelle loeber ud over graensen og ind i et indloeb laengere nede.
    i_projekt = int((lab[projekt] > 0).sum())
    if i_projekt:
        log.skriv(f"  {i_projekt:,} celler inde i projektomraadet laa opstroems for et indloeb "
                  "(graensen slynger sig) — de henfoeres til direkte opland")
        lab[projekt] = 0

    # ACCEPTTEST: er oplandene stadig sammenhaengende?
    #
    # Sporingen kan ikke fragmentere: hver celle haenger sammen med pour pointet gennem sin
    # egen stroemningsvej, og alle celler paa den vej er med i oplandet. Et sporet opland er
    # derfor 8-forbundet per konstruktion.
    #
    # De to fjernelser ovenfor er en anden sag. `lab[~total] = 0` og `lab[projekt] = 0`
    # tager celler UD af et faerdigt opland, og en fjernelse kan skaere det midt over —
    # ligger projektomraadet som en kile ind i oplandet, bliver resten til to stykker.
    # Det er det eneste sted fragmentering kan opstaa, og derfor maales det her.
    from scipy import ndimage
    struktur = np.ones((3, 3), bool)
    fragmenter: dict[int, list[float]] = {}
    for d in indloebsdata:
        komp, antal_dele = ndimage.label(
            (lab == d["id"]).reshape(graf.hoejde, graf.bredde), structure=struktur)
        if antal_dele > 1:
            stoerrelser = np.bincount(komp.ravel())[1:]
            fragmenter[d["id"]] = sorted(
                (graf.ha(int(v)) for v in stoerrelser), reverse=True)
    min_m2 = float(hent(konf, "oplande.min_polygon_areal_m2", 100.0))
    for d in indloebsdata:
        stykker = fragmenter.get(d["id"])
        if not stykker:
            continue
        vaesentlige = [ha for ha in stykker[1:] if ha * 1e4 >= min_m2]
        besked = (f"{d['navn']}s opland er delt i {len(stykker)} stykker: "
                  + ", ".join(f"{ha:.2f} ha" for ha in stykker[:6])
                  + ("..." if len(stykker) > 6 else ""))
        if vaesentlige:
            log.advar(besked + "\n     Fjernelsen af projektomraadets celler har skaaret "
                               "oplandet over. Polygonet daekker nu omraader der ikke "
                               "haenger sammen.")
        else:
            log.skriv(f"  {besked} — alle ud over det stoerste er under "
                      f"{min_m2:.0f} m2 og uden betydning")
    if not fragmenter:
        log.skriv("  alle vandloebsoplande er sammenhaengende efter udklipningen")

    celletal = {d["id"]: int((lab == d["id"]).sum()) for d in indloebsdata}
    for d in indloebsdata:
        d["areal_ha"] = graf.ha(celletal[d["id"]])
        d["areal_uspaerret_ha"] = graf.ha(celletal_uspaerret[d["id"]])

    # Nesting FOERST, paa den fulde liste. Fjernes nogen indloeb inden, forsvinder netop den
    # oplysning der forklarer hvorfor de tilbagevaerende har et lille opland — og saa ser et
    # korrekt resultat ud som en fejl.
    id_af_celle = {d["celle"]: d["id"] for d in indloebsdata}
    opstroems_for = {d["id"]: [] for d in indloebsdata}
    for d in indloebsdata:
        for celle in graf.nedstroems_traef(d["celle"], set(id_af_celle)):
            opstroems_for[id_af_celle[celle]].append(d["id"])
    for d in indloebsdata:
        d["nestede"] = opstroems_for[d["id"]]

    # INDLOEB UDEN SELVSTAENDIGT OPLAND. To uskyldige aarsager, og begge er almindelige naar
    # projektomraadet er en korridor langs vandloebet, som kryber ind og ud af det:
    #   - genindloeb: vandet er allerede naaet ind i projektomraadet opstroems (spaerren)
    #   - nesting:    et indloeb laengere oppe har taget arealet
    #
    # En mislykket sporing kan det derimod IKKE vaere. Pour pointet er udledt af selve
    # D8-rasteret og ligger paa stroemningsnettet per konstruktion — faldgrube 2 kan ikke
    # opstaa her. Derfor rejses ingen fejl; de leveres som punkter i stedet for polygoner.
    # Sikkerhedsnettet mod en gal sporing er sporing_mod_akkumulering, der holder min egen
    # sporing op mod Whitebox' uafhaengige akkumulering.
    genindloeb = [d for d in indloebsdata if d["areal_ha"] < min_ha]
    if genindloeb:
        for d in genindloeb:
            d["type"] = "genindloeb"
            d["aarsag"] = (
                f"indloeb {d['nestede']} ligger opstroems og har taget arealet"
                if d["nestede"] else
                "vandet er allerede loebet ind i projektomraadet opstroems")
            log.skriv(
                f"    uden selvstaendigt opland: {d['navn']} ved "
                f"({d['x']:.0f}, {d['y']:.0f}) baerer {d['akk_ha']:.1f} ha — {d['aarsag']}. "
                "Leveres som punkt, ikke som polygon.")
            lab[lab == d["id"]] = 0
        gen_ider = {d["id"] for d in genindloeb}
        indloebsdata = [d for d in indloebsdata if d["id"] not in gen_ider]
        celletal = {d["id"]: int((lab == d["id"]).sum()) for d in indloebsdata}
        if not indloebsdata:
            log.advar(
                f"Ingen af de {len(genindloeb)} vandloebsindloeb tilfoerer nyt opland: "
                "vandloebet loeber ind og ud af projektomraadet uden at noget af dets "
                "opland kommer ind gennem et af de fundne punkter.\n"
                "     Hele totaloplandet leveres dermed som direkte opland. Kontrollér om "
                "det foerste, oeverste indloeb overhovedet er fundet — ligger det kortlagte "
                "forloeb mere end snap_radius_celler fra modellens kanal dér, er det havnet "
                "blandt de ukortlagte tilloeb i stedet.")

    for d in sorted(indloebsdata, key=lambda d: -d["areal_ha"]):
        log.skriv(f"    opland {d['id']:2d} {d['navn']:<22s} {d['areal_ha']:9.1f} ha"
                  + (f"  (netto — indloeb {d['nestede']} ligger opstroems)"
                     if d["nestede"] else ""))

    _skriv_raster(lab.astype(np.int32), graf, derived / "04_vandloebsoplande.tif", gdal.GDT_Int32)

    dele, delliste = _underopdel_pr_vandloeb(
        graf, lab, indloebsdata, akk, vl_sti, vl_lag, navnefelt, projekt, konf, log)
    _skriv_raster(dele, graf, derived / "04_vandloebsoplande_dele.tif", gdal.GDT_Int32)

    return {"etiketter": lab, "indloeb": indloebsdata, "celletal": celletal,
            "ukortlagte": ukortlagte, "genindloeb": genindloeb,
            "dele": dele, "delliste": delliste,
            "fragmenter": fragmenter, "min_polygon_m2": min_m2,
            "celletal_dele": {d["del_id"]: d["celler"] for d in delliste}}


def _underopdel_pr_vandloeb(graf: Stroemningsgraf, lab, indloebsdata: list, akk,
                            vl_sti: Path, vl_lag: str | None, navnefelt: str | None,
                            projekt, konf: dict, log: Log):
    """
    Deler hvert vandloebsopland op pr. kortlagt vandloeb.

    ÉT indloebspunkt kan repraesentere et helt vandsystem. Paa Mollerup ligger seks
    selvstaendige vandloeb inde i det ene opland — Balling Baek, Grundvad Baek, Hestehave
    Baek, Lilleeng Baek, Neder Ginderup Baek og Roedding Aa — og de har hver deres
    afvandingsomraade. Uden opdelingen staar de som ét polygon med det nederste vandloebs
    navn: arealet er rigtigt, men strukturen forsvinder, og det ser ud som om et enkelt
    beroeringspunkt har gjort hele systemet til «Roedding Aa».

    Hvert vandloebs udloebscelle findes som cellen med stoerst akkumulering under linjens
    knudepunkter. Ét samtidigt gennemloeb goer delene disjunkte af sig selv: et tilloeb
    ligger opstroems for hovedloebet, saa tilloebets etiket vinder i dets eget opland, og
    hovedloebet beholder kun det der afvander direkte til det.
    """
    import numpy as np

    kilde, src_lag = _ogr_lag(vl_sti, vl_lag)
    x0, y1 = graf.gt[0], graf.gt[3]
    x1 = x0 + graf.bredde * graf.gt[1]
    y0 = y1 + graf.hoejde * graf.gt[5]
    src_lag.SetSpatialFilterRect(x0, y0, x1, y1)

    kandidater = []
    src_lag.ResetReading()
    for f in src_lag:
        bedst, bedst_akk = -1, -1.0
        for pkt in linjedele(f.GetGeometryRef()):
            for x, y in pkt:
                i = graf.celle_ved(x, y)
                if i is None or lab[i] == 0:
                    continue
                if akk[i] > bedst_akk:
                    bedst, bedst_akk = i, float(akk[i])
        if bedst < 0:
            continue      # vandloebet ligger ikke i noget vandloebsopland
        navn = f.GetField(navnefelt) if navnefelt else None
        kandidater.append({"navn": str(navn) if navn else "unavngivet", "celle": bedst,
                           "akk_ha": graf.ha(bedst_akk), "forael": int(lab[bedst])})
    kilde = None

    dele = np.zeros(graf.n, dtype=np.int32)
    delliste: list[dict] = []
    if kandidater:
        kandidater.sort(key=lambda k: -k["akk_ha"])
        ider = list(range(1, len(kandidater) + 1))
        dele = graf.opstroems([k["celle"] for k in kandidater], ider, spaerre=projekt)
        dele[lab == 0] = 0
        # Et delareal maa ikke stikke ind i et ANDET vandloebsopland. Sker det, hoerer
        # cellerne til restarealet dér, ikke til dette vandloeb.
        for k, i in zip(kandidater, ider):
            paa_afveje = (dele == i) & (lab != k["forael"])
            if paa_afveje.any():
                dele[paa_afveje] = 0
            k["del_id"] = i

    # Restarealet pr. vandloebsopland: det der afvander til indloebet uden at passere et
    # kortlagt vandloeb undervejs — samme skelnen som mellem direkte og vandloebsopland,
    # bare ét niveau nede.
    #
    # Er restarealet nogle faa celler, er det ikke et delopland men en vektoriseringsrest,
    # og det laegges til det stoerste delareal i samme opland. Et polygon paa 0,01 ha i en
    # leverance ligner en fejl, ogsaa naar arealbalancen holder.
    min_m2 = float(hent(konf, "oplande.min_polygon_areal_m2", 100.0))
    naeste = len(kandidater)
    for d in indloebsdata:
        rest = (lab == d["id"]) & (dele == 0)
        antal_rest = int(rest.sum())
        if antal_rest == 0:
            continue
        soeskende = [k for k in kandidater if k["forael"] == d["id"]]
        if antal_rest * graf.celleareal < min_m2 and soeskende:
            stoerst = max(soeskende, key=lambda k: int((dele == k["del_id"]).sum()))
            dele[rest] = stoerst["del_id"]
            log.skriv(f"      {antal_rest} restcelle(r) lagt til {stoerst['navn']}")
            continue
        naeste += 1
        dele[rest] = naeste
        kandidater.append({"navn": f"{d['navn']} — uden kortlagt tilloeb", "del_id": naeste,
                           "akk_ha": None, "forael": d["id"], "celle": None})

    for k in kandidater:
        k["celler"] = int((dele == k["del_id"]).sum())
        k["areal_ha"] = graf.ha(k["celler"])
        delliste.append(k)

    delliste = [k for k in delliste if k["celler"] > 0]
    log.skriv(f"  underopdeling: {len(delliste)} delarealer efter kortlagt vandloeb")
    for k in sorted(delliste, key=lambda k: -k["areal_ha"]):
        log.skriv(f"      {k['navn']:<38s} {k['areal_ha']:9.1f} ha")
    return dele, delliste


# ---------------------------------------------------------------------------
# Trin 5 — direkte opland
# ---------------------------------------------------------------------------

def trin5_direkte_opland(konf: dict, graf: Stroemningsgraf, trin3: dict, trin4: dict,
                         log: Log) -> dict:
    """
    Restarealet: totalopland minus foreningen af vandloebsoplandene.

    Beregnes ikke selvstaendigt. Definitionen i CLAUDE.md ER en subtraktion, og enhver anden
    fremgangsmaade ville give et areal der ikke summer op. Projektomraadet selv indgaar i
    restarealet, fordi det indgaar i totaloplandet — se logudskriften for dets andel.
    """
    import numpy as np
    from osgeo import gdal

    log.skriv("--- TRIN 5: direkte opland ---")
    derived = absolut(hent(konf, "output.derived_dir"))

    direkte = trin3["total"] & (trin4["etiketter"] == 0)
    n_direkte = int(direkte.sum())
    n_projekt = int((direkte & trin3["projekt"]).sum())
    log.skriv(f"  direkte opland: {graf.ha(n_direkte):.1f} ha "
              f"(heraf projektomraadet selv {graf.ha(n_projekt):.1f} ha)")

    # Hvor meget af restarealet kommer ind koncentreret? De ukortlagte tilloeb delineeres
    # INDEN FOR restarealet. Akkumuleringen i to indloebspunkter kan overlappe, saa summen af
    # dem er ikke et areal og maa ikke citeres som ét — det her er.
    ukortlagte = trin4["ukortlagte"]
    dele = np.zeros(graf.n, dtype=np.int32)
    if ukortlagte:
        ider = list(range(1, len(ukortlagte) + 1))
        dele = graf.opstroems([d["celle"] for d in ukortlagte], ider,
                              spaerre=trin3["projekt"])
        dele[~direkte] = 0
        for d, i in zip(ukortlagte, ider):
            d["del_id"] = i
            d["del_ha"] = graf.ha(int((dele == i).sum()))
        opslugt = [d for d in ukortlagte if d["del_ha"] == 0.0]
        for d in opslugt:
            log.skriv(f"    tilloebet ved ({d['x']:.0f}, {d['y']:.0f}) ligger helt inde i et "
                      "vandloebsopland — dets vand naar projektomraadet gennem vandloebet")
        koncentreret = sum(d["del_ha"] for d in ukortlagte)
        log.skriv(f"  heraf koncentreret afstroemning gennem {len(ukortlagte) - len(opslugt)} "
                  f"ukortlagte tilloeb: {koncentreret:.1f} ha")
        log.skriv(f"  heraf reelt diffus afstroemning: "
                  f"{graf.ha(n_direkte - n_projekt) - koncentreret:.1f} ha "
                  "(projektomraadet ikke medregnet)")
    # Projektomraadet faar sin EGEN etiket i stedet for at ligge i restarealet.
    # Det indgaar i direkte opland efter definitionen — restarealet er totalopland minus
    # vandloebsoplandene, og projektomraadet er en del af totalen. Men det afvander ikke
    # til sig selv, og laa det sammen med den diffuse afstroemning, ville polygonet vaere
    # 83 % projektomraade og se ud som om arealet var sit eget opland.
    projekt_id = len(ukortlagte) + 1
    diffus_id = len(ukortlagte) + 2
    dele[direkte & trin3["projekt"]] = projekt_id
    dele[direkte & (dele == 0)] = diffus_id

    # Arealbalancen paa RASTERNIVEAU er eksakt per konstruktion — kontrollen her fanger
    # en programmeringsfejl, ikke en tolerance. Vektorbalancen tjekkes i trin 6.
    sum_vl = sum(trin4["celletal"].values())
    if n_direkte + sum_vl != int(trin3["total"].sum()):
        raise OplandsFejl(
            f"arealbalancen holder ikke paa celleniveau: {n_direkte:,} + {sum_vl:,} != "
            f"{int(trin3['total'].sum()):,}. Vandloebsoplandene er ikke disjunkte.")
    log.skriv("  arealbalance paa celleniveau: eksakt")

    _skriv_raster(direkte.astype(np.uint8), graf, derived / "05_direkte_opland.tif", gdal.GDT_Byte)
    _skriv_raster(dele, graf, derived / "05_direkte_delarealer.tif", gdal.GDT_Int32)
    return {"direkte": direkte, "celler": n_direkte, "celler_projekt": n_projekt,
            "dele": dele, "diffus_id": diffus_id, "projekt_id": projekt_id,
            "celletal_dele": {v: int((dele == v).sum())
                              for v in range(1, diffus_id + 1) if (dele == v).any()}}


# ---------------------------------------------------------------------------
# Trin 6 — leverance og kvalitetssikring
# ---------------------------------------------------------------------------

def trin6_leverance(konf: dict, graf: Stroemningsgraf, trin3: dict, trin4: dict, trin5: dict,
                    koersel_id: str, log: Log) -> dict:
    """Vektoriserer, skriver GeoPackagen med de kraevede attributter, og koerer QA-saettet."""
    import numpy as np
    from qgis.core import QgsGeometry, QgsPointXY

    log.skriv("--- TRIN 6: leverance og QA ---")
    derived = absolut(hent(konf, "output.derived_dir"))
    gpkg = absolut(hent(konf, "output.gpkg"))
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    epsg = int(hent(konf, "crs.epsg"))
    dato = dt.date.today().isoformat()
    navn = str(hent(konf, "projekt.navn", "projekt"))

    forbehold = []
    if trin3["kant_celler"]:
        forbehold.append(
            "AFSKAARET: oplandet naar rasterkanten i "
            + ", ".join(f"{k} {v} celler" for k, v in trin3["kant_detaljer"].items())
            + " — arealet er et minimum")
    forbehold.append("kloak- og regnvandsoplande er ikke i scope; i urbane delarealer "
                     "foelger vandet roer, ikke terraen")
    gennemstroemning = [d for d in trin4["indloeb"] if d["karakter"] == "gennemstroemning"]
    if gennemstroemning:
        forbehold.append(
            "GENNEMSTROEMNING: "
            + "; ".join(f"{d['navn']} loeber igennem projektomraadet med {d['akk_ha']:.0f} ha "
                        f"opland og ud igen med {d['udloeb_akk_ha']:.0f} ha"
                        for d in gennemstroemning)
            + " — det areal afvander ikke til fladen, men passerer i en kanal")

    koncentreret = sum(d.get("del_ha", 0.0) for d in trin4["ukortlagte"])
    if koncentreret > 0:
        forbehold.append(
            f"{sum(1 for d in trin4['ukortlagte'] if d.get('del_ha')):d} tilloeb paa "
            f"tilsammen {koncentreret:.0f} ha loeber ind som koncentreret afstroemning uden "
            "at ligge paa et kortlagt vandloeb og indgaar derfor i det direkte opland — "
            "se lagene indloebspunkter (type=terraen) og direkte_delarealer")
    forbehold.append("fordelingen mellem direkte og vandloebsopland afhaenger af hvor "
                     "fuldstaendigt vandloebslaget er; AIS indeholder kun de stoerre forloeb")
    forbehold_tekst = ". ".join(forbehold)

    # -- vektorisering ---------------------------------------------------
    total_geom = _polygoniser(
        derived / "03_totalopland.tif", derived / "03_totalopland.gpkg", graf,
        {1: int(trin3["total"].sum())}, log, "totalopland")[1]
    direkte_geom = _polygoniser(
        derived / "05_direkte_opland.tif", derived / "05_direkte_opland.gpkg", graf,
        {1: trin5["celler"]}, log, "direkte opland")[1]
    # Er alle vandloebsindloeb genindloeb, findes der ingen vandloebsoplande overhovedet,
    # og saa er hele totaloplandet direkte opland. Det er et gyldigt resultat, ikke en fejl.
    vl_geom = _polygoniser(
        derived / "04_vandloebsoplande.tif", derived / "04_vandloebsoplande.gpkg", graf,
        trin4["celletal"], log, "vandloebsoplande") if trin4["celletal"] else {}

    metode_faelles = (f"D8 paa {graf.opl:g} m, breaching+fill, stream burning med monotont "
                      f"fald; koert paa {hent(konf, 'input.dem')}")

    faelles_felter = [("areal_ha", "tal"), ("metode", "tekst"),
                      ("koersel_id", "tekst"), ("dato", "tekst"), ("forbehold", "tekst")]

    # -- lag 1: totalopland (opretter filen foerste gang) ----------------
    ny_fil = not gpkg.exists()
    if not ny_fil:
        log.skriv(f"  {gpkg.name} findes — lagene erstattes ét ad gangen")
    _skriv_lag(gpkg, "total_opland", "MultiPolygon", epsg, faelles_felter,
               [(total_geom, [total_geom.area() / 1e4,
                              "totalopland: alle celler hvis stroemningsvej ender i "
                              f"projektomraadet, {metode_faelles}", koersel_id, dato,
                              forbehold_tekst])], log, foerste=ny_fil)

    # -- lag 2: vandloebsoplande -----------------------------------------
    vl_felter = [("indloeb_id", "heltal"), ("vandloeb_navn", "tekst"), ("orden", "tal"),
                 ("karakter", "tekst"), ("udloeb_akk_ha", "tal"),
                 ("indloeb_x", "tal"), ("indloeb_y", "tal"), ("afstand_kortlagt_m", "tal"),
                 ("akkumulering_ha", "tal")] + faelles_felter
    raekker = []
    for d in sorted(trin4["indloeb"], key=lambda d: -d["areal_ha"]):
        g = vl_geom[d["id"]]
        raekker.append((g, [d["id"], d["navn"], d["orden"], d["karakter"],
                            d.get("udloeb_akk_ha"), d["x"], d["y"],
                            d["afstand_m"], d["akk_ha"], g.area() / 1e4,
                            "vandloebsopland: opstroems for indloebspunktet, gjort disjunkt "
                            f"mod nestede indloeb, {metode_faelles}",
                            koersel_id, dato, forbehold_tekst]))
    _skriv_lag(gpkg, "vandloebsoplande", "MultiPolygon", epsg, vl_felter, raekker, log)

    # -- lag 2b: udloebspunkter for de forloeb der passerer igennem -------
    gennem = [d for d in trin4["indloeb"] if d["karakter"] == "gennemstroemning"]
    if gennem:
        _skriv_lag(gpkg, "udloebspunkter", "Point", epsg,
                   [("indloeb_id", "heltal"), ("vandloeb_navn", "tekst"),
                    ("akkumulering_ha", "tal"), ("koersel_id", "tekst"), ("dato", "tekst")],
                   [(QgsGeometry.fromPointXY(QgsPointXY(d["udloeb_x"], d["udloeb_y"])),
                     [d["id"], d["navn"], d["udloeb_akk_ha"], koersel_id, dato])
                    for d in gennem], log)

    # -- lag 3: direkte opland -------------------------------------------
    _skriv_lag(gpkg, "direkte_opland", "MultiPolygon", epsg,
               [("heraf_projekt_ha", "tal")] + faelles_felter,
               [(direkte_geom, [graf.ha(trin5["celler_projekt"]), direkte_geom.area() / 1e4,
                                "direkte opland: totalopland minus foreningen af "
                                f"vandloebsoplandene (restareal), {metode_faelles}",
                                koersel_id, dato, forbehold_tekst])], log)

    # -- lag 2c: vandloebsoplandene delt op pr. kortlagt vandloeb ---------
    #    Uden den her staar et helt vandsystem som ét polygon med det nederste vandloebs
    #    navn, fordi ét punkt paa graensen roerte en kortlagt linje. Arealet er rigtigt,
    #    men det ser ud som om alt opstroems «hoerer til» det ene vandloeb.
    if trin4["delliste"]:
        vl_del_geom = _polygoniser(
            derived / "04_vandloebsoplande_dele.tif",
            derived / "04_vandloebsoplande_dele.gpkg", graf,
            trin4["celletal_dele"], log, "vandloebsoplande, delarealer")
        vl_del_felter = [("del_id", "heltal"), ("vandloeb_navn", "tekst"),
                         ("indloeb_id", "heltal"), ("akkumulering_ha", "tal")] + faelles_felter
        vl_del_raekker = []
        for k in sorted(trin4["delliste"], key=lambda k: -k["areal_ha"]):
            g = vl_del_geom.get(k["del_id"])
            if g is None:
                continue
            vl_del_raekker.append((g, [k["del_id"], k["navn"], k["forael"], k["akk_ha"],
                                       g.area() / 1e4,
                                       "delareal af vandloebsopland: opstroems for ét "
                                       f"kortlagt vandloeb, {metode_faelles}",
                                       koersel_id, dato, forbehold_tekst]))
        _skriv_lag(gpkg, "vandloebsoplande_delarealer", "MultiPolygon", epsg,
                   vl_del_felter, vl_del_raekker, log)

    # -- lag 3b: hvordan det direkte opland kommer ind ---------------------
    #    Restarealet er ét tal, men ikke ét faenomen: en del kommer koncentreret gennem
    #    groefter, resten som diffus afstroemning over graensen. Forskellen afgoer hvordan
    #    vandet kan haandteres, og den kan ikke ses paa det samlede polygon.
    del_geom = _polygoniser(
        derived / "05_direkte_delarealer.tif", derived / "05_direkte_delarealer.gpkg", graf,
        trin5["celletal_dele"], log, "direkte delarealer")
    del_felter = [("del_id", "heltal"), ("kilde", "tekst"), ("naermeste_vandloeb", "tekst"),
                  ("afstand_kortlagt_m", "tal")] + faelles_felter
    del_raekker = []
    for d in sorted(trin4["ukortlagte"], key=lambda d: -d.get("del_ha", 0.0)):
        g = del_geom.get(d.get("del_id"))
        if g is None:
            continue
        del_raekker.append((g, [d["del_id"], "koncentreret tilloeb uden kortlagt vandloeb",
                                d["navn"], d["afstand_m"], g.area() / 1e4,
                                "delareal af direkte opland: opstroems for et indloebspunkt "
                                f"der ikke ligger paa et kortlagt vandloeb, {metode_faelles}",
                                koersel_id, dato, forbehold_tekst]))
    if trin5["projekt_id"] in del_geom:
        g = del_geom[trin5["projekt_id"]]
        del_raekker.append((g, [trin5["projekt_id"], "projektomraadet selv", None, None,
                                g.area() / 1e4,
                                "delareal af direkte opland: projektomraadet, som indgaar i "
                                "totaloplandet og dermed i restarealet — det afvander ikke "
                                f"til sig selv, {metode_faelles}",
                                koersel_id, dato, forbehold_tekst]))
    if trin5["diffus_id"] in del_geom:
        g = del_geom[trin5["diffus_id"]]
        del_raekker.append((g, [trin5["diffus_id"], "diffus afstroemning", None, None,
                                g.area() / 1e4,
                                "delareal af direkte opland: afstroemning over graensen "
                                f"uden et samlende tilloeb, {metode_faelles}",
                                koersel_id, dato, forbehold_tekst]))
    _skriv_lag(gpkg, "direkte_delarealer", "MultiPolygon", epsg, del_felter, del_raekker, log)

    # -- lag 4: indloebspunkter, baade kortlagte og ukortlagte tilloeb ----
    #    De ukortlagte hoerer arealmaessigt til det direkte opland, men de er koncentreret
    #    afstroemning. Uden dem paa kortet ser 536 ha diffus afstroemning ud som noget helt
    #    andet end det er.
    p_felter = [("indloeb_id", "heltal"), ("type", "tekst"), ("vandloeb_navn", "tekst"),
                ("orden", "tal"), ("afstand_kortlagt_m", "tal"), ("akkumulering_ha", "tal"),
                ("opland_ha", "tal"), ("henfoert_til", "tekst"),
                ("koersel_id", "tekst"), ("dato", "tekst")]
    punkt_raekker = [
        (QgsGeometry.fromPointXY(QgsPointXY(d["x"], d["y"])),
         [d["id"], "vandloeb", d["navn"], d["orden"], d["afstand_m"], d["akk_ha"],
          d["areal_ha"], "vandloebsopland", koersel_id, dato])
        for d in sorted(trin4["indloeb"], key=lambda d: -d["areal_ha"])]
    # opland_ha for de ukortlagte er det DISJUNKTE delareal fra trin 5, ikke akkumuleringen:
    # to naboindloebs akkumuleringer overlapper, og lagt sammen ville de overdrive arealet.
    punkt_raekker += [
        (QgsGeometry.fromPointXY(QgsPointXY(d["x"], d["y"])),
         [d.get("del_id"), "terraen", d["navn"], d["orden"], d["afstand_m"], d["akk_ha"],
          d.get("del_ha"), "direkte opland", koersel_id, dato])
        for d in sorted(trin4["ukortlagte"], key=lambda d: -d.get("del_ha", 0.0))]
    # Genindloeb har intet selvstaendigt opland, men de er vaerd at kunne se: det er dér
    # vandloebet vender tilbage i projektomraadet efter at have vaeret udenfor.
    punkt_raekker += [
        (QgsGeometry.fromPointXY(QgsPointXY(d["x"], d["y"])),
         [None, "genindloeb", d["navn"], d["orden"], d["afstand_m"], d["akk_ha"],
          0.0, d.get("aarsag", "intet selvstaendigt opland"), koersel_id, dato])
        for d in sorted(trin4["genindloeb"], key=lambda d: -d["akk_ha"])]
    _skriv_lag(gpkg, "indloebspunkter", "Point", epsg, p_felter, punkt_raekker, log)

    # -- lag 5: projektomraadet, saa leverancen er selvstaendig ----------
    from osgeo import gdal
    projekt_geom = _polygoniser(
        _skriv_raster(trin3["projekt"].astype(np.uint8), graf,
                      derived / "03_projektomraade.tif", gdal.GDT_Byte),
        derived / "03_projektomraade.gpkg", graf,
        {1: int(trin3["projekt"].sum())}, log, "projektomraade")[1]
    _skriv_lag(gpkg, "projektomraade", "MultiPolygon", epsg,
               [("areal_ha", "tal"), ("navn", "tekst"), ("koersel_id", "tekst")],
               [(projekt_geom, [projekt_geom.area() / 1e4, navn, koersel_id])], log)

    # -- lag 6: modellens vandveje, til visuel kontrol -------------------
    if bool(hent(konf, "output.vandveje_lag", True)):
        veje = derived / "06_vandveje.gpkg"
        _frigiv(veje)
        koer(kraev_algoritme("raster_streams_to_vector"), {
            "streams_raster": str(derived / "02_streams.tif"),
            "d8_pntr": str(derived / "02_d8_pointer.tif"),
            "esri_pntr": False,
            "all_vertices": True,
            "output": str(veje),
        }, log, "vektoriser modellens vandveje")
        ds_veje, vlag = _ogr_lag(veje, None)
        taerskel = float(hent(konf, "stroemning.vandloeb_taerskel_ha"))
        linjer = [(g, [koersel_id, taerskel])
                  for g in (_qgs_geometri(f.GetGeometryRef()) for f in vlag)
                  if g is not None]
        ds_veje = None
        if linjer:
            _skriv_lag(gpkg, "vandveje_beregnet", "MultiLineString", epsg,
                       [("koersel_id", "tekst"), ("taerskel_ha", "tal")], linjer, log)

    # -- QA ---------------------------------------------------------------
    qa = _qa(konf, graf, trin3, trin4, trin5, total_geom, direkte_geom, vl_geom, log)

    log_dir = absolut(hent(konf, "output.log_dir"))
    import yaml
    with open(log_dir / f"{koersel_id}_qa.yml", "w", encoding="utf-8") as f:
        yaml.safe_dump({"koersel_id": koersel_id, "dato": dato, "qa": qa,
                        "forbehold": forbehold},
                       f, allow_unicode=True, sort_keys=False)

    if bool(hent(konf, "output.hillshade", False)):
        hs = absolut(hent(konf, "output.hillshade_fil", None) or (gpkg.parent / "hillshade.tif"))
        if not spring_over(hs, derived / "00_dem_analyse.tif"):
            koer("gdal:hillshade", {
                "INPUT": str(derived / "00_dem_analyse.tif"), "BAND": 1, "Z_FACTOR": 3.0,
                "SCALE": 1.0, "AZIMUTH": 315.0, "ALTITUDE": 45.0, "COMPUTE_EDGES": True,
                "MULTIDIRECTIONAL": False, "OUTPUT": str(hs),
            }, log, "hillshade til visuel kontrol")

    log.skriv(f"  leverance skrevet: {gpkg}")
    return qa


def _qa(konf: dict, graf: Stroemningsgraf, trin3: dict, trin4: dict, trin5: dict,
        total_geom, direkte_geom, vl_geom: dict, log: Log) -> dict:
    """
    QA-saettet paa VEKTORLAGENE — det er dem der leveres, ikke rasterne.

    Rasterbalancen er allerede kontrolleret i trin 5 og er eksakt. Her kontrolleres at
    vektoriseringen har bevaret den, at polygonerne ikke overlapper, og at intet opland er
    saa lille at det maa vaere en fejl.
    """
    log.skriv("  --- QA ---")
    resultat: dict[str, Any] = {}
    fejlede = []

    def tjek(navn: str, bestaaet: bool, detalje: str) -> None:
        resultat[navn] = {"bestaaet": bool(bestaaet), "detalje": detalje}
        log.skriv(f"    [{'OK ' if bestaaet else 'FEJL'}] {navn}: {detalje}")
        if not bestaaet:
            fejlede.append(navn)

    a_total = total_geom.area()
    a_direkte = direkte_geom.area()
    a_vl = sum(g.area() for g in vl_geom.values())
    tol = float(hent(konf, "oplande.arealbalance_tolerance_pct", 0.1))
    afvig = abs(a_direkte + a_vl - a_total) / a_total * 100.0
    tjek("arealbalance", afvig <= tol,
         f"direkte {a_direkte/1e4:.2f} ha + vandloeb {a_vl/1e4:.2f} ha "
         f"= {(a_direkte+a_vl)/1e4:.2f} ha mod total {a_total/1e4:.2f} ha "
         f"(afvigelse {afvig:.4f} %, tolerance {tol} %)")

    overlap = 0.0
    ider = sorted(vl_geom)
    for i in range(len(ider)):
        for j in range(i + 1, len(ider)):
            snit = vl_geom[ider[i]].intersection(vl_geom[ider[j]])
            if snit and not snit.isEmpty():
                overlap += snit.area()
    tjek("overlap_vandloebsoplande", overlap < 1.0,
         f"{overlap:.3f} m2 samlet overlap mellem {len(ider)} oplande")

    min_ha = float(hent(konf, "oplande.min_vandloebsopland_ha", 1.0))
    mindste = min((d["areal_ha"] for d in trin4["indloeb"]), default=None)
    tjek("minimumsareal", mindste is None or mindste >= min_ha,
         "ingen vandloebsoplande at maale" if mindste is None else
         f"mindste vandloebsopland {mindste:.2f} ha, graense {min_ha} ha"
         + (f" ({len(trin4['genindloeb'])} genindloeb uden selvstaendigt opland udeladt)"
            if trin4["genindloeb"] else ""))

    tjek("antal_oplande_matcher_indloeb", len(vl_geom) == len(trin4["indloeb"]),
         f"{len(vl_geom)} polygoner mod {len(trin4['indloeb'])} indloebspunkter")

    # Det staerkeste uafhaengige tjek der findes her: mit eget opstroems-gennemloeb mod
    # Whitebox' egen akkumulering. To adskilte implementeringer af samme stoerrelse — hvis
    # D8-afkodningen eller sporingen var gal, ville de ikke kunne ramme hinanden.
    # Sammenlignes mod sporingen UDEN spaerre: akkumuleringen kender ikke projektomraadet,
    # saa det er den stoerrelse der svarer til den. Med spaerren ville et genindloeb se ud
    # som en fejl, og sammenligningen ville miste sin vaerdi som uafhaengig kontrol.
    uafhaengige = [d for d in trin4["indloeb"] + trin4["genindloeb"] if not d.get("nestede")]
    vaerst, hvem = 0.0, "-"
    for d in uafhaengige:
        rel = abs(d["areal_uspaerret_ha"] - d["akk_ha"]) / max(d["akk_ha"], 1e-9)
        if rel > vaerst:
            vaerst, hvem = rel, d["navn"]
    tjek("sporing_mod_akkumulering", vaerst < 0.005,
         f"stoerste afvigelse mellem sporet opland og Whitebox' akkumulering: {vaerst:.4%}"
         + (f" ({hvem})" if vaerst > 0 else "")
         + f", maalt paa {len(uafhaengige)} ikke-nestede indloeb")

    a_dele = sum(v for v in trin5["celletal_dele"].values()) * graf.celleareal
    tjek("delarealer_summer_til_direkte", abs(a_dele - a_direkte) < 1.0,
         f"delarealerne af det direkte opland summer til {a_dele/1e4:.2f} ha "
         f"mod {a_direkte/1e4:.2f} ha")

    # Fragmentering kan kun opstaa ved udklipningen af projektomraadet, ikke ved sporingen.
    # Smaastykker under minimumsarealet er vektoriseringsstoej; alt derover er et opland
    # der er skaaret over, og saa daekker polygonet omraader der ikke haenger sammen.
    graense = trin4["min_polygon_m2"]
    brudte = {i: st for i, st in trin4["fragmenter"].items()
              if any(ha * 1e4 >= graense for ha in st[1:])}
    tjek("oplande_sammenhaengende", not brudte,
         "alle vandloebsoplande er ét sammenhaengende stykke" if not brudte else
         "; ".join(f"indloeb {i} delt i {len(st)} stykker: "
                   + ", ".join(f"{ha:.2f} ha" for ha in st[:5]) for i, st in brudte.items()))

    a_vl_dele = sum(trin4["celletal_dele"].values()) * graf.celleareal
    tjek("delarealer_summer_til_vandloebsopland", abs(a_vl_dele - a_vl) < 1.0,
         f"de {len(trin4['delliste'])} delarealer pr. kortlagt vandloeb summer til "
         f"{a_vl_dele/1e4:.2f} ha mod {a_vl/1e4:.2f} ha")

    # contains() er ubrugelig her: delene deler graense med totaloplandet, og en delt graense
    # taeller ikke som indeholdt. Arealet af det der stikker udenfor er det rigtige maal.
    udenfor = sum(g.difference(total_geom).area() for g in [direkte_geom, *vl_geom.values()])
    tjek("dele_inden_for_total", udenfor < 1.0,
         f"{udenfor:.3f} m2 af deloplandene ligger uden for totaloplandet")

    tjek("kantkontaminering", trin3["kant_celler"] == 0,
         f"{trin3['kant_celler']} celler paa rasterkanten"
         + ("" if not trin3["kant_celler"] else
            " — " + ", ".join(f"{k} {v}" for k, v in trin3["kant_detaljer"].items())))

    resultat["_fejlede"] = fejlede
    if fejlede:
        log.advar(f"QA: {len(fejlede)} tjek fejlede: {', '.join(fejlede)}")
    else:
        log.skriv("    alle QA-tjek bestaaet")
    return resultat


# ---------------------------------------------------------------------------
# Hovedprogram
# ---------------------------------------------------------------------------

def gem_parameterlog(konf: dict, log_dir: Path, koersel_id: str) -> None:
    """Kopierer de faktisk anvendte parametre, saa resultatet kan reproduceres."""
    import yaml
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / f"{koersel_id}_parametre.yml", "w", encoding="utf-8") as f:
        yaml.safe_dump({"koersel_id": koersel_id,
                        "tidspunkt": dt.datetime.now().isoformat(timespec="seconds"),
                        "parametre": konf},
                       f, allow_unicode=True, sort_keys=False)


class Afbrudt(RuntimeError):
    """Brugeren stoppede koerslen. Ikke en fejl — der skal ikke skrives fejlrapport."""


def koer_analyse(konf: dict, log: Log, koersel_id: str, foerste_trin: int = 0,
                 afbryd=None, fremdrift=None) -> dict:
    """
    Hele kaeden, trin 0-6. Den ENESTE vej gennem analysen.

    Baade kommandolinjen og QGIS-vaerktoejet kalder herind. To kodestier ville betyde to
    saet parametre der langsomt kommer til at betyde noget forskelligt.

    `afbryd` er en funktion der returnerer sandt naar brugeren har stoppet koerslen;
    `fremdrift(procent, tekst)` melder tilbage undervejs. Begge er valgfri.
    """
    def _stop(procent: int, tekst: str) -> None:
        if fremdrift is not None:
            fremdrift(procent, tekst)
        if afbryd is not None and afbryd():
            raise Afbrudt("koerslen blev afbrudt")

    registrer_whitebox(log)
    log_dir = absolut(hent(konf, "output.log_dir"))
    gem_parameterlog(konf, log_dir, koersel_id)

    derived = absolut(hent(konf, "output.derived_dir"))
    dem_analyse = derived / "00_dem_analyse.tif"
    dem_hydro = derived / "01_dem_hydro.tif"

    _stop(0, "klargoering")
    if foerste_trin <= 0:
        dem_analyse = trin0_klargoer(konf, log)
    elif not dem_analyse.exists():
        raise OplandsFejl(f"Trin {foerste_trin} kraever {dem_analyse}, som ikke findes. "
                          "Koer fra trin 0.")

    _stop(20, "hydrologisk konditionering")
    if foerste_trin <= 1:
        dem_hydro = trin1_konditioner(konf, dem_analyse, log)
    elif not dem_hydro.exists():
        raise OplandsFejl(f"Trin {foerste_trin} kraever {dem_hydro}, som ikke findes. "
                          "Koer fra trin 1.")

    _stop(50, "stroemningsretning og -akkumulering")
    stroem = {"pointer": derived / "02_d8_pointer.tif",
              "akkumulering": derived / "02_d8_akkumulering.tif",
              "streams": derived / "02_streams.tif"}
    if foerste_trin <= 2:
        stroem = trin2_stroemning(konf, dem_hydro, log)
    else:
        for sti in stroem.values():
            if not sti.exists():
                raise OplandsFejl(f"Trin {foerste_trin} kraever {sti}. Koer fra trin 2.")

    # Trin 3-5 koeres altid samlet. De deler den samme stroemningsgraf i hukommelsen, og
    # delt i tre uafhaengige koersler ville man kunne komme til at blande et totalopland
    # med vandloebsoplande fra en anden konditionering — uden at noget melder fejl.
    _stop(65, "bygger stroemningsgrafen")
    log.skriv("--- bygger stroemningsgrafen ---")
    graf = Stroemningsgraf(stroem["pointer"], dem_hydro, log)

    _stop(75, "totalopland")
    t3 = trin3_totalopland(konf, graf, log)
    _stop(82, "vandloebsoplande")
    t4 = trin4_vandloebsoplande(konf, graf, t3, log)
    _stop(88, "direkte opland")
    t5 = trin5_direkte_opland(konf, graf, t3, t4, log)
    _stop(92, "leverance og QA")
    qa = trin6_leverance(konf, graf, t3, t4, t5, koersel_id, log)
    _stop(100, "faerdig")

    return {"gpkg": absolut(hent(konf, "output.gpkg")), "qa": qa,
            "totalopland_ha": graf.ha(int(t3["total"].sum())),
            "vandloebsopland_ha": graf.ha(sum(t4["celletal"].values())),
            "direkte_ha": graf.ha(t5["celler"]),
            "projekt_ha": graf.ha(t5["celler_projekt"]),
            "antal_indloeb": len(t4["indloeb"]),
            "gennemstroemning": [d for d in t4["indloeb"]
                                 if d["karakter"] == "gennemstroemning"],
            "gennemstroemning_ha": graf.ha(sum(
                t4["celletal"][d["id"]] for d in t4["indloeb"]
                if d["karakter"] == "gennemstroemning")),
            "kant_celler": t3["kant_celler"],
            "graf": graf, "trin3": t3, "trin4": t4, "trin5": t5}


def skriv_resultat(res: dict, log: Log) -> None:
    """Opsummeringen til sidst. Samme ordlyd uanset om kaldet kom fra CLI eller QGIS."""
    log.skriv("")
    log.skriv("RESULTAT")
    log.skriv(f"  totalopland          {res['totalopland_ha']:10.1f} ha")
    log.skriv(f"  heraf vandloebsopland{res['vandloebsopland_ha']:10.1f} ha "
              f"fordelt paa {res['antal_indloeb']} indloeb")
    log.skriv(f"  heraf direkte opland {res['direkte_ha']:10.1f} ha "
              f"(inkl. projektomraadet {res['projekt_ha']:.1f} ha)")
    # De to stoerrelser maa ikke laegges sammen ukritisk: gennemstroemning passerer i en
    # kanal, mens resten afvander til fladen. Derfor staar de hver for sig her.
    if res["gennemstroemning"]:
        log.skriv("")
        log.skriv(f"  heraf GENNEMSTROEMNING{res['gennemstroemning_ha']:9.1f} ha — "
                  "vandet passerer tvaers over arealet og loeber ud igen:")
        for d in res["gennemstroemning"]:
            log.skriv(f"      {d['navn']}: ind {d['akk_ha']:.0f} ha, "
                      f"ud {d['udloeb_akk_ha']:.0f} ha")
        rest = res["totalopland_ha"] - res["gennemstroemning_ha"]
        log.skriv(f"  afvander TIL fladen  {rest:10.1f} ha "
                  "(totalopland minus gennemstroemning)")
    log.skriv(f"  leverance: {res['gpkg']}")


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Oplandsanalyse: direkte opland og vandloebsoplande")
    p.add_argument("--konfig", type=Path, default=STANDARD_KONFIG)
    p.add_argument("--trin", type=int, default=0,
                   help="foerste trin der skal koeres (0-6)")
    args = p.parse_args(list(argv) if argv is not None else None)

    global AKTIV_KONFIG
    AKTIV_KONFIG = args.konfig.resolve()
    konf = indlaes_konfiguration(args.konfig)

    koersel_id = hent(konf, "projekt.koersel_id", None) or dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = absolut(hent(konf, "output.log_dir"))
    log = Log(log_dir / f"{koersel_id}.log", koersel_id)

    app, vi_startede = start_qgis()
    try:
        res = koer_analyse(konf, log, koersel_id, foerste_trin=args.trin)
        skriv_resultat(res, log)
        if res["qa"].get("_fejlede") and bool(hent(konf, "qa.stop_ved_fejlet_accepttest", True)):
            log.advar("QA-tjek fejlede — se ovenfor. Lagene er skrevet, men resultatet "
                      "skal ikke leveres videre uden at hvert fejlet tjek er forklaret.")
            return 2
        return 0

    except Afbrudt as e:
        log.skriv(str(e))
        return 130
    except OplandsFejl as e:
        log.skriv(f"FEJL: {e}")
        return 1
    finally:
        log.luk()
        if vi_startede:
            app.exitQgis()


if __name__ == "__main__":
    sys.exit(main())
