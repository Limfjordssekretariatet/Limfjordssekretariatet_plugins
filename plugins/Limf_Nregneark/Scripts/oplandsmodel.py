"""Fælles grundlag for de to trin der kører oplandsmodellen.

  "Beregn strømningsveje"  (Beregn_Stroemningsveje_Script.py)
      henter terrænmodellen, konditionerer den og udleder strømningsretning,
      akkumulering og vandløbsnetværk — trin 0-2 i oplande.py.

  "Beregn oplande"         (Udpeg_Oplande_N_Script.py)
      sporer totalopland, vandløbsoplande og direkte opland ovenpå det — trin 3-6.

De to trin SKAL regne på præcis samme konfiguration, og derfor bygges den her.
To grunde, og den anden er den vigtigste:

  1. To sæt parametre i to filer kommer langsomt til at betyde noget forskelligt.
  2. oplande.py afgør om et mellemresultat kan genbruges ved at sammenligne dets
     alder med konfigurationsfilens. Skriver de to trin hver sin konfiguration —
     eller bare samme værdier i en anden rækkefølge — er filen nyere end
     mellemresultaterne, og oplandstrinnet konditionerer terrænet forfra. Det er
     den dyre del af kæden (resampling, brænding, breaching, fill).

Standardværdierne står som konstanter herunder og bruges som dialogstandard i
BEGGE trin, så de ikke kan komme til at afvige. De er målt og afprøvet i
kildeprojektet bag oplande.py — se dets config/parametre.yml for begrundelserne
før du ændrer dem.
"""

import datetime as dt
import os
import sys
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsProject,
    QgsVectorFileWriter,
    QgsProcessingException,
    QgsRasterLayer,
    QgsVectorLayer,
)

# Pluginnets egne mapper, udledt af DENNE fils placering.
#
# utils.find_grunddata() gør det samme, men 'utils' er ét globalt modul i QGIS:
# ligger en anden kopi af pluginnet (fx Vaadomraade_Modeller_v2) først i
# Processings SCRIPTS_FOLDERS, er det DENS utils der ender i sys.modules, og så
# peger opslaget på den anden kopis Grunddata. Referencedata og beregningskode
# skal komme fra samme kopi som resten — ellers regner man på et andet
# vandløbsnetværk end man tror.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
GRUNDDATA = os.path.join(PLUGIN_ROOT, 'Grunddata')
SCRIPTS = os.path.join(PLUGIN_ROOT, 'Scripts')

# Referencedata i Grunddata.
#
# Vandløbsnetværket er LINJER (AIS, ORDEN 1-4 med VLNAVN). Det er ikke det samme
# lag som Grunddata/Vandloeb/Vandloeb_DK.shp, der er polygoner — oplandsmodellen
# skal have linjer, både til brændingen og til at afgøre om et indløb ligger på
# et kortlagt vandløb.
#
# Hvor fuldstændigt netværket er, styrer HELE fordelingen mellem direkte opland
# og vandløbsopland: et tilløb der ikke findes i laget, tæller som direkte
# opland. Derfor er det en parameter man kan skifte ud, ikke en detalje.
VANDLOEB_REL = os.path.join('Vandloeb_AIS', 'AIS_Jylland_med_inspire_geometri.shp')
DHMLINJE_REL = os.path.join('DHMLinje', 'DHMLinje.shp')

# Højdemodel-kandidater i Outputfiler_<omraade>, i prioriteret rækkefølge.
# DHM_raa.tif er terrænmodellen FØR nogen konditionering — oplandsmodellen laver
# sin egen og skal have råt terræn. Hoejdemodel.tif er modellens KONDITIONEREDE
# terræn (den er allerede brændt, breachet og fyldt); bruges den som input, sker
# det hele to gange, og fyldte lavninger kan ikke gendannes.
DEM_RAA = 'DHM_raa.tif'
DEM_KONDITIONERET = 'Hoejdemodel.tif'
DEM_KANDIDATER = (DEM_RAA, DEM_KONDITIONERET, 'Hoejdemodel.sdat')

# Hele pluginnet regner i EPSG:25832 (DHM-downloaden leverer altid i 25832).
# Bruges kun hvis højdemodellen ikke selv oplyser en EPSG-kode — SAGA-formatet
# .sdat taber den nemt.
STANDARD_EPSG = 25832

# Nøgletal der skal frem til brugeren, ikke bare i logfilen. Interfacets
# fremdriftsvindue viser kun vent-beskeder, så alt andet forsvinder. Linjer med
# dette mærke samles op og vises når trinnet er færdigt.
NOEGLETAL = '▸ '


def meld(feedback, tekst):
    """Et nøgletal brugeren skal se — ikke en advarsel, men heller ikke støj."""
    if feedback is not None:
        feedback.pushInfo(NOEGLETAL + tekst)


ARBEJDSMAPPE = 'Oplandsmodel'   # mellemresultater og log, under Outputfiler
LEVERANCE = 'Oplande.gpkg'      # alle oplandslag i én GeoPackage, under Outputfiler
CHANNEL_NETWORKS = 'Channel_Networks.gpkg'

# ── Standardværdier — dialogstandard i BEGGE trin ────────────────────────────
STD_OPLOESNING = 2.0        # analyse- og downloadopløsning (m)
STD_STROEM_TAERSKEL = 250.0  # akkumuleringstærskel for beregnet vandløb (ha)
STD_SNAP_CELLER = 5          # maks. afstand kortlagt vandløb -> indløb (celler)
STD_BURN = True              # brænd vandløbsnetværket ind
STD_BURN_DYBDE = 1.0         # brændingsdybde (m)
STD_BREACH_DIST = 25         # breaching-søgeafstand (celler); køretid vokser kvadratisk
# Hvilken fill der rydder op efter breaching.
#
# 'wang_and_liu' og ikke Whitebox' egen 'fill_depressions'. Den sidste
# forsøger at allokere 128 GiB på rasterer over ca. 30 mio. celler og river
# processen med sig uden fejlbesked — det rækker til et opland på ca. 100 km2,
# og 24 af Limfjordens oplande er større end det. Målt mod hinanden på opland_284
# (36 km2, 30 mio. celler) afviger det konditionerede terræn på 2,6 % af
# cellerne, vandoplandet med 0,95 %, og træfprocenten mod de kortlagte vandløb
# er bedre med Wang & Liu (94,0 mod 85,3 %).
STD_FILL_METODE = 'wang_and_liu'
STD_MIN_POLYGON = 100.0      # fjern øer og huller under (m²)
STD_TAERSKEL_HA = 50.0       # mindste tilløb der udpeges selvstændigt (ha)
STD_STOP_VED_KANT = False    # stop hvis oplandet når rasterkanten
STD_DOWNLOAD_BUFFER = 5000.0  # buffer om projektområdet ved DHM-download (m)


def grunddata_mappe():
    """Pluginnets egen Grunddata-mappe, med utils-opslaget som reserve."""
    if os.path.isdir(GRUNDDATA):
        return GRUNDDATA
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    from utils import find_grunddata
    return find_grunddata()


def _standard_sti(relativ):
    """Sti til et referencedatasæt i Grunddata — kun hvis filen faktisk er der.

    En sti der peger på noget der ikke findes, må ikke blive stående i dialogen og
    lyve: QGIS afviser den med "Incorrect parameter value", længe før koden når at
    forklare hvad der mangler. Et tomt felt giver den rigtige fejlbesked.
    """
    mappe = grunddata_mappe()
    if not mappe:
        return None
    sti = os.path.join(mappe, relativ)
    return sti if os.path.isfile(sti) else None


def vandloeb_standard():
    return _standard_sti(VANDLOEB_REL)


def grunddata_modul():
    """Modulet der henter de store referencedata — eller None.

    N-regnearkspluginnet henter dem fra en release og lægger dem i QGIS-profilen;
    Udpeg opland har dem liggende lokalt og har ikke modulet. Det er derfor
    valgfrit.
    """
    sti = os.path.join(SCRIPTS, 'grunddata.py')
    if not os.path.isfile(sti):
        return None
    try:
        import importlib.util

        # Fingeraftryk i navnet, saa en opdateret fil ikke hentes fra cachen.
        st = os.stat(sti)
        modulnavn = (f'_grunddata_{os.path.basename(PLUGIN_ROOT).lower()}'
                     f'_{st.st_size}_{int(st.st_mtime)}')
        modul = sys.modules.get(modulnavn)
        if modul is None:
            spec = importlib.util.spec_from_file_location(modulnavn, sti)
            modul = importlib.util.module_from_spec(spec)
            sys.modules[modulnavn] = modul
            spec.loader.exec_module(modul)
        return modul
    except Exception:
        return None


def _hentet_datasaet(navn, filnavn):
    """Sti til et datasæt der hentes fra en release, hvis pluginnet gør det."""
    modul = grunddata_modul()
    if modul is None:
        return None
    try:
        return modul.sti(navn, filnavn)
    except Exception:
        return None


def tilpasninger_standard():
    sti = _standard_sti(DHMLINJE_REL)
    if sti:
        return sti
    return _hentet_datasaet('DHMLinje', 'DHMLinje.shp')


def taerskel_ha():
    """Tærsklen for hvornår et indløb udpeges selvstændigt, fra interfacet.

    Begge trin læser den samme værdi. Gør de ikke det, skriver de hver sin
    konfiguration, og så genbruges konditioneringen ikke.
    """
    from qgis.core import QgsSettings
    try:
        return float(QgsSettings().value(
            'vaadomraade_modeller/vandloeb_taerskel_ha', STD_TAERSKEL_HA))
    except (TypeError, ValueError):
        return STD_TAERSKEL_HA


def indlaes_oplande():
    """Indlæser beregningskoden fra DENNE plugin-kopis Scripts/oplande.py.

    Et almindeligt `import oplande` ville tage den kopi der tilfældigvis blev
    importeret først i QGIS-sessionen — og med flere kopier af pluginnet
    installeret er det ikke nødvendigvis den der hører til denne kode. Det er
    præcis den fælde 'utils' falder i (se ovenfor), og at regne på en anden
    beregningskerne end man tror, er værre end en fejl der siger noget.
    """
    import importlib.util

    sti = os.path.join(SCRIPTS, 'oplande.py')
    # Fingeraftrykket i navnet: efter en plugin-opdatering laeser
    # Processing scripterne paa ny, men et modul der ligger i
    # sys.modules bliver hentet fra cachen — og saa koerer ny kode mod
    # gammel kerne. Med stoerrelse og tidsstempel i navnet bliver en
    # aendret fil et andet modul og indlaeses forfra.
    try:
        _st = os.stat(sti)
        _mrk = f'_{_st.st_size}_{int(_st.st_mtime)}'
    except OSError:
        _mrk = ''
    navn = '_oplande_' + os.path.basename(PLUGIN_ROOT).lower() + _mrk
    if sys.modules.get(navn) is not None:
        return sys.modules[navn]
    if not os.path.isfile(sti):
        raise ImportError(f'beregningskoden mangler: {sti}')
    spec = importlib.util.spec_from_file_location(navn, sti)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[navn] = modul
    spec.loader.exec_module(modul)

    # Kompatibilitet med QGIS før 3.38: beregningskoden bygger felter med
    # QgsField(navn, QMetaType.Type.X), og den konstruktør findes først fra 3.38.
    # Her byttes den ENE funktion ud med en der også kan QVariant. Resten af
    # oplande.py røres ikke — den skal blive ved med at være en ren kopi af
    # kildeprojektets fil, så en rettelse dér kan kopieres direkte ind.
    try:
        modul._felt('kompatibilitetstjek', 'tekst')
    except Exception:
        modul._felt = lambda navn, slags: felt(navn, slags)

    return modul


def output_mappe():
    """Outputfiler_<omraade> for det aktive projektområde. Fejler højt hvis den mangler."""
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    from utils import find_output_filer
    mappe = find_output_filer()
    if not mappe:
        raise QgsProcessingException(
            'Outputfiler_<omraade>-mappen blev ikke fundet. Kør "Navngiv projekt" '
            'og "Tilføj dit projektområde (.shp)" i interfacet først.')
    return Path(mappe)


def arbejdsstier(ud_mappe: Path, opret=True):
    """Mapper og filer de to trin deler. Samme stier fra begge, ellers intet genbrug."""
    arbejde = ud_mappe / ARBEJDSMAPPE
    stier = {
        'arbejdsmappe': arbejde,
        'derived': arbejde / 'mellemresultater',
        'log': arbejde / 'log',
        'konf_fil': arbejde / 'parametre_koersel.yml',
        'gpkg': ud_mappe / LEVERANCE,
        'dem_raa': ud_mappe / DEM_RAA,
        'hoejdemodel': ud_mappe / DEM_KONDITIONERET,
        'channel_networks': ud_mappe / CHANNEL_NETWORKS,
    }
    if opret:
        for n in ('arbejdsmappe', 'derived', 'log'):
            stier[n].mkdir(parents=True, exist_ok=True)
    return stier


def find_dem(ud_mappe: Path, feedback=None):
    """Højdemodellen i Outputfiler, i prioriteret rækkefølge. (sti, er_raa)."""
    for navn in DEM_KANDIDATER:
        sti = ud_mappe / navn
        if sti.is_file():
            return sti, navn == DEM_RAA
    raise QgsProcessingException(
        f'Højdemodellen blev ikke fundet i {ud_mappe}. Kør "Beregn strømningsveje" '
        'først — den henter terrænmodellen. Forventet en af: '
        + ', '.join(DEM_KANDIDATER))


def dem_som_fil(dem_lag, feedback):
    """Filstien bag et rasterlag. Modellen læser rasteret direkte med GDAL."""
    sti = Path(dem_lag.source().split('|', 1)[0])
    if not sti.is_file():
        raise QgsProcessingException(
            f'Højdemodellen er ikke en fil på disken ({dem_lag.providerType()}). '
            'Analysen læser rasteret direkte med GDAL — gem den som GeoTIFF først.')
    return sti


def epsg_af_dem(dem_lag, feedback):
    """(epsg, crs) for højdemodellen. Fejler på grader, gætter kun hvis EPSG mangler."""
    crs = dem_lag.crs()
    # Hele metoden regner i meter: celleareal, brændingsdybde, snapafstand. I grader
    # ville tallene være meningsløse uden at noget fejlede.
    if crs.isValid() and crs.isGeographic():
        raise QgsProcessingException(
            f'Højdemodellen er i {crs.authid()}, et geografisk koordinatsystem med '
            'grader som enhed. Analysen regner i meter — reprojicér til EPSG:25832 først.')
    authid = crs.authid() if crs.isValid() else ''
    if authid.startswith('EPSG:'):
        epsg = int(authid.split(':')[1])
    else:
        # SAGA-formatet .sdat taber ofte EPSG-koden. Pluginnets DHM er altid i
        # 25832, så det er en sikker antagelse — men den skal siges højt.
        epsg = STANDARD_EPSG
        feedback.pushWarning(
            f'Højdemodellen oplyser ingen EPSG-kode ({authid or "ukendt CRS"}) — '
            f'antager EPSG:{STANDARD_EPSG}, som resten af pluginnet regner i.')
    return epsg, QgsCoordinateReferenceSystem(f'EPSG:{epsg}')


def materialiser(alg, parameters, navn, context, feedback, maal_crs,
                 arbejdsmappe: Path, standard=None):
    """Gør et vektorinput klar som fil i maal_crs. (sti, lagnavn) eller None.

    Oplandsmodellen reprojicerer IKKE selv, og blandede referencesystemer giver
    forskydninger på metersniveau — rigeligt til at et indløbspunkt havner ved
    siden af strømningsvejen.

    Ligger laget allerede som en fil i det rigtige koordinatsystem uden filter,
    bruges filen som den er. Det er ikke en mikrooptimering: DHMLinje er
    landsdækkende med 438.694 linjer, og en kopi ved hver kørsel ville koste mere
    tid end hele oplandsberegningen. Modellen læser selv kun det den skal bruge,
    med et geografisk filter.
    """
    import processing

    lag = alg.parameterAsVectorLayer(parameters, navn, context)
    if lag is None and standard and os.path.isfile(standard):
        lag = QgsVectorLayer(standard, navn.lower(), 'ogr')
        feedback.pushInfo(f'  {navn}: bruger pluginnets {standard}')
    if lag is None or not lag.isValid() or lag.featureCount() == 0:
        return None

    if (lag.dataProvider() is not None and lag.dataProvider().name() == 'ogr'
            and not lag.subsetString() and lag.crs() == maal_crs):
        raa = lag.source()
        sti = raa.split('|', 1)[0]
        lagnavn = next((d.split('=', 1)[1] for d in raa.split('|')[1:]
                        if d.startswith('layername=')), None)
        # layerid= kan ikke oversættes til et lagnavn modellen kan slå op
        if os.path.isfile(sti) and 'layerid=' not in raa:
            feedback.pushInfo(f'  {navn}: bruger {sti} direkte')
            return Path(sti), lagnavn

    ud = arbejdsmappe / f'input_{navn.lower()}.gpkg'
    kilde_fil = lag.source().split('|', 1)[0]
    # Skriv kun igen hvis kilden er nyere. Ellers ville filen få nyt tidsstempel ved
    # hver kørsel, og så bliver INTET mellemresultat nogensinde genbrugt: hver kørsel
    # ville starte forfra med resampling og konditionering.
    if (ud.is_file() and os.path.isfile(kilde_fil)
            and ud.stat().st_mtime >= os.path.getmtime(kilde_fil)):
        feedback.pushInfo(f'  {navn}: genbruger {ud.name}')
        return ud, None

    if lag.crs() != maal_crs:
        feedback.pushInfo(f'  {navn}: reprojicerer fra {lag.crs().authid()} '
                          f'til {maal_crs.authid()}')
    else:
        feedback.pushInfo(f'  {navn}: skriver {lag.featureCount():,} objekter til fil')
    if ud.exists():
        ud.unlink()
    processing.run('native:reprojectlayer',
                   {'INPUT': lag, 'TARGET_CRS': maal_crs, 'OUTPUT': str(ud)},
                   context=context, feedback=feedback, is_child_algorithm=True)
    if not ud.exists():
        raise QgsProcessingException(
            f'Inputlaget "{navn}" kunne ikke skrives til {ud}. Er mappen skrivbar?')
    return ud, None


def projektomraade_navn():
    from qgis.core import QgsSettings
    return QgsSettings().value(
        'vaadomraade_modeller/projektomraade_navn', '') or 'projektomraade'


def byg_konfiguration(*, epsg, dem_sti, projekt, vandloeb, tilpasninger, stier,
                      oploesning=STD_OPLOESNING,
                      stroem_taerskel_ha=STD_STROEM_TAERSKEL,
                      snap_celler=STD_SNAP_CELLER,
                      burn=STD_BURN, burn_dybde=STD_BURN_DYBDE,
                      breach_dist=STD_BREACH_DIST,
                      fill_metode=STD_FILL_METODE,
                      indloeb_rapport_ha=None,
                      min_polygon_m2=STD_MIN_POLYGON,
                      stop_ved_kant=STD_STOP_VED_KANT):
    """Konfigurationen som oplande.py forventer. ÉN kilde for begge trin.

    Værdierne uden en dialogparameter er de målte og afprøvede fra kildeprojektets
    config/parametre.yml — begrundelserne står dér, slå dem op før du ændrer noget.
    """
    if indloeb_rapport_ha is None:
        indloeb_rapport_ha = taerskel_ha()

    return {
        'projekt': {'navn': str(projektomraade_navn()), 'koersel_id': None},
        # afvis_afvigende=false: vi har selv sikret CRS ovenfor, og en højdemodel
        # uden EPSG-kode må ikke stoppe kørslen.
        'crs': {'epsg': int(epsg), 'afvis_afvigende': False},
        'input': {
            'projektomraade': str(projekt[0]),
            'projektomraade_lag': projekt[1],
            'dem': str(dem_sti),
            'vandloeb': str(vandloeb[0]) if vandloeb else None,
            'vandloeb_lag': vandloeb[1] if vandloeb else None,
            'hydro_tilpasninger': str(tilpasninger[0]) if tilpasninger else None,
            'hydro_tilpasninger_lag': tilpasninger[1] if tilpasninger else None,
            # Tilpasningskoterne ERSTATTER terrænet (de trækkes ikke fra), så selv på
            # et allerede tilpasset DEM er en gentagelse harmløs.
            'dem_er_hydrologisk_tilpasset': False,
        },
        'analyse': {
            'oplaesning_m': float(oploesning),
            'aggregering': 'mean',
            # null = brug hele højdemodellen. DHM-downloaden er allerede klippet til
            # de oplande projektområdet rører (+500 m), så det ER analyseudstrækningen.
            # En symmetrisk buffer ovenpå ville smide dækning væk i netop de retninger
            # hvor oplandet strækker sig.
            'buffer_m': None,
            # Vandflader vippes umærkeligt mod deres udløb i stedet for at maskeres:
            # maskering fjerner landskabets udløb, og helt flade flader kvæler
            # breaching. min_areal er sat urealistisk højt, så intet maskeres.
            'maskeer_vandflader': True,
            'vandflade_maks_kote_m': 1.0,
            'vandflade_min_areal_ha': 1000000.0,
            'vandflade_relief_m': 0.001,
            'vandflade_vip_m': 0.01,
        },
        'konditionering': {
            'tilpasning_felt_start': 'START_Z',
            'tilpasning_felt_slut': 'END_Z',
            # Linjer markeret "uncertain" af dataleverandøren tages med: udeladelse
            # efterlader et falsk vandskel hvor der faktisk er en underføring.
            'tilpasning_medtag_usikre': True,
            'stream_burn': bool(burn),
            'stream_burn_dybde_m': float(burn_dybde),
            'stream_burn_fald_pr_m': 0.0001,
            # Tragt og kapring er afprøvet og forkastet i kildeprojektet — begge
            # skabte konkurrerende strømningsveje. Lad dem stå på 0.
            'stream_burn_korridor_m': 0.0,
            'stream_burn_korridor_dybde_m': 2.0,
            'stream_burn_kapre_m': 0.0,
            'breach_dist_celler': int(breach_dist),
            'breach_max_cost': None,
            # SKAL være false: fill_deps=true i dette Whitebox-build ødelægger
            # højdemodellen. De lavninger breaching ikke kan bryde, fyldes af
            # fill_depressions bagefter.
            'breach_fill_rest': False,
            'fill_metode': str(fill_metode),
            'flat_increment': 0.0001,
            # 1 = natural. Værktøjets default (0 = garbrecht_martz) efterlader
            # lavninger, og hver lavning er afbrudt afvanding.
            'flat_resolution': 1,
            'fill_til_sidst': True,
        },
        'stroemning': {
            'vandloeb_taerskel_ha': float(stroem_taerskel_ha),
            'snap_radius_celler': int(snap_celler),
        },
        'oplande': {
            'min_vandloebsopland_ha': 1.0,
            'indloeb_rapport_ha': float(indloeb_rapport_ha),
            'indloeb_min_afstand_m': 50.0,
            'min_polygon_areal_m2': float(min_polygon_m2),
            'arealbalance_tolerance_pct': 0.1,
        },
        'output': {
            'gpkg': str(stier['gpkg']),
            'derived_dir': str(stier['derived']),
            'log_dir': str(stier['log']),
            'hillshade': False,
            'hillshade_fil': str(stier['derived'] / 'hillshade.tif'),
            'vandveje_lag': True,
            'overskriv': False,
        },
        'qa': {
            'stop_ved_fejlet_accepttest': True,
            'kraev_nul_kantkontaminering': bool(stop_ved_kant),
        },
    }


def gem_konfiguration(konf: dict, sti: Path):
    """Skriver konfigurationen — men KUN hvis den er en anden end sidst.

    oplande.py afgør om et mellemresultat kan genbruges ved at sammenligne dets
    alder med denne fils. Skrives den ved hver kørsel, er den altid nyere end alt
    andet, og så bliver intet nogensinde genbrugt: hvert trin ville starte forfra
    med resampling og konditionering.
    """
    import yaml

    sti.parent.mkdir(parents=True, exist_ok=True)
    ny = yaml.safe_dump(konf, allow_unicode=True, sort_keys=False)
    if sti.exists() and sti.read_text(encoding='utf-8') == ny:
        return False
    sti.write_text(ny, encoding='utf-8')
    return True


def aabn_dem(alg, parameters, navn, context, feedback, ud_mappe: Path, advar=True):
    """Højdemodellen fra dialogen, ellers slået op i Outputfiler. (sti, epsg, crs)."""
    dem_lag = alg.parameterAsRasterLayer(parameters, navn, context)
    if dem_lag is None:
        sti, er_raa = find_dem(ud_mappe, feedback)
        dem_lag = QgsRasterLayer(str(sti), 'DHM')
        feedback.pushInfo(f'Højdemodel slået op automatisk: {sti}')
        if not er_raa and advar:
            feedback.pushWarning(
                f'Bruger {sti.name}, som allerede er konditioneret (vandløb brændt '
                'ned og lavninger fyldt). Fyldte lavninger kan ikke gendannes, så '
                'oplandsgrænserne bliver ringere end de behøver. Kør "Beregn '
                f'strømningsveje" igen — den gemmer {DEM_RAA}, det rå terræn, som '
                'beregningen selv konditionerer.')
    if not dem_lag.isValid():
        raise QgsProcessingException('Højdemodellen kunne ikke indlæses.')
    epsg, crs = epsg_af_dem(dem_lag, feedback)
    return dem_som_fil(dem_lag, feedback), epsg, crs


# ── robusthed på tværs af QGIS-versioner ─────────────────────────────────────

def felt(navn: str, slags: str):
    """QgsField der virker på tværs af QGIS-versioner.

    QVariant-konstruktøren er deprecated fra QGIS 3.38 og forsvinder i QGIS 4;
    QMetaType-varianten findes til gengæld først fra 3.38. Prøv den nye først og
    fald tilbage — ellers dør en hel kørsel på et feltnavn.
    """
    from qgis.core import QgsField

    try:
        from qgis.PyQt.QtCore import QMetaType
        typer = {"tekst": QMetaType.Type.QString,
                 "tal": QMetaType.Type.Double,
                 "heltal": QMetaType.Type.Int}
        return QgsField(navn, typer[slags])
    except (ImportError, AttributeError, TypeError):
        from qgis.PyQt.QtCore import QVariant
        typer = {"tekst": QVariant.String,
                 "tal": QVariant.Double,
                 "heltal": QVariant.Int}
        return QgsField(navn, typer[slags])


def tjek_forudsaetninger(feedback=None):
    """Kontrollerer at alt beregningen skal bruge, er der — FØR den tunge del.

    Uden tjekket opdages en manglende pakke eller en manglende udvidelse først
    efter minutters resampling og konditionering, og fejlen kommer som en
    traceback midt i kæden. Her fejler den med det samme, og med noget man kan
    handle på.
    """
    from qgis.core import QgsApplication, QgsProcessingException

    manglende = []
    for modul, forklaring in (
        ("numpy", "følger med QGIS"),
        ("scipy", "følger med QGIS — geninstallér QGIS hvis den mangler"),
        ("yaml", "PyYAML, følger med QGIS"),
        ("osgeo.gdal", "GDAL, følger med QGIS"),
    ):
        try:
            __import__(modul)
        except ImportError:
            manglende.append(f"{modul} ({forklaring})")
    if manglende:
        raise QgsProcessingException(
            "Beregningen kan ikke køre — disse Python-pakker mangler i QGIS:\n  "
            + "\n  ".join(manglende))

    # Whitebox leverer selve hydrologien. Er provideren registreret, er alt godt —
    # det er den i QGIS når udvidelsen er aktiveret. Ellers ledes der på disken, og
    # først hvis den ikke findes NOGEN steder, er det en fejl. Selve registreringen
    # overlades til oplande.py.
    if QgsApplication.processingRegistry().providerById("whitebox_workflows") is None:
        fundet = find_whitebox()
        if fundet is None:
            nl = chr(10)
            raise QgsProcessingException(
                'Udvidelsen "Whitebox Workflows for QGIS" blev ikke fundet. '
                'Den leverer den hydrologiske beregning (breaching, fill, D8, '
                'oplandssporing), så uden den kan trinnet ikke køre.'
                + nl + nl +
                'Installér den under Udvidelser > Administrer og installér '
                'udvidelser > Alle, og sæt flueben ved den under Installeret.')
        if feedback is not None:
            feedback.pushInfo('Whitebox fundet i ' + str(fundet) + ' — registreres.')


def find_whitebox():
    """Finder Whitebox-udvidelsen på disken og lægger dens mappe på sys.path.

    Der ledes flere steder med vilje. QgsApplication.qgisSettingsDirPath() peger det
    rigtige sted inde i QGIS, men giver en meningsløs sti når QGIS kører uden
    brugerflade — og profilen behøver ikke hedde 'default'. Derfor gennemsøges alle
    profiler under den mappe styresystemet bruger, plus QGIS' egen plugin-mappe.

    Returnerer stien til udvidelsen, eller None hvis den ikke findes.
    """
    import glob
    from qgis.core import QgsApplication

    kandidater = []
    try:
        kandidater.append(os.path.join(
            QgsApplication.qgisSettingsDirPath(), 'python', 'plugins'))
    except Exception:
        pass

    if sys.platform.startswith('win'):
        rod = os.path.join(os.path.expandvars('%APPDATA%'), 'QGIS', 'QGIS3', 'profiles')
    elif sys.platform == 'darwin':
        rod = os.path.expanduser('~/Library/Application Support/QGIS/QGIS3/profiles')
    else:
        rod = os.path.expanduser('~/.local/share/QGIS/QGIS3/profiles')
    kandidater.extend(glob.glob(os.path.join(rod, '*', 'python', 'plugins')))

    try:
        kandidater.append(os.path.join(QgsApplication.pkgDataPath(), 'python', 'plugins'))
    except Exception:
        pass

    for mappe in kandidater:
        udvidelse = os.path.join(mappe, 'whitebox_workflows_for_qgis')
        if os.path.isfile(os.path.join(udvidelse, 'provider.py')):
            if mappe not in sys.path:
                sys.path.insert(0, mappe)
            return udvidelse
    return None


def frigiv_filer(stier, feedback=None):
    """Fjerner lag i QGIS-projektet der peger på filer vi er ved at skrive.

    På Windows holder et indlæst lag filen åben, og så fejler skrivningen — eller
    værre: GeoPackagen bliver skrevet halvt. Trinnene lægger selv deres output i
    lagpanelet, så ved anden kørsel er filerne næsten altid åbne.
    """
    from qgis.core import QgsProject

    projekt = QgsProject.instance()
    if projekt is None:
        return
    maal = {os.path.normpath(str(s)).lower() for s in stier}
    fjernet = []
    for lag in list(projekt.mapLayers().values()):
        try:
            kilde = lag.source().split("|", 1)[0]
        except RuntimeError:
            continue
        if os.path.normpath(kilde).lower() in maal:
            fjernet.append(lag.name())
            projekt.removeMapLayer(lag.id())
    if fjernet and feedback is not None:
        feedback.pushInfo(
            "Fjernede lag fra forrige kørsel, så filerne kan skrives: "
            + ", ".join(fjernet))


# ── accepttest: rammer modellen de kortlagte vandløb? ────────────────────────

def traefprocent(akkumulering_sti, vandloeb_sti, vandloeb_lag=None,
                 soeg_m=50.0, andel=0.5, min_ha=5.0):
    """Falder modellens vandveje sammen med det kortlagte netværk?

    Kriteriet må ikke være cirkulært: brænder man en linje ned i terrænet og måler
    bagefter at der løber vand i renden, måler man sin egen brænding. Der spørges i
    stedet, for hvert knudepunkt på den kortlagte linje: bærer linjen mindst
    `andel` af den STØRSTE akkumulering inden for `soeg_m` — eller løber
    hovedstrømmen ved siden af?

    Returnerer None hvis der ikke er noget at måle på (intet netværk i udstrækningen),
    ellers den samlede træfprocent, antal knudepunkter, fordelingen pr. vandløb og
    hvor langt hovedstrømmen typisk ligger væk for dem der ikke ramte.
    """
    import numpy as np
    from osgeo import gdal, ogr

    ds = gdal.Open(str(akkumulering_sti))
    if ds is None:
        return None
    gt = ds.GetGeoTransform()
    bredde, hoejde = ds.RasterXSize, ds.RasterYSize
    akk = ds.GetRasterBand(1).ReadAsArray().astype("float64")
    ds = None

    opl = gt[1]
    ha_pr_celle = opl * opl / 1e4
    soeg = max(1, int(round(soeg_m / opl)))

    kilde = ogr.Open(str(vandloeb_sti))
    if kilde is None:
        return None
    lag = kilde.GetLayerByName(vandloeb_lag) if vandloeb_lag else kilde.GetLayer(0)
    if lag is None:
        return None
    lag.SetSpatialFilterRect(gt[0], gt[3] - hoejde * opl,
                             gt[0] + bredde * opl, gt[3])

    navnefelt = None
    definition = lag.GetLayerDefn()
    for kandidat in ("VLNAVN", "navn", "NAVN", "vandloebsnavn"):
        if definition.GetFieldIndex(kandidat) >= 0:
            navnefelt = kandidat
            break

    pr_vandloeb = {}
    ramte_i_alt = knuder_i_alt = 0
    afstande = []

    for f in lag:
        navn = (f.GetField(navnefelt) if navnefelt else None) or "unavngivet"
        geom = f.GetGeometryRef()
        if geom is None:
            continue
        ramte = knuder = 0
        for punkter in linjedele(geom):
            for px, py in punkter:
                kol = int((px - gt[0]) / opl)
                raekke = int((gt[3] - py) / opl)
                if not (soeg <= raekke < hoejde - soeg and soeg <= kol < bredde - soeg):
                    continue
                vindue = akk[raekke - soeg:raekke + soeg + 1,
                             kol - soeg:kol + soeg + 1] * ha_pr_celle
                bedst = float(vindue.max())
                if bedst < min_ha:
                    continue            # ingen vandvej i nærheden — ikke en forbier
                knuder += 1
                paa_linjen = float(akk[raekke, kol] * ha_pr_celle)
                if paa_linjen >= andel * bedst:
                    ramte += 1
                else:
                    rr, cc = np.where(vindue >= andel * bedst)
                    afstande.append(float(np.hypot(rr - soeg, cc - soeg).min() * opl))
        if knuder:
            pr_vandloeb[navn] = (ramte, knuder, geom.Length() / 1000.0)
            ramte_i_alt += ramte
            knuder_i_alt += knuder
    kilde = None

    if not knuder_i_alt:
        return None
    return {
        "pct": 100.0 * ramte_i_alt / knuder_i_alt,
        "ramte": ramte_i_alt,
        "knuder": knuder_i_alt,
        "pr_vandloeb": pr_vandloeb,
        "median_afvigelse_m": (float(sorted(afstande)[len(afstande) // 2])
                               if afstande else 0.0),
        "soeg_m": soeg_m,
        "andel": andel,
    }


def linjedele(geometri):
    """Punktlisterne for hver linjedel i en OGR-geometri.

    Uundværlig, fordi GetPointCount() returnerer 0 på en MultiLineString. Samme lag
    læst fra en shapefil og fra en GeoPackage kommer ud som henholdsvis LineString
    og MultiLineString, og en naiv gennemløbning taber så hele laget uden at fejle.
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


# ── præberegnet grundlag ─────────────────────────────────────────────────────
#
# Trin 0-2 (konditionering, D8, akkumulering) afhænger IKKE af projektområdet —
# kun af terrænet, referencedataene og parametrene. Derfor kan de beregnes én gang
# pr. opland og genbruges af alle projekter deri. Trin 3-6 er projektspecifikke,
# men de er også de hurtige.
#
# Et grundlag er en mappe pr. flise med de fem rastere trin 3 skal bruge, plus et
# manifest der siger hvad de er beregnet på. Passer manifestet ikke til de
# aktuelle parametre, bruges fliserne ikke — så regner pluginnet selv i stedet for
# stille at levere noget der er lavet på et andet grundlag.

# 2: stroemningsvejene udvides til at daekke de kortlagte vandloeb og klippes
#    til oplandet.
# 3: udvidelsen sporer fra ét punkt pr. hul i flere runder, saa nettet daekker
#    de kortlagte forloeb uden at flette sig til parallelle traade.
# Fliser fra en aeldre version genbruges ikke — indholdet er et andet.
GRUNDLAG_VERSION = 5
GRUNDLAG_RASTERE = ('00_dem_analyse.tif', '01_dem_hydro.tif', '02_d8_pointer.tif',
                    '02_d8_akkumulering.tif', '02_streams.tif')
MANIFEST = 'manifest.json'
DAEKNING = 'daekning.gpkg'


# Indholdshash pr. proces. En 100 MB-fil tager under et sekund at laese, og
# noeglen regnes faa gange pr. koersel — men ikke én gang for meget.
_HASH_HUSKET = {}


def _filhash(sti, st=None):
    """sha1 af filens INDHOLD — ikke dens tidsstempel.

    Tidsstemplet skiftede med maskinen: en kopieret eller hentet fil fik et nyt,
    noeglen blev en anden, og de praeberegnede fliser passede saa kun paa den
    maskine de var bygget paa. Alle andre hentede terraenet forfra hver gang uden
    at noget sagde fra.
    """
    import hashlib

    sti = str(sti)
    if st is None:
        try:
            st = os.stat(sti)
        except OSError:
            return None
    noegle = (sti, st.st_size, int(st.st_mtime))
    husket = _HASH_HUSKET.get(noegle)
    if husket is not None:
        return husket
    h = hashlib.sha1()
    try:
        with open(sti, 'rb') as f:
            for blok in iter(lambda: f.read(1 << 20), b''):
                h.update(blok)
    except OSError:
        return None
    kort = h.hexdigest()[:16]
    _HASH_HUSKET[noegle] = kort
    return kort


def konditioneringsnoegle(konf: dict) -> str:
    """Fingeraftryk af præcis det der bestemmer trin 0-2's resultat.

    Projektområde, outputstier og projektnavn er udeladt: de ændrer sig fra projekt
    til projekt uden at ændre terrænet. Snap-radius er også udeladt — den bruges
    først i trin 4. Vandløbs- og tilpasningslagene indgår med filstørrelse og
    tidsstempel, så et opdateret referencelag ugyldiggør grundlaget.
    """
    import hashlib
    import json

    def filsignatur(sti):
        if not sti:
            return None
        try:
            st = os.stat(str(sti))
        except OSError:
            return [str(sti), None, None]
        return [os.path.basename(str(sti)), st.st_size, _filhash(sti, st)]

    relevant = {
        'version': GRUNDLAG_VERSION,
        'epsg': konf['crs']['epsg'],
        'analyse': {n: konf['analyse'][n] for n in sorted(konf['analyse'])
                    if n != 'buffer_m'},
        'konditionering': {n: konf['konditionering'][n]
                           for n in sorted(konf['konditionering'])},
        'vandloeb_taerskel_ha': konf['stroemning']['vandloeb_taerskel_ha'],
        'vandloeb': filsignatur(konf['input'].get('vandloeb')),
        'tilpasninger': filsignatur(konf['input'].get('hydro_tilpasninger')),
        'dem_er_tilpasset': konf['input'].get('dem_er_hydrologisk_tilpasset'),
    }
    tekst = json.dumps(relevant, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(tekst.encode('utf-8')).hexdigest()[:16]




def undgaa_tomme_input(konf, dem_analyse, log=None):
    """Slår brænding og tilpasningslinjer fra når der ikke er nogen i udsnittet.

    Motoren stopper med en fejl hvis vandløbsbrændingen ikke rammer en eneste
    celle, eller hvis der ikke er én brugbar tilpasningslinje i udstrækningen. For
    et lille område ved kysten er begge dele en rigtig beskrivelse af stedet, ikke
    en fejl — og at stoppe dér efterlader brugeren uden resultat.

    Resultatet er nøjagtig det samme som at brænde nul linjer ind eller påtrykke
    nul koter, så konditioneringsnøglen ændrer sig ikke ved det. Kald derfor
    først efter at nøglen er beregnet, og efter trin 0: udstrækningen aflæses på
    analyserasteret, så vinduet er præcis motorens eget.

    Returnerer listen af de fravalg der blev truffet.
    """
    from osgeo import gdal, ogr

    r = gdal.Open(str(dem_analyse))
    if r is None:
        return []
    gt = r.GetGeoTransform()
    x0, y1 = gt[0], gt[3]
    x1 = gt[0] + gt[1] * r.RasterXSize
    y0 = gt[3] + gt[5] * r.RasterYSize
    r = None

    def antal_i_vindue(sti, lagnavn, kotefelter=None):
        ds = ogr.Open(str(sti))
        if ds is None:
            return 0
        lag = ds.GetLayerByName(lagnavn) if lagnavn else ds.GetLayer(0)
        if lag is None:
            return 0
        lag.SetSpatialFilterRect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if not kotefelter:
            return lag.GetFeatureCount()
        # Motoren tæller kun linjer den kan bruge. En linje uden koter i
        # START_Z/END_Z springes over, og findes der kun dem, stopper den alligevel.
        d = lag.GetLayerDefn()
        if any(d.GetFieldIndex(n) < 0 for n in kotefelter):
            return 0
        n = 0
        for f in lag:
            if all(f.GetField(k) is not None for k in kotefelter):
                n += 1
        return n

    fravalg = []
    vandloeb = konf['input'].get('vandloeb')
    if konf['konditionering'].get('stream_burn') and vandloeb:
        if antal_i_vindue(vandloeb, konf['input'].get('vandloeb_lag')) == 0:
            konf['konditionering']['stream_burn'] = False
            fravalg.append('ingen kortlagte vandløb i udstrækningen — '
                           'brændingen slået fra')

    tilpas = konf['input'].get('hydro_tilpasninger')
    if tilpas:
        felter = (konf['konditionering'].get('tilpasning_felt_start', 'START_Z'),
                  konf['konditionering'].get('tilpasning_felt_slut', 'END_Z'))
        if antal_i_vindue(tilpas, konf['input'].get('hydro_tilpasninger_lag'),
                          felter) == 0:
            konf['input']['hydro_tilpasninger'] = None
            fravalg.append('ingen brugbare tilpasningslinjer i udstrækningen — '
                           'de springes over')

    if log is not None:
        for f in fravalg:
            log.skriv(f'  {f}')
    return fravalg


def haardfoer_whitebox(oplande, log=None):
    """Genrejser Whitebox' provider når den holder op med at kunne oprette algoritmer.

    Efter en raekke kald i den samme proces begynder provideren at svare "Error
    creating algorithm from createInstance()" paa alt. Det er ikke rasterets
    stoerrelse — det samme vaerktoej koerer paa et sekund paa den samme flise i en
    frisk proces. Maalt: hver flise over ca. 54 km2 ramte det, altid paa det foerste
    kald efter stroemningsvejene er faerdige.

    Her registreres provideren forfra og kaldet proeves én gang til. Hjaelper det
    ikke, faar fejlen lov at gaa videre — saa proever batchen flisen igen i en
    helt frisk proces.
    """
    if getattr(oplande, '_whitebox_haardfoer', False):
        return
    oprindelig = oplande.koer

    def koer(alg_id, parametre, log_, beskrivelse=''):
        try:
            return oprindelig(alg_id, parametre, log_, beskrivelse)
        except Exception as e:
            if 'createInstance' not in str(e):
                raise
            log_.advar('Whitebox-provideren kunne ikke oprette algoritmen — '
                       'registrerer den forfra og proever igen')
            oplande.registrer_whitebox(log_)
            return oprindelig(alg_id, parametre, log_, beskrivelse)

    oplande._whitebox_haardfoer = True
    oplande.koer = koer
    if log is not None:
        log.skriv('  Whitebox-kald genrejses ved createInstance-fejl')

def vaelg_fill(oplande, konf, log=None):
    """Sætter konditionering.fill_metode igennem i trin 1.

    oplande.py kalder fill_depressions direkte, og filen skal blive ved med at
    være en tro kopi af kildeprojektet — derfor byttes algoritmen her i stedet
    for dér. Kaldes én gang før trin 1, efter registrer_whitebox.

    fill_depressions forsøger at allokere 128 GiB på rasterer over ca. 30 mio.
    celler og river processen med sig, se STD_FILL_METODE.
    """
    metode = str(konf.get('konditionering', {}).get('fill_metode') or 'fill')
    if metode == 'fill':
        return metode
    nyt = 'fill_depressions_' + metode
    if getattr(oplande, '_fill_byttet', None) == nyt:
        return metode
    oprindelig = getattr(oplande, '_fill_oprindelig', None) or oplande.koer

    def koer(alg_id, parametre, log_, beskrivelse=''):
        if str(alg_id).endswith(':fill_depressions'):
            alg_id = oplande.kraev_algoritme(nyt)
            # Hverken Wang & Liu eller Planchon & Darboux kender flat_resolution
            # og max_depth.
            parametre = {n: v for n, v in parametre.items()
                         if n not in ('flat_resolution', 'max_depth')}
            beskrivelse = f"{beskrivelse or 'fill'} [{metode}]"
        return oprindelig(alg_id, parametre, log_, beskrivelse)

    oplande._fill_oprindelig = oprindelig
    oplande._fill_byttet = nyt
    oplande.koer = koer
    if log is not None:
        log.skriv(f'  fill: {nyt} i stedet for fill_depressions')
    return metode

def grundlag_mappe(opret=False):
    """Biblioteket med præberegnede fliser.

    Er der ikke sat en mappe, bruges én under QGIS-profilen. Den ligger med vilje
    ét sted pr. maskine og ikke under projektets outputmappe: fliserne er
    uafhængige af projektet, og med én mappe pr. projektområde blev de samme
    30-100 MB hentet forfra for hvert nyt område.
    """
    from qgis.core import QgsApplication, QgsSettings

    sti = QgsSettings().value('vaadomraade_modeller/grundlag_mappe', '')
    if sti and os.path.isdir(sti):
        return Path(sti)
    standard = Path(QgsApplication.qgisSettingsDirPath()) / 'oplandsgrundlag'
    if standard.is_dir():
        return standard
    if opret:
        standard.mkdir(parents=True, exist_ok=True)
        return standard
    return None


def laes_manifest(flise: Path):
    import json

    fil = Path(flise) / MANIFEST
    if not fil.is_file():
        return None
    try:
        return json.loads(fil.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def find_flise(bibliotek: Path, omraade_geom, noegle: str, feedback=None):
    """Den flise der dækker projektområdet og er beregnet med de samme parametre.

    Dækning kræves helt: rager projektområdet uden for flisen, mangler der terræn
    netop dér hvor oplandet skal spores, og så er et lokalt beregnet grundlag det
    rigtige. Der vælges den mindste dækkende flise — den er hurtigst at kopiere.
    """
    from qgis.core import QgsGeometry, QgsRectangle

    if bibliotek is None or not Path(bibliotek).is_dir():
        return None
    kandidater = []
    for mappe in sorted(Path(bibliotek).iterdir()):
        if not mappe.is_dir():
            continue
        m = laes_manifest(mappe)
        if not m:
            continue
        grunde = []
        if m.get('grundlag_version') != GRUNDLAG_VERSION:
            grunde.append('anden udgave af grundlaget')
        if m.get('konditioneringsnoegle') != noegle:
            grunde.append('andre parametre eller opdaterede referencedata')
        manglende = [r for r in GRUNDLAG_RASTERE if not (mappe / r).is_file()]
        if manglende:
            grunde.append('ufuldstændig (' + ', '.join(manglende) + ')')
        u = m.get('udstraekning')
        daekning = mappe / DAEKNING
        if daekning.is_file():
            # Det maskerede omraade, ikke rektanglet. Et projektomraade i hjoernet af
            # rektanglet ville ellers faa sit opland afkortet ved NoData — og
            # kantkontaminerings-tjekket fanger det ikke, for det maaler kun celler
            # paa selve rasterkanten.
            lag = QgsVectorLayer(f'{daekning}|layername=daekning', 'daekning', 'ogr')
            samlet = None
            if lag.isValid():
                geoms = [f.geometry() for f in lag.getFeatures()
                         if f.geometry() and not f.geometry().isEmpty()]
                samlet = QgsGeometry.unaryUnion(geoms) if geoms else None
            if samlet is None:
                grunde.append('dækningen kunne ikke læses')
            elif not samlet.contains(omraade_geom):
                grunde.append('projektområdet ligger ikke helt inden for det '
                              'præberegnede opland')
        elif not u or len(u) != 4:
            grunde.append('manifest uden udstrækning')
        elif not QgsGeometry.fromRect(
                QgsRectangle(u[0], u[1], u[2], u[3])).contains(omraade_geom):
            grunde.append('dækker ikke projektområdet')
        if grunde:
            if feedback is not None:
                feedback.pushInfo(f'  {mappe.name}: bruges ikke — ' + '; '.join(grunde))
            continue
        kandidater.append(((u[2] - u[0]) * (u[3] - u[1]), mappe, m))
    if not kandidater:
        return None
    _, mappe, m = min(kandidater, key=lambda t: t[0])
    return mappe, m


def hent_flise(flise: Path, derived: Path, feedback=None, log=None):
    """Kopierer flisens fem rastere til projektets mellemresultat-mappe.

    De kopieres frem for at blive brugt på stedet: trin 3-6 skriver sine egne
    rastere i samme mappe, og biblioteket skal blive ved med kun at indeholde det
    der er præberegnet. Kopien springes over hvis filen allerede ligger der med
    samme størrelse og tidsstempel.
    """
    import shutil

    derived.mkdir(parents=True, exist_ok=True)
    kopieret = sprunget = 0
    for navn in GRUNDLAG_RASTERE:
        kilde = Path(flise) / navn
        maal = derived / navn
        if (maal.is_file() and maal.stat().st_size == kilde.stat().st_size
                and int(maal.stat().st_mtime) >= int(kilde.stat().st_mtime)):
            sprunget += 1
            continue
        shutil.copy2(kilde, maal)
        kopieret += 1
    besked = (f'Præberegnet grundlag hentet fra {Path(flise).name}: '
              f'{kopieret} raster(e) kopieret, {sprunget} var der i forvejen.')
    # Loggen sender selv videre til feedback, saa beskeden maa kun gives ét sted.
    if log is not None:
        log.skriv('  ' + besked)
    elif feedback is not None:
        feedback.pushInfo(besked)
    return kopieret


MAERKE = 'grundlag.json'


def skriv_maerke(arbejdsmappe: Path, noegle: str, kilde: str, ekstra=None):
    """Noterer hvad mellemresultaterne i arbejdsmappen er lavet med.

    Uden mærket måtte oplandstrinnet gætte ud fra filernes tidsstempler om trin 0-2
    stadig passer til parametrene. Det holder ikke når rasterne er kopieret fra et
    præberegnet grundlag: de er ældre end konfigurationsfilen, selv om de er
    fuldstændig gyldige. Mærket siger det direkte i stedet.
    """
    import json

    data = {'konditioneringsnoegle': noegle, 'kilde': kilde,
            'grundlag_version': GRUNDLAG_VERSION,
            'skrevet': __import__('datetime').datetime.now().isoformat(timespec='seconds')}
    if ekstra:
        data.update(ekstra)
    (Path(arbejdsmappe) / MAERKE).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def laes_maerke(arbejdsmappe: Path):
    import json

    fil = Path(arbejdsmappe) / MAERKE
    if not fil.is_file():
        return None
    try:
        return json.loads(fil.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def kan_springe_konditionering_over(stier, noegle: str):
    """Er trin 0-2 allerede lavet med præcis de her parametre?

    Kræver både mærket og at alle fem rastere ligger der. Er der tvivl, regnes der
    forfra: en time ekstra er billigere end et opland regnet på et andet grundlag
    end det man tror.
    """
    maerke = laes_maerke(stier['arbejdsmappe'])
    if not maerke or maerke.get('konditioneringsnoegle') != noegle:
        return None
    if maerke.get('grundlag_version') != GRUNDLAG_VERSION:
        return None
    if not all((stier['derived'] / r).is_file() for r in GRUNDLAG_RASTERE):
        return None
    return maerke


def arbejds_crs(ud_mappe: Path, feedback=None):
    """(epsg, crs) at regne i, uden at hente terræn først.

    Ligger der allerede en højdemodel, følger vi den. Ellers EPSG:25832, som er det
    hele pluginnet regner i — og det gør at biblioteket kan slås op FØR terrænet
    hentes. Findes der en dækkende flise, skal der nemlig ikke hentes noget.
    """
    from qgis.core import QgsRasterLayer

    for navn in DEM_KANDIDATER:
        sti = Path(ud_mappe) / navn
        if sti.is_file():
            lag = QgsRasterLayer(str(sti), 'dhm')
            if lag.isValid():
                return epsg_af_dem(lag, feedback)
    return STANDARD_EPSG, QgsCoordinateReferenceSystem(f'EPSG:{STANDARD_EPSG}')


def omraade_geometri(projekt):
    """Projektområdets samlede geometri, læst fra den materialiserede fil."""
    from qgis.core import QgsGeometry

    sti, lagnavn = projekt
    kilde = f'{sti}|layername={lagnavn}' if lagnavn else str(sti)
    lag = QgsVectorLayer(kilde, 'projektomraade', 'ogr')
    if not lag.isValid():
        return None
    geoms = [f.geometry() for f in lag.getFeatures()
             if f.geometry() and not f.geometry().isEmpty()]
    return QgsGeometry.unaryUnion(geoms) if geoms else None


# ── strømningsvejene som linjer ───────────────────────────────────────────────


def stroemnet_graf(oplande, stroem, dem_hydro, log):
    """Vandløbscellerne som en graf: nedstrøms nabo, antal tilløb, Strahler-orden.

    Både ordenen og vektoriseringen af strømningsvejene bygger på det samme, og
    begge dele lå før i Whitebox. Provideren begynder efter en række kald i samme
    proces at svare "Error creating algorithm from createInstance()" på alt — målt
    ramte det hver eneste flise over ca. 54 km2, og at registrere den forfra
    hjælper ikke. Rasteret er stort, men vandløbscellerne er få (25.000 i et
    opland på 66 km2), så det er billigt at gøre her.

    Strahler-reglen: et forløb uden tilløb har orden 1; løber to eller flere
    forløb af samme højeste orden sammen, stiger den; ellers arves den højeste.
    Rækkefølgen kommer af at tælle tilløb pr. celle og arbejde nedstrøms fra dem
    uden nogen, så hver celle røres én gang og der er ingen rekursion.
    """
    import numpy as np
    from osgeo import gdal

    ds = gdal.Open(str(stroem['streams']))
    streams = ds.GetRasterBand(1).ReadAsArray()
    gt, proj = ds.GetGeoTransform(), ds.GetProjection()
    ds = None
    hoejde, bredde = streams.shape

    rp = gdal.Open(str(stroem['pointer']))
    pointer = rp.GetRasterBand(1).ReadAsArray()
    rp = None

    # Afkodningen udledes empirisk mod terrænet — den dokumenterede
    # WhiteboxTools-konvention passer ikke på dette build.
    kode = oplande.d8_afkodning(Path(stroem['pointer']), Path(dem_hydro), log)

    raekker, kolonner = np.nonzero(streams > 0)
    antal = len(raekker)
    graf = {'raekker': raekker, 'kolonner': kolonner, 'gt': gt, 'proj': proj,
            'form': streams.shape, 'antal': antal}
    if not antal:
        graf.update(ned=np.empty(0, dtype=np.int64), tilloeb=np.empty(0, dtype=np.int32),
                    orden=np.empty(0, dtype=np.int32))
        return graf

    indeks = -np.ones(streams.shape, dtype=np.int64)
    indeks[raekker, kolonner] = np.arange(antal)
    ned = -np.ones(antal, dtype=np.int64)
    for vaerdi, (dr, dc) in kode.items():
        traef = pointer[raekker, kolonner] == vaerdi
        if not traef.any():
            continue
        nr = raekker[traef] + dr
        nc = kolonner[traef] + dc
        inde = (nr >= 0) & (nr < hoejde) & (nc >= 0) & (nc < bredde)
        maal = -np.ones(int(traef.sum()), dtype=np.int64)
        maal[inde] = indeks[nr[inde], nc[inde]]
        ned[traef] = maal

    tilloeb = np.zeros(antal, dtype=np.int32)
    har_ned = ned >= 0
    np.add.at(tilloeb, ned[har_ned], 1)

    orden = np.zeros(antal, dtype=np.int32)
    hoejeste = np.zeros(antal, dtype=np.int32)
    antal_hoejeste = np.zeros(antal, dtype=np.int32)
    tilbage = tilloeb.copy()
    koe = list(np.flatnonzero(tilbage == 0))
    behandlet = 0
    while koe:
        i = koe.pop()
        behandlet += 1
        orden[i] = 1 if tilloeb[i] == 0 else (
            hoejeste[i] + 1 if antal_hoejeste[i] >= 2 else hoejeste[i])
        j = ned[i]
        if j < 0:
            continue
        if orden[i] > hoejeste[j]:
            hoejeste[j] = orden[i]
            antal_hoejeste[j] = 1
        elif orden[i] == hoejeste[j]:
            antal_hoejeste[j] += 1
        tilbage[j] -= 1
        if tilbage[j] == 0:
            koe.append(j)

    if behandlet < antal:
        # En kreds i pointeren ville efterlade celler uden orden. Det sker ikke på
        # et konditioneret terræn, men står det stille her, er noget galt.
        log.advar(f'Strahler: {antal - behandlet:,} netcelle(r) indgår i en kreds '
                  'og fik ingen orden')
        orden[orden == 0] = 1

    graf.update(ned=ned, tilloeb=tilloeb, orden=orden)
    log.skriv(f'  strømningsnet: {antal:,} celler, højeste Strahler-orden '
              f'{int(orden.max())}')
    return graf


def strahler_orden(oplande, stroem, dem_hydro, ud_raster, log, graf=None):
    """Skriver Strahler-ordenen som raster. Se stroemnet_graf for reglen."""
    import numpy as np
    from osgeo import gdal

    if graf is None:
        graf = stroemnet_graf(oplande, stroem, dem_hydro, log)
    ud = np.zeros(graf['form'], dtype=np.int32)
    if graf['antal']:
        ud[graf['raekker'], graf['kolonner']] = graf['orden']
    _gem_raster(ud_raster, ud, graf['gt'], graf['proj'], gdal.GDT_Int32)
    return ud


def vektoriser_stroemme(graf):
    """Strømningsvejene som strækninger — én linje pr. forløb mellem to knuder.

    En strækning begynder ved et udspring (ingen tilløb) eller ved et sammenløb,
    og følger vandet nedstrøms indtil næste sammenløb eller udløbet. Sammenløbs-
    cellen kommer med som sidste punkt, så linjerne hænger sammen.

    Ordenen tages fra strækningens første celle: den er den samme hele vejen ned
    til den knude der afslutter strækningen.
    """
    if not graf['antal']:
        return []
    ned, tilloeb, orden = graf['ned'], graf['tilloeb'], graf['orden']
    raekker, kolonner, gt = graf['raekker'], graf['kolonner'], graf['gt']

    def punkt(i):
        return (gt[0] + (kolonner[i] + 0.5) * gt[1],
                gt[3] + (raekker[i] + 0.5) * gt[5])

    straekninger = []
    for start in range(graf['antal']):
        if tilloeb[start] == 1:
            continue                      # midt i et forløb, ikke en begyndelse
        punkter = [punkt(start)]
        i = start
        while True:
            j = ned[i]
            if j < 0:
                break
            punkter.append(punkt(j))
            if tilloeb[j] >= 2:
                break                     # sammenløb: her slutter strækningen
            i = j
        if len(punkter) > 1:
            straekninger.append((punkter, int(orden[start])))
    return straekninger


def _gem_raster(sti, arr, gt, proj, dtype):
    """Skriver et enkeltbaandsraster med samme georeference som kilden."""
    from osgeo import gdal

    drv = gdal.GetDriverByName('GTiff')
    ds = drv.Create(str(sti), arr.shape[1], arr.shape[0], 1, dtype,
                    options=['COMPRESS=DEFLATE', 'TILED=YES'])
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    baand = ds.GetRasterBand(1)
    baand.WriteArray(arr)
    baand.SetNoDataValue(0)
    ds.FlushCache()
    ds = None
    return sti

def skriv_channel_networks(oplande, konf, derived, ud_gpkg, stroem,
                       koersel_id, crs, log, feedback=None, tillad_tomt=False):
    """Vektoriserer det beregnede vandløbsnet og giver hver strækning Strahler-orden.

    Ordenen regnes på samme D8-raster som strømningsvejene, så den hører til de
    veje der faktisk leveres. Feltnavnet ORDER er det samme som SAGA's channel
    network brugte, så 'Udtraek max Strahler' og de øvrige værktøjer der læser
    feltet, virker uændret.

    Både ordenen og selve vektoriseringen lå før i Whitebox. Provideren
    begynder efter en række kald i samme proces at svare "Error creating
    algorithm from createInstance()" på alt — det ramte hver eneste flise over
    ca. 54 km2, og at registrere den forfra hjælper ikke. Begge dele regnes nu
    på strømningsnettets egen graf, se stroemnet_graf. Ordenen er afprøvet mod
    Whitebox' egen på et ukroppet raster: enige celle for celle.
    """
    from qgis.core import QgsGeometry, QgsPointXY

    derived = Path(derived)
    orden_raster = derived / '02_strahler.tif'

    # Terraenet ligger i mellemresultaterne — D8-afkodningen udledes mod det.
    graf = stroemnet_graf(oplande, stroem, derived / '01_dem_hydro.tif', log)
    strahler_orden(oplande, stroem, None, orden_raster, log, graf=graf)
    straekninger = vektoriser_stroemme(graf)

    taerskel = float(konf['stroemning']['vandloeb_taerskel_ha'])
    metode = (f'D8 på {konf["analyse"]["oplaesning_m"]:g} m, tilpasningskoter, '
              'monoton vandløbsbrænding, breaching+fill; '
              f'vandløb fra {taerskel:g} ha akkumulering')

    vl = QgsVectorLayer(f'MultiLineString?crs={crs.authid()}',
                        'Channel_Networks', 'memory')
    vl.dataProvider().addAttributes([
        felt('ORDER', 'heltal'),
        felt('taerskel_ha', 'tal'),
        felt('metode', 'tekst'),
        felt('koersel_id', 'tekst'),
        felt('dato', 'tekst'),
    ])
    vl.updateFields()

    dato = dt.date.today().isoformat()
    ordener = {}
    objekter = []
    for punkter, orden in straekninger:
        geom = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in punkter])
        if geom is None or geom.isEmpty():
            continue
        geom.convertToMultiType()
        ordener[orden] = ordener.get(orden, 0) + 1
        ny = QgsFeature(vl.fields())
        ny.setGeometry(geom)
        ny.setAttributes([orden, taerskel, metode, koersel_id, dato])
        objekter.append(ny)
    kilde = None

    if not objekter and not tillad_tomt:
        raise QgsProcessingException(
            'Der blev ikke udledt én eneste strømningsvej. Enten er '
            f'akkumuleringstærsklen ({taerskel:g} ha) for høj til området, eller '
            'konditioneringen gik galt — se loggen i ' + str(Path(konf['output']['log_dir'])))
    if not objekter:
        # Et lille opland uden et eneste kortlagt vandløb har ingen strømningsveje
        # over tærsklen, og der er intet net at forlænge op langs. Det er en rigtig
        # beskrivelse af stedet, ikke en fejl: strømningsretning og -akkumulering er
        # beregnet, og oplandssporingen bygger på dem. Alt vand bliver direkte opland.
        log.skriv('  Channel_Networks: ingen strømningsveje over tærsklen, og '
                  'ingen kortlagte vandløb at følge — laget skrives tomt')

    vl.dataProvider().addFeatures(objekter)
    vl.updateExtents()
    gem_gpkg(vl, ud_gpkg, 'Channel_Networks')
    log.skriv(f'  Channel_Networks: {len(objekter)} straekninger skrevet til '
              f'{ud_gpkg}')
    return len(objekter), ordener


def gem_gpkg(lag, sti, lagnavn: str):
    """Skriver et hukommelseslag til GeoPackage, robust over for åbne lag i QGIS."""
    projekt = QgsProject.instance()
    sti_norm = os.path.normpath(str(sti)).lower()
    for l in list(projekt.mapLayers().values()):
        if os.path.normpath(l.source().split('|', 1)[0]).lower() == sti_norm:
            projekt.removeMapLayer(l.id())
    sti = Path(sti)
    if sti.exists():
        try:
            sti.unlink()
        except OSError as e:
            raise QgsProcessingException(
                f'{sti} kunne ikke overskrives: {e}. Ligger laget åbent i QGIS? '
                'Fjern det og kør igen.')

    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = 'GPKG'
    opts.fileEncoding = 'UTF-8'
    opts.layerName = lagnavn
    fejl, besked, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
        lag, str(sti), QgsCoordinateTransformContext(), opts)
    if fejl != QgsVectorFileWriter.NoError:
        raise QgsProcessingException(
            f'Kunne ikke gemme "{lagnavn}" til {sti}: {besked or fejl}')


# ── det beregnede net skal dække det kortlagte ───────────────────────────────

def udvid_stroemme_til_kortlagte(oplande, stroem, dem_hydro, vandloeb_sti,
                                 vandloeb_lag, log, punktafstand_m=5.0):
    """Sikrer at det beregnede vandløbsnet er mindst så langt som det kortlagte.

    Akkumuleringstærsklen bestemmer hvornår en celle regnes som vandløb, og i den
    øvre ende af hvert forløb er der under tærsklen opstrøms. Derfor stopper det
    beregnede net før det kortlagte, og der mangler stumper i toppen af hvert
    tilløb.

    Her spores der nedstrøms fra punkter langs hver kortlagt linje, indtil
    sporingen rammer det net der allerede er. Sporingen følger D8, så de tilføjede
    celler ligger på faktiske strømningsveje — en ren rasterisering af linjen ville
    kunne lægge sig en celle ved siden af den vej vandet tager, og så ville nettet
    hverken hænge sammen eller kunne få en Strahler-orden.

    Tærsklen røres ikke: den bestemmer stadig hvad der regnes som vandløb dér hvor
    der ikke er kortlagt noget.
    """
    import numpy as np
    from osgeo import gdal, ogr

    if not vandloeb_sti:
        return 0

    ds = gdal.Open(str(stroem['streams']), gdal.GA_Update)
    if ds is None:
        return 0
    baand = ds.GetRasterBand(1)
    streams = baand.ReadAsArray()
    gt = ds.GetGeoTransform()
    hoejde, bredde = streams.shape
    opl = gt[1]

    rp = gdal.Open(str(stroem['pointer']))
    pointer = rp.GetRasterBand(1).ReadAsArray()
    rp = None

    kode = oplande.d8_afkodning(Path(stroem['pointer']), Path(dem_hydro), log)

    kilde = ogr.Open(str(vandloeb_sti))
    if kilde is None:
        ds = None
        return 0
    lag = kilde.GetLayerByName(vandloeb_lag) if vandloeb_lag else kilde.GetLayer(0)
    lag.SetSpatialFilterRect(gt[0], gt[3] + gt[5] * hoejde,
                             gt[0] + gt[1] * bredde, gt[3])

    # Kun dér hvor nettet FAKTISK mangler, og ÉT startpunkt pr. hul.
    #
    # Sporer man fra hvert punkt paa linjen, snapper nabopunkter til hver sin
    # celle, og sporingerne fletter sig til et net af parallelle traade langs
    # samme vandloeb — hydrologisk ligegyldigt, men det ser ud som om der er ti
    # vandloeb hvor der er ét. Startpunktet er den oeverste celle i hullet, maalt
    # paa akkumuleringen: derfra daekker sporingen nedstroems resten af hullet.
    #
    # Der koeres flere runder. En sporing foelger terraenet, ikke linjen, saa den
    # kan forlade det kortlagte forloeb og efterlade et nyt, mindre hul. Runderne
    # lukker dem, og de stopper naar der ikke er mere at hente.
    from scipy import ndimage

    ra = gdal.Open(str(stroem['akkumulering']))
    akk = ra.GetRasterBand(1).ReadAsArray()
    ra = None

    linjepunkter = []
    skridt = max(punktafstand_m, opl)
    for f in lag:
        geom = f.GetGeometryRef()
        if geom is None:
            continue
        for punkter in linjedele(geom):
            raekkefoelge = []
            for i in range(len(punkter) - 1):
                (x0, y0), (x1, y1) = punkter[i], punkter[i + 1]
                laengde = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                antal = max(1, int(laengde / skridt))
                for k in range(antal):
                    t = k / antal
                    x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                    c = int((x - gt[0]) / gt[1])
                    r = int((y - gt[3]) / gt[5])
                    if 0 <= r < hoejde and 0 <= c < bredde:
                        raekkefoelge.append((r, c))
            if raekkefoelge:
                linjepunkter.append(raekkefoelge)
    kilde = None

    hul_graense = max(2.0 * opl, 10.0)

    tilfoejet = huller_i_alt = 0
    # Hvilke celler udvidelsen lagde til. De ligger pr. definition paa en sporing
    # fra et kortlagt vandloeb, og klipningen bagefter skal kunne frede dem.
    tilfoejede = np.zeros(streams.shape, dtype=bool)
    # Tre runder. Én runde lukker kun det foerste stykke af hvert hul, fordi
    # sporingen foelger terraenet og tit forlader det kortlagte forloeb efter faa
    # celler; runderne tager resten.
    #
    # Flere runder er afproevet og forkastet. Paa fliser hvor terraenet foerer
    # vandet et andet sted hen end AIS tegner linjen, koeber de kun daekning ved
    # enten at lade nettet vokse langt ud over virkeligheden (Glombak: 90 %
    # daekning, men 126 km net mod 54,8 km kortlagt) eller — med sporingerne holdt
    # i en korridor om linjerne — ved at splitte nettet i stumper (478
    # straekninger mod 137). Uenigheden mellem terraen og kort kan udvidelsen ikke
    # lukke; den staar i manifestets daekning_af_kortlagte_pct i stedet.
    MAKS_RUNDER = 3
    runde = 0
    while runde < MAKS_RUNDER:
        runde += 1
        if (streams > 0).any():
            afstand_til_net = ndimage.distance_transform_edt(streams == 0) * opl
        else:
            afstand_til_net = np.full(streams.shape, np.inf)

        startceller = set()
        for raekkefoelge in linjepunkter:
            hul = []
            for rc in list(raekkefoelge) + [None]:
                if rc is not None and afstand_til_net[rc[0], rc[1]] > hul_graense:
                    hul.append(rc)
                    continue
                if hul:
                    startceller.add(min(hul, key=lambda p: akk[p[0], p[1]]))
                    hul = []
        if not startceller:
            if runde == 1:
                log.skriv('  nettet daekker allerede de kortlagte vandloeb — '
                          'intet at udvide')
            break
        huller_i_alt += len(startceller)
        foer_runden = tilfoejet

        for start in startceller:
            r, c = start
            skridt_i_alt = 0
            while True:
                if streams[r, c] > 0:
                    break                      # naaet det eksisterende net
                streams[r, c] = 1
                tilfoejede[r, c] = True
                tilfoejet += 1
                skridt_i_alt += 1
                if skridt_i_alt > 4 * (hoejde + bredde):
                    log.advar('  sporing nedstroems blev usandsynlig lang — afbrudt')
                    break
                retning = kode.get(int(pointer[r, c]))
                if retning is None:
                    break                      # sluk eller uden for nettet
                r, c = r + retning[0], c + retning[1]
                if not (0 <= r < hoejde and 0 <= c < bredde):
                    break

        if tilfoejet == foer_runden:
            break                              # ingen fremgang, hullerne er blinde

    if tilfoejet:
        baand.WriteArray(streams)
        ds.FlushCache()
    ds = None
    log.skriv(f'  nettet udvidet til at daekke de kortlagte vandloeb: '
              f'{tilfoejet:,} celler tilfoejet ({tilfoejet * opl / 1000:,.1f} km) '
              f'i {huller_i_alt:,} hul(ler) paa de kortlagte linjer, {runde} runde(r)')
    return tilfoejet, tilfoejede


def klip_stroemme_til(stroem, maske, log, hvad='oplandet', fred=None):
    """Fjerner strømningsveje uden for masken.

    Flisen dækker oplandet plus en margin, og i margenen ligger stumper af
    nabooplandenes vandløb. De hører ikke til her og skal ikke tegnes med.

    `fred` er celler der bliver stående uanset masken. Sporingen fra et kortlagt
    vandløb følger vandet nedstrøms og forlader derfor oplandet dér hvor vandet
    gør — og en ren klipning ville tage netop de stumper der blev lagt til for at
    nå de kortlagte vandløb. Målt på små, flade kystoplande fjernede den 2.276 af
    2.429 celler igen.
    """
    from osgeo import gdal

    ds = gdal.Open(str(stroem['streams']), gdal.GA_Update)
    if ds is None:
        return 0
    baand = ds.GetRasterBand(1)
    streams = baand.ReadAsArray()
    foer = int((streams > 0).sum())
    behold = maske if fred is None else (maske | fred)
    streams[~behold] = 0
    efter = int((streams > 0).sum())
    baand.WriteArray(streams)
    ds.FlushCache()
    ds = None
    log.skriv(f'  stroemningsveje klippet til {hvad}: {foer - efter:,} celler '
              f'fjernet, {efter:,} tilbage')
    return efter


def netdaekning(stroem, vandloeb_sti, vandloeb_lag=None, maske=None,
                afstand_m=10.0, skridt_m=5.0):
    """Dækker det beregnede net de kortlagte vandløb?

    Tre tal, alle inden for masken (typisk oplandet), for ellers sammenlignes
    modellens net i oplandet med kortlagte vandløb i nabooplandene:

      beregnet_km   længden af det beregnede net
      kortlagt_km   længden af de kortlagte linjer
      daekning_pct  hvor stor en andel af de kortlagte linjer der har en beregnet
                    strømningsvej inden for `afstand_m`

    Det er dækningen der er kravet: nettet skal følge de kortlagte vandløb hele
    vejen. To lige lange net kan sagtens ligge hvert sit sted.
    """
    import numpy as np
    from osgeo import gdal, ogr
    from scipy import ndimage

    ds = gdal.Open(str(stroem['streams']))
    gt = ds.GetGeoTransform()
    streams = ds.GetRasterBand(1).ReadAsArray()
    hoejde, bredde = streams.shape
    ds = None
    opl = gt[1]
    if maske is not None:
        streams = np.where(maske, streams, 0)
    beregnet_km = float((streams > 0).sum()) * opl / 1000.0

    # Afstand fra hver celle til naermeste beregnede stroemningsvej — saa
    # daekningen kan slaas op direkte for hvert punkt paa linjerne.
    if (streams > 0).any():
        afstandsraster = ndimage.distance_transform_edt(streams == 0) * opl
    else:
        afstandsraster = np.full(streams.shape, np.inf)

    kortlagt_km = 0.0
    ramt = i_alt = 0
    if vandloeb_sti:
        kilde = ogr.Open(str(vandloeb_sti))
        if kilde is not None:
            lag = (kilde.GetLayerByName(vandloeb_lag) if vandloeb_lag
                   else kilde.GetLayer(0))
            lag.SetSpatialFilterRect(gt[0], gt[3] + gt[5] * hoejde,
                                     gt[0] + gt[1] * bredde, gt[3])
            for f in lag:
                geom = f.GetGeometryRef()
                if geom is None:
                    continue
                for punkter in linjedele(geom):
                    for i in range(len(punkter) - 1):
                        (x0, y0), (x1, y1) = punkter[i], punkter[i + 1]
                        laengde = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                        antal = max(1, int(laengde / skridt_m))
                        for k in range(antal):
                            t = (k + 0.5) / antal
                            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                            c = int((x - gt[0]) / gt[1])
                            r = int((y - gt[3]) / gt[5])
                            if not (0 <= r < hoejde and 0 <= c < bredde):
                                continue
                            if maske is not None and not maske[r, c]:
                                continue    # ligger i et andet opland
                            kortlagt_km += (laengde / antal) / 1000.0
                            i_alt += 1
                            if afstandsraster[r, c] <= afstand_m:
                                ramt += 1
            kilde = None
    return {
        'beregnet_km': beregnet_km,
        'kortlagt_km': kortlagt_km,
        'daekning_pct': (100.0 * ramt / i_alt) if i_alt else None,
        'punkter': i_alt,
        'afstand_m': afstand_m,
    }


# ── grundlag hentet fra nettet ───────────────────────────────────────────────
#
# Fliserne ligger som én zip hver på et GitHub-release, med en fortegnelse
# (index.json) ved siden af. Pluginnet henter fortegnelsen, finder den flise der
# dækker projektområdet OG er beregnet med brugerens egne parametre, og henter
# kun den. Den pakkes ud i det lokale bibliotek og bliver dér — næste projekt i
# samme opland henter ingenting.

INDEKS = 'index.json'
INDEKS_LOKALT = 'index_online.json'
INDEKS_TIMER = 24.0     # hvor længe en hentet fortegnelse bruges uden at spørge igen


# Hvor det udgivne grundlag ligger. 'latest' peger altid paa den nyeste udgivelse,
# saa en ny udgivelse tages i brug uden at nogen skal aendre en indstilling.
# Kan overskrives i QGIS med vaadomraade_modeller/grundlag_url.
STD_GRUNDLAG_URL = ('https://github.com/Limfjordssekretariatet/oplandsgrundlag'
                    '/releases/latest/download/')


def grundlag_url():
    """Base-URL til det udgivne grundlag.

    Brugerens egen indstilling vinder; ellers den udgivne adresse. Saettes
    indstillingen til 'ingen', slaas online-opslaget fra.
    """
    from qgis.core import QgsSettings

    url = (QgsSettings().value('vaadomraade_modeller/grundlag_url', '') or '').strip()
    if url.lower() in ('ingen', 'nej', 'off'):
        return None
    if not url:
        url = STD_GRUNDLAG_URL
    return url if url.endswith('/') else url + '/'


def _aabner():
    """urllib-opener der følger QGIS' egne proxyindstillinger.

    På et kommunalt net går trafikken gennem en proxy, og en forbindelse der ikke
    kender den, giver bare timeout. QGIS' indstillinger er dér hvor brugeren
    allerede har konfigureret det én gang.
    """
    import urllib.request
    from qgis.core import QgsSettings

    s = QgsSettings()
    handlers = []
    if str(s.value('proxy/proxyEnabled', 'false')).lower() == 'true':
        vaert = s.value('proxy/proxyHost', '')
        port = s.value('proxy/proxyPort', '')
        bruger = s.value('proxy/proxyUser', '')
        kode = s.value('proxy/proxyPassword', '')
        if vaert and port:
            legitimation = f'{bruger}:{kode}@' if bruger else ''
            adresse = f'http://{legitimation}{vaert}:{port}'
            handlers.append(urllib.request.ProxyHandler(
                {'http': adresse, 'https': adresse}))
    aabner = urllib.request.build_opener(*handlers)
    aabner.addheaders = [('User-Agent', 'Vaadomraade-Modeller/QGIS')]
    return aabner


def hent_indeks(url, bibliotek, feedback=None, tving=False):
    """Fortegnelsen over udgivne fliser. Genbruges et døgn ad gangen.

    Returnerer None hvis der ikke er hul igennem. En manglende netforbindelse må
    ikke stoppe en beregning der udmærket kan laves lokalt.
    """
    import json
    import time

    lokal = Path(bibliotek) / INDEKS_LOKALT
    if not tving and lokal.is_file():
        alder = (time.time() - lokal.stat().st_mtime) / 3600.0
        if alder < INDEKS_TIMER:
            try:
                return json.loads(lokal.read_text(encoding='utf-8'))
            except ValueError:
                pass

    if not url:
        return None
    try:
        with _aabner().open(url + INDEKS, timeout=30) as svar:
            raa = svar.read().decode('utf-8')
        indeks = json.loads(raa)
    except Exception as e:
        if feedback is not None:
            feedback.pushInfo(f'  fortegnelsen kunne ikke hentes fra {url}: {e}')
        if lokal.is_file():           # en gammel fortegnelse er bedre end ingen
            try:
                return json.loads(lokal.read_text(encoding='utf-8'))
            except ValueError:
                return None
        return None

    lokal.parent.mkdir(parents=True, exist_ok=True)
    lokal.write_text(raa, encoding='utf-8')
    if feedback is not None:
        feedback.pushInfo(
            f'  fortegnelse hentet: {indeks.get("antal_fliser")} fliser, udgivet '
            f'{indeks.get("udgivet")}')
    return indeks


def find_flise_online(indeks, omraade_geom, noegle, feedback=None):
    """Den udgivne flise der dækker projektområdet og passer til parametrene.

    Dækningen i fortegnelsen er forenklet, så den er et FILTER, ikke et bevis.
    Den nøjagtige kontrol sker på den hentede flises egen daekning.gpkg — samme
    kontrol som for en lokal flise.
    """
    from qgis.core import QgsGeometry, QgsRectangle

    if not indeks:
        return None
    if indeks.get('grundlag_version') != GRUNDLAG_VERSION:
        if feedback is not None:
            feedback.pushInfo(
                f'  det udgivne grundlag er version {indeks.get("grundlag_version")}, '
                f'pluginnet forventer {GRUNDLAG_VERSION} — bruges ikke')
        return None

    kandidater = []
    for post in indeks.get('fliser', []):
        if post.get('konditioneringsnoegle') != noegle:
            continue
        wkt = post.get('daekning_wkt')
        if wkt:
            daekning = QgsGeometry.fromWkt(wkt)
            if daekning.isNull() or not daekning.contains(omraade_geom):
                continue
        else:
            u = post.get('udstraekning')
            if not u or len(u) != 4:
                continue
            if not QgsGeometry.fromRect(
                    QgsRectangle(u[0], u[1], u[2], u[3])).contains(omraade_geom):
                continue
        kandidater.append(post)
    if not kandidater:
        return None
    return min(kandidater, key=lambda p: p.get('bytes', 0))


def hent_flise_online(url, post, bibliotek, feedback=None, afbryd=None):
    """Henter og udpakker én udgiven flise. Returnerer mappen, eller None.

    Der hentes til en midlertidig fil og verificeres mod sha256 inden udpakning:
    en halvt hentet zip må aldrig ende i biblioteket og blive taget for et gyldigt
    grundlag ved næste kørsel.
    """
    import hashlib
    import shutil
    import zipfile

    bibliotek = Path(bibliotek)
    maal = bibliotek / post['flise_id']
    midlertidig = bibliotek / (post['fil'] + '.henter')
    bibliotek.mkdir(parents=True, exist_ok=True)
    bytes_i_alt = int(post.get('bytes') or 0)

    if feedback is not None:
        feedback.pushInfo(
            f'  henter {post["flise_id"]} ({bytes_i_alt/1e6:.0f} MB)')
    h = hashlib.sha256()
    hentet = 0
    try:
        with _aabner().open(url + post['fil'], timeout=60) as svar, \
                open(midlertidig, 'wb') as ud:
            while True:
                if afbryd is not None and afbryd():
                    raise KeyboardInterrupt
                stykke = svar.read(1 << 20)
                if not stykke:
                    break
                ud.write(stykke)
                h.update(stykke)
                hentet += len(stykke)
                if feedback is not None and bytes_i_alt:
                    feedback.setProgress(min(99.0, 100.0 * hentet / bytes_i_alt))
    except KeyboardInterrupt:
        midlertidig.unlink(missing_ok=True)
        return None
    except Exception as e:
        midlertidig.unlink(missing_ok=True)
        if feedback is not None:
            feedback.pushWarning(f'  flisen kunne ikke hentes: {e}')
        return None

    forventet = (post.get('sha256') or '').lower()
    if forventet and h.hexdigest() != forventet:
        midlertidig.unlink(missing_ok=True)
        if feedback is not None:
            feedback.pushWarning(
                '  den hentede flise havde forkert checksum og er kasseret. '
                'Prøv igen, eller lad terrænet konditionere lokalt.')
        return None

    udpakning = bibliotek / (post['flise_id'] + '.udpakker')
    if udpakning.exists():
        shutil.rmtree(udpakning, ignore_errors=True)
    udpakning.mkdir(parents=True)
    try:
        with zipfile.ZipFile(midlertidig) as z:
            z.extractall(udpakning)
    except Exception as e:
        shutil.rmtree(udpakning, ignore_errors=True)
        if feedback is not None:
            feedback.pushWarning(f'  zippen kunne ikke pakkes ud: {e}')
        return None
    finally:
        midlertidig.unlink(missing_ok=True)

    if maal.exists():
        shutil.rmtree(maal, ignore_errors=True)
    udpakning.rename(maal)
    if feedback is not None:
        feedback.pushInfo(f'  flisen er hentet og ligger nu i {maal}')
    return maal
