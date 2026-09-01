"""Udpeg oplande til projektområdet — vandløbsoplande og direkte opland.

Trin 3-6 i oplandsmodellen (Scripts/oplande.py), ovenpå det konditionerede terræn og
det D8-raster som "Beregn strømningsveje" har lavet:

  Trin 3  Totaloplandet spores opstrøms fra SAMTLIGE celler i projektområdet.
  Trin 4  Ét vandløbsopland pr. celle på grænsen hvor strømningsretningen peger ind i
          området og et kortlagt vandløb ligger inden for snap-afstanden. Sporingen
          spærres ved projektområdet, så et vandløb der kryber ind og ud, ikke får
          tildelt areal der reelt er løbet ind længere opstrøms. Nestede oplande
          gøres disjunkte, og hvert opland underopdeles pr. kortlagt vandløb.
  Trin 5  Direkte opland = totalopland − ∪(vandløbsoplande), altså et RESTAREAL.
          Derfor holder arealbalancen altid.
  Trin 6  Leverance og QA: arealbalance, overlap, sammenhæng, og sporingen holdt op
          mod Whitebox' uafhængige akkumulering.

Mellemresultaterne fra "Beregn strømningsveje" genbruges — begge trin bygger
konfigurationen i oplandsmodel.py netop for at det kan lade sig gøre. Er de ikke
lavet endnu (eller er en parameter ændret), laver dette trin dem selv.

Output i Outputfiler_<omraade> — uændrede navne, så de efterfølgende modeller
(Klip grunddata, Vandopland, Direkte opland, N-regneark) kører videre:

    Vandoplandet.gpkg    ∪(vandløbsoplande) − projektområdet
    Direkte_Opland.gpkg  direkte opland − projektområdet
    Klippe_Omraade.gpkg  totalopland ∪ projektområdet

Hele leverancen med alle lag ligger i Outputfiler_<omraade>/Oplande.gpkg:
totalopland, vandløbsoplande (også delt op pr. kortlagt vandløb), direkte opland med
delarealer, indløbspunkter (inkl. de tilløb der IKKE ligger på et kortlagt vandløb og
derfor tælles som direkte opland), og modellens beregnede vandveje. Log, QA-resultat
og de faktisk brugte parametre skrives til Outputfiler_<omraade>/Oplandsmodel/log/,
så en kørsel kan reproduceres.

Kræver plugin'et "Whitebox Workflows" (Udvidelser > Hent flere).

Erstatter den tidligere beregning, der kørte sagang:upslopearea fra ét frø-punkt pr.
indløb og udledte det direkte opland fra udløbets totalopland. Algoritme-id
(udpeg_oplande_n), parameteren TAERSKEL_HA og de tre outputfiler er uændrede, så
interfacet og modellerne omkring trinnet er urørte.

BEMÆRK: rettes oplande.py i kildeprojektet, skal filen kopieres herind igen —
ellers regner pluginnet videre på den gamle kode.
"""

import datetime as dt
import os
import sys
import shutil
from pathlib import Path

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorDestination,
    QgsProcessingParameterVectorLayer,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

# Sørg for at scriptets egen mappe er på sys.path, så modulerne kan importeres
# uanset hvordan QGIS' script-provider loader filen.
import os as _os, sys as _sys
try:
    _d = _os.path.dirname(_os.path.abspath(__file__))
    if _d and _d not in _sys.path:
        _sys.path.insert(0, _d)
except NameError:
    pass
from utils import find_resultater_mappe as find_resultater      # noqa: F401

GRUNDDATA_REGNEARK = "mst_n_beregning_jul2023.xlsx"


def _fælles():
    """Indlæser oplandsmodel.py fra DENNE plugin-kopi.

    Samme begrundelse som i oplandsmodel.indlaes_oplande(): et almindeligt import
    ville kunne ramme en anden kopi af pluginnet, som ligger først i Processings
    SCRIPTS_FOLDERS — og så ville referencedata og parametre komme fra den forkerte.
    """
    import importlib.util

    rod = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    sti = os.path.join(rod, 'Scripts', 'oplandsmodel.py')
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
    navn = '_oplandsmodel_' + os.path.basename(rod).lower() + _mrk
    if _sys.modules.get(navn) is not None:
        return _sys.modules[navn]
    spec = importlib.util.spec_from_file_location(navn, sti)
    modul = importlib.util.module_from_spec(spec)
    _sys.modules[navn] = modul
    spec.loader.exec_module(modul)
    return modul


om = _fælles()


def get_resultat_regneark_navn():
    from qgis.core import QgsSettings
    s = QgsSettings()
    omraade = s.value('vaadomraade_modeller/projektomraade_navn', '')
    if not omraade:
        return "Resultat.xlsx"
    return f"Resultat_{omraade}.xlsx"


class UdpegOplandeN(QgsProcessingAlgorithm):

    DHM             = 'DHM'
    VANDLOEB        = 'VANDLOEB'
    TILPASNINGER    = 'TILPASNINGER'
    PROJEKTOMRAADE  = 'PROJEKTOMRAADE'
    OPLOESNING      = 'OPLOESNING'
    TAERSKEL_HA     = 'TAERSKEL_HA'
    SNAP_CELLER     = 'SNAP_CELLER'
    BURN            = 'BURN'
    BURN_DYBDE      = 'BURN_DYBDE'
    BREACH_DIST     = 'BREACH_DIST'
    MIN_POLYGON     = 'MIN_POLYGON'
    STROEM_TAERSKEL = 'STROEM_TAERSKEL'
    STOP_VED_KANT   = 'STOP_VED_KANT'
    DIREKTE_OPLAND  = 'DIREKTE_OPLAND'
    VANDOPLANDET    = 'VANDOPLANDET'
    KLIPPE_OMRAADE  = 'KLIPPE_OMRAADE'

    def name(self):
        return 'udpeg_oplande_n'

    def displayName(self):
        return 'Udpeg oplande (vandløbsoplande + direkte opland)'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return UdpegOplandeN()

    def shortHelpString(self):
        return (
            '<p>Beregner <b>vandløbsoplande</b> og <b>direkte opland</b> <i>til</i> '
            'projektområdet ud fra projektpolygonen og en terrænmodel.</p>'
            '<ul>'
            '<li><b>Totalopland</b> — alle celler hvis strømningsvej ender inde i '
            'projektområdet. Projektområdet selv indgår.</li>'
            '<li><b>Vandløbsopland</b> — den del der ledes ind gennem et <i>kortlagt</i> '
            'vandløb. Ét opland pr. indløbspunkt, gjort disjunkte hvis de er nestede.</li>'
            '<li><b>Direkte opland</b> — totalopland minus vandløbsoplandene, som '
            'restareal. Derfor holder arealbalancen altid.</li>'
            '</ul>'
            '<p>Kør <b>"Beregn strømningsveje"</b> først — den henter terrænmodellen og '
            'laver konditioneringen, som dette trin genbruger. Højdemodel, '
            'vandløbsnetværk og hydrologiske tilpasninger slås op automatisk.</p>'
            '<p><b>Læs dette før du stoler på resultatet:</b> rører oplandet '
            'rasterkanten, er arealet et MINIMUM — der mangler terræn, og forbeholdet '
            'skrives på attributten <code>forbehold</code>. Tilløb der ikke findes i '
            'det kortlagte vandløbslag, tæller som direkte opland; de leveres som '
            'punkter med <code>type=terraen</code> i Oplande.gpkg. Kloak- og '
            'regnvandsoplande er ikke i scope.</p>'
            '<p>Kræver plugin\'et <b>Whitebox Workflows</b>.</p>'
        )

    # ── parametre ────────────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        def avanceret(p):
            p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
            return p

        # Projektområdet er den eneste påkrævede input. Højdemodellen slås op i
        # Outputfiler_<omraade>, og referencedataene følger med pluginnet — men alle
        # tre kan overskrives, så algoritmen også kan køres manuelt fra
        # Værktøjskassen på egne lag.
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.PROJEKTOMRAADE, 'Projektområde',
            types=[QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DHM, 'Højdemodel (DHM) — valgfri, slås op automatisk',
            optional=True))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.VANDLOEB, 'Kortlagt vandløbsnetværk (linjer)',
            types=[QgsProcessing.TypeVectorLine],
            defaultValue=om.vandloeb_standard(), optional=True))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.TILPASNINGER, 'Hydrologiske tilpasninger, DHM/Hydro (linjer)',
            types=[QgsProcessing.TypeVectorLine],
            defaultValue=om.tilpasninger_standard(), optional=True))

        # Tærsklen er bevidst den samme parameter som før, men den betyder nu
        # "hvor stort skal et tilløb være for at blive udpeget selvstændigt".
        # Tilløb under grænsen indgår i det direkte opland, præcis som før.
        self.addParameter(QgsProcessingParameterNumber(
            self.TAERSKEL_HA,
            'Tærskel: et indløb udpeges som selvstændigt tilløb ved opstrøms areal ≥ (ha)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=om.taerskel_ha(), minValue=0.0))

        # ── avanceret ────────────────────────────────────────────────────────
        # De parametre der også styrer konditioneringen, har PRÆCIS samme
        # standardværdier som "Beregn strømningsveje". Afviger de, konditioneres
        # terrænet forfra her i stedet for at genbruge trinnets arbejde.
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.OPLOESNING, 'Analyseopløsning (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=om.STD_OPLOESNING, minValue=0.1)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.SNAP_CELLER, 'Maks. afstand fra kortlagt vandløb til indløb (celler)',
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=om.STD_SNAP_CELLER, minValue=0)))
        self.addParameter(avanceret(QgsProcessingParameterBoolean(
            self.BURN, 'Brænd vandløbsnetværket ind som strømningsveje',
            defaultValue=om.STD_BURN)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.BURN_DYBDE, 'Brændingsdybde (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=om.STD_BURN_DYBDE, minValue=0.0)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.BREACH_DIST, 'Breaching: søgeafstand i celler',
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=om.STD_BREACH_DIST, minValue=1)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.MIN_POLYGON, 'Fjern øer og huller under (m²)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=om.STD_MIN_POLYGON, minValue=0.0)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.STROEM_TAERSKEL, 'Akkumuleringstærskel for beregnet vandløb (ha)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=om.STD_STROEM_TAERSKEL, minValue=0.01)))
        self.addParameter(avanceret(QgsProcessingParameterBoolean(
            self.STOP_VED_KANT, 'Stop hvis oplandet når rasterkanten',
            defaultValue=om.STD_STOP_VED_KANT)))

        # Output-destinationerne er VALGFRIE: scriptet skriver altid de tre lag til
        # Outputfiler_<omraade> selv. Destinationerne lader dig eventuelt også gemme
        # en kopi et selvvalgt sted.
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.DIREKTE_OPLAND, 'Direkte opland', optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.VANDOPLANDET, 'Vandoplandet', optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.KLIPPE_OMRAADE, 'Klippe område', optional=True, createByDefault=False))

    # ── kørsel ───────────────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context: QgsProcessingContext,
                         feedback: QgsProcessingFeedback):
        om.tjek_forudsaetninger(feedback)
        oplande = om.indlaes_oplande()
        ud_mappe = om.output_mappe()
        stier = om.arbejdsstier(ud_mappe)
        # Trinnet laegger selv sine lag i lagpanelet, saa ved anden koersel holder de
        # filerne aabne. Paa Windows fejler skrivningen saa — og en halvt skrevet
        # GeoPackage er vaerre end ingen.
        om.frigiv_filer([stier['gpkg'],
                         ud_mappe / 'Vandoplandet.gpkg',
                         ud_mappe / 'Direkte_Opland.gpkg',
                         ud_mappe / 'Klippe_Omraade.gpkg'], feedback)

        # Genbruges konditioneringen, laeses hoejdemodellen aldrig — saa skal der
        # heller ikke advares om at den er konditioneret i forvejen.
        har_maerke = om.laes_maerke(stier['arbejdsmappe']) is not None
        dem_sti, epsg, maal_crs = om.aabn_dem(self, parameters, self.DHM, context,
                                              feedback, ud_mappe,
                                              advar=not har_maerke)

        # Alle vektorinput skal ligge som fil i højdemodellens CRS: oplandsmodellen
        # reprojicerer IKKE selv, og blandede referencesystemer giver forskydninger
        # på metersniveau — rigeligt til at et indløbspunkt havner ved siden af
        # strømningsvejen.
        feedback.pushInfo('Klargør inputlag …')
        projekt = om.materialiser(self, parameters, self.PROJEKTOMRAADE, context,
                                  feedback, maal_crs, stier['arbejdsmappe'])
        if projekt is None:
            raise QgsProcessingException('Projektområde-laget er tomt eller kunne '
                                         'ikke indlæses.')
        vandloeb = om.materialiser(self, parameters, self.VANDLOEB, context, feedback,
                                   maal_crs, stier['arbejdsmappe'],
                                   standard=om.vandloeb_standard())
        # Tilpasningslinjerne foelger ikke med pluginnet — de hentes fra en
        # release foerste gang. Uden dem bliver hver vejdaemning et kunstigt
        # vandskel, saa det er vaerd at vente paa.
        try:
            _gd = om.grunddata_modul()
            if _gd is not None:
                _gd.sikr('DHMLinje', feedback=feedback)
        except Exception as e:
            feedback.pushInfo(f'DHMLinje kunne ikke hentes: {e!r}')

        tilpasninger = om.materialiser(self, parameters, self.TILPASNINGER, context,
                                       feedback, maal_crs, stier['arbejdsmappe'],
                                       standard=om.tilpasninger_standard())
        if vandloeb is None:
            raise QgsProcessingException(
                'Det kortlagte vandløbsnetværk blev ikke fundet: '
                f'{os.path.join(om.GRUNDDATA, om.VANDLOEB_REL)}. '
                'Uden det kan vandløbsoplandene ikke '
                'beregnes, og så er det direkte opland heller ikke defineret — det ER '
                'totalopland minus vandløbsoplande. Filen skal ligge i pluginnets '
                'Grunddata-mappe.')
        if tilpasninger is None:
            feedback.pushWarning(
                'Ingen hydrologiske tilpasninger (DHMLinje) fundet: hver vejdæmning '
                'og bane bliver et ubrudt vandskel, fordi laserscanningen ser dæmningen '
                'men ikke røret under den. Oplandene bliver systematisk for små.')

        # Samme konfiguration som "Beregn strømningsveje" byggede — derfor kan trin
        # 0-2 genbruges. Konfigurationsfilen skrives kun hvis indholdet er ændret:
        # oplande.py sammenligner mellemresultaternes alder mod den.
        konf = om.byg_konfiguration(
            epsg=epsg, dem_sti=dem_sti, projekt=projekt, vandloeb=vandloeb,
            tilpasninger=tilpasninger, stier=stier,
            oploesning=self.parameterAsDouble(parameters, self.OPLOESNING, context),
            stroem_taerskel_ha=self.parameterAsDouble(
                parameters, self.STROEM_TAERSKEL, context),
            snap_celler=self.parameterAsInt(parameters, self.SNAP_CELLER, context),
            burn=self.parameterAsBool(parameters, self.BURN, context),
            burn_dybde=self.parameterAsDouble(parameters, self.BURN_DYBDE, context),
            breach_dist=self.parameterAsInt(parameters, self.BREACH_DIST, context),
            indloeb_rapport_ha=self.parameterAsDouble(
                parameters, self.TAERSKEL_HA, context),
            min_polygon_m2=self.parameterAsDouble(parameters, self.MIN_POLYGON, context),
            stop_ved_kant=self.parameterAsBool(parameters, self.STOP_VED_KANT, context),
        )
        # Er trin 0-2 lavet med præcis de her parametre — enten af
        # strømningsvejs-trinnet eller hentet fra det præberegnede bibliotek — så
        # springes de over. Mærket siger det direkte; tidsstempler ville lyve om
        # rastere der er kopieret ind fra biblioteket.
        noegle = om.konditioneringsnoegle(konf)
        maerke = om.kan_springe_konditionering_over(stier, noegle)
        foerste_trin = 3 if maerke else 0
        if maerke:
            om.meld(feedback,
                    'Genbrugte konditioneringen fra '
                    + str(maerke.get('kilde', 'ukendt kilde')))

        if om.gem_konfiguration(konf, stier['konf_fil']) and not maerke:
            feedback.pushInfo(
                'Parametrene er ændret siden sidst — konditioneringen af terrænet '
                'laves om (det er den tunge del).')

        oplande.AKTIV_KONFIG = stier['konf_fil']
        koersel_id = dt.datetime.now().strftime('%Y%m%d_%H%M%S')

        def fremdrift(procent, tekst):
            feedback.setProgress(procent)
            feedback.setProgressText(tekst)

        log = oplande.Log(stier['log'] / f'{koersel_id}.log', koersel_id,
                          ekstra=feedback.pushInfo)
        try:
            res = oplande.koer_analyse(konf, log, koersel_id,
                                       foerste_trin=foerste_trin,
                                       afbryd=feedback.isCanceled,
                                       fremdrift=fremdrift)
            oplande.skriv_resultat(res, log)
        except oplande.Afbrudt:
            log.skriv('afbrudt af brugeren')
            return {}
        except oplande.OplandsFejl as e:
            log.skriv(f'FEJL: {e}')
            raise QgsProcessingException(str(e))
        finally:
            log.luk()

        # ── forbehold der SKAL ses ───────────────────────────────────────────
        fejlede = res['qa'].get('_fejlede') or []
        if fejlede:
            feedback.reportError(
                'QA-tjek der IKKE bestod: ' + ', '.join(fejlede)
                + '. Lagene er skrevet, men tallene bør ikke føres ind i regnearket '
                  'før hvert fejlet tjek er forklaret. Detaljer i '
                  f'{stier["log"] / (koersel_id + "_qa.yml")}', fatalError=False)
        if res['kant_celler']:
            feedback.reportError(
                f'Oplandet når rasterkanten i {res["kant_celler"]} celler. '
                f'De {res["totalopland_ha"]:.1f} ha er et MINIMUM — der mangler '
                'terræn. Hent højdemodellen igen for et større område og kør igen.',
                fatalError=False)
        for d in res['gennemstroemning']:
            feedback.pushWarning(
                f'{d["navn"]} løber IGENNEM projektområdet: ind med {d["akk_ha"]:.0f} ha, '
                f'ud igen med {d["udloeb_akk_ha"]:.0f} ha. Det opland afvander ikke til '
                'fladen, men passerer i en kanal — se feltet "karakter" i Oplande.gpkg.')

        # ── de tre lag resten af pluginnet regner videre på ───────────────────
        gemte = self._skriv_pluginlag(parameters, context, feedback, stier['gpkg'],
                                      projekt, maal_crs, ud_mappe, res)

        # ── regneark fra Grunddata til Resultater (hvis ikke der) ─────────────
        grunddata = om.grunddata_mappe()
        resultater_mappe = find_resultater()
        if grunddata and resultater_mappe:
            kilde = os.path.join(grunddata, GRUNDDATA_REGNEARK)
            maal  = os.path.join(resultater_mappe, get_resultat_regneark_navn())
            if os.path.isfile(kilde) and not os.path.isfile(maal):
                shutil.copy2(kilde, maal)
                feedback.pushInfo(f'Regneark kopieret til: {maal}')

        feedback.pushInfo(f'Hele leverancen med alle lag: {stier["gpkg"]}')
        return {
            self.DIREKTE_OPLAND: gemte.get('Direkte_Opland', ''),
            self.VANDOPLANDET:   gemte.get('Vandoplandet', ''),
            self.KLIPPE_OMRAADE: gemte.get('Klippe_Omraade', ''),
        }

    # ── output i pluginnets navne ────────────────────────────────────────────

    @staticmethod
    def _deloplande_modul():
        """Indlæser Scripts/deloplande.py fra denne plugin-kopi."""
        import importlib.util
        import sys

        sti = os.path.join(om.SCRIPTS, 'deloplande.py')
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
        navn = '_deloplande_' + os.path.basename(om.PLUGIN_ROOT).lower() + _mrk
        if sys.modules.get(navn) is not None:
            return sys.modules[navn]
        if not os.path.isfile(sti):
            raise QgsProcessingException(f'deloplande.py mangler: {sti}')
        spec = importlib.util.spec_from_file_location(navn, sti)
        modul = importlib.util.module_from_spec(spec)
        sys.modules[navn] = modul
        spec.loader.exec_module(modul)
        return modul

    def _skriv_pluginlag(self, parameters, context, feedback, gpkg: Path, projekt,
                         crs, ud_mappe: Path, res: dict):
        """Oversætter leverancen til de tre lag resten af pluginnet forventer.

        Leverancen er den fulde sandhed; disse tre er den delmængde de
        efterfølgende modeller regner på, og de defineres præcis som før:
          Vandoplandet   = ∪(vandløbsoplande) − projektområdet
          Direkte_Opland = direkte opland − projektområdet
          Klippe_Omraade = totalopland ∪ projektområdet
        Projektområdet trækkes fra, fordi det tælles for sig i regnearket —
        modellens direkte opland indeholder det (det er et restareal).
        """
        omraade_geom = self._geom_fra_fil(projekt[0], projekt[1])
        if omraade_geom is None:
            raise QgsProcessingException('Projektområdet har ingen gyldig geometri.')
        total_geom = self._geom_fra_gpkg(gpkg, 'total_opland')
        direkte_raa = self._geom_fra_gpkg(gpkg, 'direkte_opland')
        vand_raa = self._geom_fra_gpkg(gpkg, 'vandloebsoplande')

        if total_geom is None:
            raise QgsProcessingException(
                f'Totaloplandet mangler i leverancen {gpkg} — beregningen nåede ikke '
                'at skrive det. Se loggen.')
        if vand_raa is None:
            raise QgsProcessingException(
                'Der blev ikke beregnet ét eneste vandløbsopland. Enten løber der '
                'reelt intet kortlagt vandløb ind i projektområdet — og så er hele '
                'totaloplandet direkte opland — eller vandløbslaget dækker ikke '
                f'området. Kontrollér det i {gpkg} (lagene indloebspunkter og '
                'vandveje_beregnet) før du går videre; regnearket kan ikke udfyldes '
                'uden et vandopland.')

        vandoplandet_geom = vand_raa.difference(omraade_geom)
        direkte_geom = (direkte_raa.difference(omraade_geom)
                        if direkte_raa is not None else QgsGeometry())
        klippe_geom = total_geom.combine(omraade_geom)

        if direkte_geom.isEmpty():
            feedback.pushWarning(
                'Det direkte opland er tomt: hele totaloplandet kommer ind gennem '
                'kortlagte vandløb. Kontrollér at det er rigtigt.')

        # Deloplandene som ét redigerbart lag. Skrives FØR de to samlede filer,
        # for de udledes af det: retter man `type` paa et delopland, slaar det
        # igennem naeste gang regnearket fyldes ud.
        dl = self._deloplande_modul()
        antal_del = dl.skriv_deloplande(
            om, gpkg, ud_mappe / dl.FILNAVN, omraade_geom, crs)
        feedback.pushInfo(
            f'Deloplande: {antal_del} stykke(r) skrevet til {ud_mappe / dl.FILNAVN}. '
            'Ret kolonnen "type" dér for at flytte et delopland mellem '
            'vandløbsopland og direkte opland.')
        context.addLayerToLoadOnCompletion(
            f'{ud_mappe / dl.FILNAVN}|layername={dl.LAGNAVN}',
            QgsProcessingContext.LayerDetails(
                dl.LAGNAVN, QgsProject.instance(), dl.LAGNAVN))

        gemte = {}
        for lagnavn, geom, dest in (
            ('Direkte_Opland', direkte_geom, self.DIREKTE_OPLAND),
            ('Vandoplandet',   vandoplandet_geom, self.VANDOPLANDET),
            ('Klippe_Omraade', klippe_geom, self.KLIPPE_OMRAADE),
        ):
            sti = ud_mappe / f'{lagnavn}.gpkg'
            self._gem_geometri(sti, lagnavn, geom, crs)
            feedback.pushInfo(f'Eksporteret: {sti} ({geom.area() / 1e4:.1f} ha)')
            gemte[lagnavn] = str(sti)
            detaljer = QgsProcessingContext.LayerDetails(
                lagnavn, QgsProject.instance(), lagnavn)
            context.addLayerToLoadOnCompletion(str(sti), detaljer)

            # Brugerens eventuelle egen destination får en kopi af samme geometri.
            valgt = self.parameterAsOutputLayer(parameters, dest, context)
            if valgt:
                self._gem_geometri(Path(valgt), lagnavn, geom, crs)

        # Arealerne skal kunne holdes op mod modellens egne tal i loggen.
        om.meld(feedback,
                f'Totalopland {res["totalopland_ha"]:.1f} ha: vandløbsopland '
                f'{res["vandloebsopland_ha"]:.1f} ha + direkte opland '
                f'{res["direkte_ha"]:.1f} ha')
        feedback.pushInfo(
            f'Totalopland {res["totalopland_ha"]:.1f} ha = vandløbsopland '
            f'{res["vandloebsopland_ha"]:.1f} ha + direkte opland '
            f'{res["direkte_ha"]:.1f} ha (heraf projektområdet '
            f'{res["projekt_ha"]:.1f} ha, som ikke er med i Direkte_Opland.gpkg).')
        return gemte

    @staticmethod
    def _geom_fra_gpkg(gpkg: Path, lagnavn: str):
        """Foreningen af alle geometrier i et lag i leverancen. None hvis tomt."""
        lag = QgsVectorLayer(f'{gpkg}|layername={lagnavn}', lagnavn, 'ogr')
        if not lag.isValid():
            return None
        geoms = [f.geometry() for f in lag.getFeatures()
                 if f.geometry() and not f.geometry().isEmpty()]
        return QgsGeometry.unaryUnion(geoms) if geoms else None

    @staticmethod
    def _geom_fra_fil(sti: Path, lagnavn):
        kilde = f'{sti}|layername={lagnavn}' if lagnavn else str(sti)
        lag = QgsVectorLayer(kilde, 'projektomraade', 'ogr')
        if not lag.isValid():
            return None
        geoms = [f.geometry() for f in lag.getFeatures()
                 if f.geometry() and not f.geometry().isEmpty()]
        return QgsGeometry.unaryUnion(geoms) if geoms else None

    @staticmethod
    def _gem_geometri(sti: Path, lagnavn: str, geom, crs):
        """Skriver ÉN geometri som ét objekt uden attributter — som hidtil.

        Lagene læses af modeller der refererer dem ved navn og summerer
        area($geometry), så strukturen skal blive præcis den de kender.
        """
        vl = QgsVectorLayer(f'MultiPolygon?crs={crs.authid()}', lagnavn, 'memory')
        g = QgsGeometry(geom)
        if not g.isEmpty():
            g.convertToMultiType()
        feat = QgsFeature()
        feat.setGeometry(g)
        vl.dataProvider().addFeature(feat)
        vl.updateExtents()

        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = ('GPKG' if str(sti).lower().endswith('.gpkg')
                           else 'ESRI Shapefile')
        opts.fileEncoding = 'UTF-8'
        opts.layerName = lagnavn
        # Anden koersel skriver oven i filen fra den foerste, og GeoPackage-
        # skriveren naegter medmindre den faar besked. Hver fil rummer ét lag.
        opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        fejl, besked, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
            vl, str(sti), QgsCoordinateTransformContext(), opts)
        if fejl != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(
                f'Kunne ikke gemme "{lagnavn}" til {sti}: {besked or fejl}. '
                'Ligger laget åbent i QGIS? Fjern det og kør igen.')
