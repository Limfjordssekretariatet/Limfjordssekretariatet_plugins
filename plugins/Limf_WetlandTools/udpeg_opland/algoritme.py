# -*- coding: utf-8 -*-
"""Udpeg opland — ét værktøj, fra et polygon eller et punkt til oplandet.

Værktøjet var et selvstændigt plugin og ligger nu i Vådområder. Beregningen
er N-regnearkspluginnets — dets oplandsmodel.py og oplande.py indlæses direkte
fra dets mappe, ikke fra en kopi her. Kopien i det gamle plugin var allerede
gledet fra N-regnearkets, og en rettelse det ene sted nåede ikke det andet.
Grunddata (vandløb og tilpasningslinjer) kommer samme sted fra.

Beregningen er skåret ned til det ene formål. Kæden er:

    trin 0-2   terrænet skaffes og konditioneres, og strømningen udledes
    trin 3-6   oplandet spores og skrives ud

Trin 0-2 er den tunge del, og den afhænger KUN af terrænet og parametrene — ikke
af hvor området ligger. Derfor slås det præberegnede grundlag op først: dækker en
flise området, hentes den, og så er der ingenting at konditionere.
"""
import datetime as dt
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)

ROD = os.path.dirname(os.path.realpath(__file__))

#: N-regnearkspluginnets mappenavn — beregningen og grunddata hentes derfra.
NREGNEARK = 'Limf_Nregneark'

#: Standardværdier til værktøjskassen, hvis N-regnearket mangler, når den
#: bygges. Selve kørslen stopper så med en forklaring (indlaes_oplandsmodel).
_RESERVE = {
    'STD_GRUNDLAG_URL': '', 'STD_OPLOESNING': 2.0,
    'STD_DOWNLOAD_BUFFER': 5000.0, 'STD_STROEM_TAERSKEL': 250.0,
    'STD_SNAP_CELLER': 5, 'STD_BURN': True, 'STD_BURN_DYBDE': 1.0,
    'STD_BREACH_DIST': 25, 'STD_TAERSKEL_HA': 50.0, 'STD_MIN_POLYGON': 100.0,
}


def nregneark_mappe():
    """Mappen med N-regnearkspluginnet, eller None.

    Pluginnene installeres side om side, så det ligger ved siden af
    Vådområder. Profilens plugin-mappe prøves også — det er der, QGIS
    installerer det, hvis Vådområder selv ligger et andet sted.
    """
    kandidater = [os.path.join(os.path.dirname(os.path.dirname(ROD)), NREGNEARK)]
    try:
        from qgis.utils import home_plugin_path
        kandidater.append(os.path.join(home_plugin_path, NREGNEARK))
    except Exception:
        pass
    for mappe in kandidater:
        if os.path.isfile(os.path.join(mappe, 'Scripts', 'oplandsmodel.py')):
            return os.path.realpath(mappe)
    return None


def indlaes_oplandsmodel():
    """Indlæser N-regnearkspluginnets Scripts/oplandsmodel.py.

    Modulnavnet dannes præcis som i N-regnearket selv
    (Udpeg_Oplande_N_Script._fælles), så de to deler ét modul i en
    QGIS-session og ikke kan komme til at regne på hver sin udgave.
    """
    rod = nregneark_mappe()
    if rod is None:
        raise QgsProcessingException(
            'Udpeg opland bruger beregningen fra "Vandprojekter – N-regneark", '
            'og det plugin blev ikke fundet. Installér det under Udvidelser > '
            'Administrer og installér udvidelser — det ligger i samme '
            'repository som Vådområder.')
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
    if sys.modules.get(navn) is not None:
        return sys.modules[navn]
    spec = importlib.util.spec_from_file_location(navn, sti)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[navn] = modul
    spec.loader.exec_module(modul)
    return modul


class UdpegOpland(QgsProcessingAlgorithm):

    OMRAADE = 'OMRAADE'
    PUNKT = 'PUNKT'
    PUNKTER = 'PUNKTER'
    PUNKT_ID_FELT = 'PUNKT_ID_FELT'
    DISJUNKTE = 'DISJUNKTE'
    TOKEN = 'TOKEN'
    DHM = 'DHM'

    GRUNDLAG = 'GRUNDLAG'
    GRUNDLAG_URL = 'GRUNDLAG_URL'
    VANDLOEB = 'VANDLOEB'
    TILPASNINGER = 'TILPASNINGER'
    ARBEJDSMAPPE = 'ARBEJDSMAPPE'

    OPLOESNING = 'OPLOESNING'
    BUFFER = 'BUFFER'
    STROEM_TAERSKEL = 'STROEM_TAERSKEL'
    SNAP_CELLER = 'SNAP_CELLER'
    SOEGERADIUS = 'SOEGERADIUS'
    BURN = 'BURN'
    BURN_DYBDE = 'BURN_DYBDE'
    BREACH_DIST = 'BREACH_DIST'
    TAERSKEL_HA = 'TAERSKEL_HA'
    MIN_POLYGON = 'MIN_POLYGON'

    OPLAND = 'OPLAND'
    VANDLOEBSOPLANDE = 'VANDLOEBSOPLANDE'
    DIREKTE_OPLAND = 'DIREKTE_OPLAND'

    def name(self):
        return 'udpeg_opland'

    def displayName(self):
        return 'Udpeg opland'

    def group(self):
        return ''

    def groupId(self):
        return ''

    def createInstance(self):
        return UdpegOpland()

    def shortHelpString(self):
        return (
            '<p>Udpeger oplandet til et område, til ét punkt eller til hvert '
            'punkt i et lag.</p>'
            '<p><b>Giv ét af de tre input.</b> Er der givet flere, bruges det '
            'første i rækkefølgen område → punktlag → punkt. Det enkelte punkt '
            'kan prikkes direkte på kortet med knappen ude til højre.</p>'
            '<ul>'
            '<li><b>Polygon:</b> hele oplandet til fladen udpeges, og det deles op '
            'i vandløbsoplande (det der løber ind gennem et kortlagt vandløb) og '
            'direkte opland (resten).</li>'
            '<li><b>Punkt:</b> oplandet opstrøms for punktet udpeges. Punktet '
            'flyttes først hen på nærmeste strømningsvej.</li>'
            '<li><b>Punktlag:</b> oplandet udpeges for hvert punkt i laget, og '
            'terrænet konditioneres kun én gang for dem alle. Det er den tunge '
            'del, så mange punkter i samme egn er meget hurtigere her end ét ad '
            'gangen. Ligger punkterne spredt ud over landet, bliver terræn-'
            'udsnittet til gengæld stort. Hvert punkts fulde opland udpeges — så '
            'to punkter på samme vandløb får overlappende oplande, med mindre '
            '"Gør oplandene disjunkte" er sat under Avanceret.</li>'
            '</ul>'
            '<p><b>Terrænet skaffer værktøjet selv.</b> Dækker det præberegnede '
            'grundlag området, hentes den flise — det tager sekunder. Ellers hentes '
            'DHM/Terræn fra Dataforsyningen, og det kræver et token (gratis på '
            'dataforsyningen.dk). Har du højdemodellen i forvejen, kan du pege på '
            'den i stedet.</p>'
            '<p>Oplandet er kun så godt som terrænet: i byområde løber vandet i rør, '
            'ikke efter terræn, og dér passer det ikke.</p>'
        )

    # ── parametre ───────────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        from qgis.core import QgsProcessingParameterDefinition

        def avanceret(p):
            p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
            return p

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.OMRAADE, 'Område (polygon)',
            types=[QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterPoint(
            self.PUNKT, '… eller et punkt på kortet', optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.PUNKTER, '… eller et lag med mange punkter',
            types=[QgsProcessing.TypeVectorPoint], optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.PUNKT_ID_FELT, 'Felt der navngiver hvert punkt (valgfrit)',
            parentLayerParameterName=self.PUNKTER, optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.TOKEN, 'Dataforsyningen-token (kun hvis terrænet skal hentes)',
            optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DHM, 'Egen højdemodel (valgfri — ellers skaffes den selv)',
            optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.SOEGERADIUS, 'Punktet flyttes til nærmeste vandløb inden for (m)',
            type=QgsProcessingParameterNumber.Double, defaultValue=100.0,
            minValue=0.0))

        # Mangler N-regnearket, skal værktøjskassen stadig kunne bygges —
        # kørslen siger så til. Ellers ville hele udbyderen fejle.
        try:
            om = indlaes_oplandsmodel()
        except Exception:
            om = None

        def std(navn):
            return getattr(om, navn) if om is not None else _RESERVE[navn]

        self.addParameter(avanceret(QgsProcessingParameterVectorLayer(
            self.VANDLOEB, 'Kortlagte vandløb (linjer)',
            types=[QgsProcessing.TypeVectorLine],
            defaultValue=om.vandloeb_standard() if om else None, optional=True)))
        self.addParameter(avanceret(QgsProcessingParameterVectorLayer(
            self.TILPASNINGER, 'Hydrologiske tilpasningslinjer (DHMLinje)',
            types=[QgsProcessing.TypeVectorLine],
            defaultValue=om.tilpasninger_standard() if om else None,
            optional=True)))
        self.addParameter(avanceret(QgsProcessingParameterFile(
            self.GRUNDLAG, 'Bibliotek med præberegnede fliser',
            behavior=QgsProcessingParameterFile.Folder,
            defaultValue=str(self._standard_bibliotek()), optional=True)))
        self.addParameter(avanceret(QgsProcessingParameterString(
            self.GRUNDLAG_URL, 'Adresse på det udgivne grundlag ("ingen" slår det fra)',
            defaultValue=std('STD_GRUNDLAG_URL'), optional=True)))
        self.addParameter(avanceret(QgsProcessingParameterFile(
            self.ARBEJDSMAPPE, 'Arbejdsmappe (mellemresultater og log)',
            behavior=QgsProcessingParameterFile.Folder, optional=True)))

        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.OPLOESNING, 'Analyseopløsning (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=std('STD_OPLOESNING'), minValue=0.4, maxValue=20.0)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.BUFFER, 'Terræn hentes så langt uden om området (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=std('STD_DOWNLOAD_BUFFER'), minValue=0.0)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.STROEM_TAERSKEL, 'Vandløb fra (ha opstrøms)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=std('STD_STROEM_TAERSKEL'), minValue=0.1)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.SNAP_CELLER, 'Snap-radius (celler)',
            defaultValue=std('STD_SNAP_CELLER'), minValue=0)))
        self.addParameter(avanceret(QgsProcessingParameterBoolean(
            self.BURN, 'Brænd de kortlagte vandløb ind',
            defaultValue=std('STD_BURN'))))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.BURN_DYBDE, 'Brændingsdybde (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=std('STD_BURN_DYBDE'), minValue=0.0)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.BREACH_DIST, 'Breaching-søgeafstand (celler)',
            defaultValue=std('STD_BREACH_DIST'), minValue=0)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.TAERSKEL_HA, 'Et tilløb udpeges selvstændigt fra (ha)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=std('STD_TAERSKEL_HA'), minValue=0.0)))
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.MIN_POLYGON, 'Mindste polygon der beholdes (m²)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=std('STD_MIN_POLYGON'), minValue=0.0)))
        self.addParameter(avanceret(QgsProcessingParameterBoolean(
            self.DISJUNKTE,
            'Punktlag: gør oplandene disjunkte hvor punkter ligger opstrøms for '
            'hinanden (ét punkts opland stopper ved det næste — hurtigere)',
            defaultValue=False)))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OPLAND, 'Opland(e)'))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.VANDLOEBSOPLANDE, 'Vandløbsoplande', optional=True,
            createByDefault=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.DIREKTE_OPLAND, 'Direkte opland', optional=True,
            createByDefault=False))

    @staticmethod
    def _standard_bibliotek():
        """Hvor hentede fliser lægges.

        Har brugeren N-regnearkspluginnet med et bibliotek der FAKTISK indeholder
        fliser, bruges dét — så hentes de samme gigabyte ikke to gange. En sti der
        bare står i indstillingerne uden noget i, tælles ikke: den kan være en
        gammel prøvemappe, og så ville fliserne havne et sted brugeren har glemt.

        Ellers: under QGIS-profilen, hvor de overlever en genstart.
        """
        from qgis.core import QgsApplication, QgsSettings

        tidligere = QgsSettings().value('vaadomraade_modeller/grundlag_mappe', '')
        if tidligere and os.path.isdir(tidligere):
            for post in os.scandir(tidligere):
                if post.is_dir() and os.path.isfile(
                        os.path.join(post.path, 'manifest.json')):
                    return Path(tidligere)
        return Path(QgsApplication.qgisSettingsDirPath()) / 'oplandsgrundlag'

    # ── kørslen ─────────────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        om = indlaes_oplandsmodel()
        om.tjek_forudsaetninger(feedback)
        oplande = om.indlaes_oplande()

        # Tilpasningslinjerne følger ikke med N-regnearket — de hentes fra en
        # release første gang, samme sted som N-regnearket selv lægger dem.
        # Uden dem bliver hver vejdæmning et kunstigt vandskel.
        gd = om.grunddata_modul()
        if gd is not None:
            try:
                gd.sikr('DHMLinje', feedback=feedback)
            except Exception as e:
                feedback.pushInfo(f'DHMLinje kunne ikke hentes: {e!r}')

        arbejde = self._arbejdsmappe(parameters, context)
        stier = om.arbejdsstier(arbejde)
        om.frigiv_filer([stier['gpkg']], feedback)

        omraade_lag = self.parameterAsVectorLayer(parameters, self.OMRAADE, context)
        punkter_kilde = self.parameterAsSource(parameters, self.PUNKTER, context)
        id_felt = self.parameterAsString(parameters, self.PUNKT_ID_FELT, context)
        har_omraade = omraade_lag is not None
        har_punkter = punkter_kilde is not None and punkter_kilde.featureCount() != 0
        har_punkt = bool(self.parameterAsString(parameters, self.PUNKT, context))

        valgt = [navn for navn, er_givet in (
            ('område', har_omraade), ('punktlag', har_punkter), ('punkt', har_punkt))
            if er_givet]
        if not valgt:
            raise QgsProcessingException(
                'Giv enten et polygonlag under "Område", et punktlag eller et '
                'punkt på kortet. Uden et af delene er der ikke noget at udpege '
                'oplandet til.')
        if len(valgt) > 1:
            feedback.pushWarning(
                f'Flere input er givet ({", ".join(valgt)}). Rækkefølgen er '
                f'område → punktlag → punkt — her bruges "{valgt[0]}".')
        har_omraade = valgt[0] == 'område'
        har_punkter = valgt[0] == 'punktlag'
        har_punkt = valgt[0] == 'punkt'

        # ── terrænets CRS afgør alt. Modellen reprojicerer ikke selv ────────
        dem_lag = self.parameterAsRasterLayer(parameters, self.DHM, context)
        dem_sti = epsg = maal_crs = None
        if dem_lag is not None:
            dem_sti = om.dem_som_fil(dem_lag, feedback)
            epsg, maal_crs = om.epsg_af_dem(dem_lag, feedback)
        else:
            epsg = 25832
            maal_crs = QgsCoordinateReferenceSystem('EPSG:25832')

        # ── området som fil, i terrænets CRS ────────────────────────────────
        punkter = None
        if har_punkt:
            punkt = self.parameterAsPoint(parameters, self.PUNKT, context, maal_crs)
            feedback.pushInfo(
                f'Punkt: ({punkt.x():.0f}, {punkt.y():.0f}) i {maal_crs.authid()}')
            projekt = self._punkt_som_omraade(punkt, stier['arbejdsmappe'], maal_crs,
                                              parameters, context)
        elif har_punkter:
            punkter = self._laes_punkter(punkter_kilde, id_felt, maal_crs, context,
                                         feedback)
            if not punkter:
                raise QgsProcessingException(
                    'Punktlaget er tomt eller uden gyldige punktgeometrier.')
            feedback.pushInfo(f'Punktlag: {len(punkter)} punkt(er) i {maal_crs.authid()}')
            projekt = self._punkter_som_omraade(punkter, stier['arbejdsmappe'],
                                                maal_crs, parameters, context,
                                                feedback)
        else:
            projekt = om.materialiser(self, parameters, self.OMRAADE, context,
                                      feedback, maal_crs, stier['arbejdsmappe'])
            if projekt is None:
                raise QgsProcessingException(
                    'Områdelaget er tomt eller kunne ikke indlæses.')

        vandloeb = om.materialiser(self, parameters, self.VANDLOEB, context, feedback,
                                   maal_crs, stier['arbejdsmappe'],
                                   standard=om.vandloeb_standard())
        tilpasninger = om.materialiser(self, parameters, self.TILPASNINGER, context,
                                       feedback, maal_crs, stier['arbejdsmappe'],
                                       standard=om.tilpasninger_standard())
        if vandloeb is None:
            feedback.pushWarning(
                'Det kortlagte vandløbsnetværk blev ikke fundet. Oplandet kan godt '
                'udpeges, men det kan ikke deles i vandløbsopland og direkte '
                'opland — den opdeling er defineret ud fra de kortlagte vandløb.')
        if tilpasninger is None:
            feedback.pushWarning(
                'Ingen hydrologiske tilpasningslinjer fundet: hver vejdæmning og '
                'bane bliver et ubrudt vandskel, fordi laserscanningen ser dæmningen '
                'men ikke røret under den. Oplandet bliver systematisk for lille.')

        def byg_konf(dem):
            return om.byg_konfiguration(
                epsg=epsg, dem_sti=dem, projekt=projekt, vandloeb=vandloeb,
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
                min_polygon_m2=self.parameterAsDouble(
                    parameters, self.MIN_POLYGON, context),
            )

        konf = byg_konf(dem_sti or '(afgøres nedenfor)')
        noegle = om.konditioneringsnoegle(konf)

        # ── er konditioneringen lavet i forvejen? ───────────────────────────
        flise, kilde = self._find_flise(om, parameters, context, feedback, projekt,
                                        noegle)
        if flise is not None:
            dem_sti = flise / '01_dem_hydro.tif'

        koersel_id = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
        log = oplande.Log(stier['log'] / f'{koersel_id}.log', koersel_id,
                          ekstra=feedback.pushInfo)
        try:
            oplande.registrer_whitebox(log)
            om.vaelg_fill(oplande, konf, log)
            om.haardfoer_whitebox(oplande, log)

            # Trin 0-2 laegges paa plads FOER oplandet spores — enten fra flisen
            # eller ved at regne dem. Begge veje ender med de samme tre rastere i
            # mellemresultaterne, og saa er resten den samme kode.
            if flise is not None:
                om.hent_flise(flise, stier['derived'], feedback, log)
                konf = byg_konf(dem_sti)
                om.gem_konfiguration(konf, stier['konf_fil'])
                oplande.AKTIV_KONFIG = stier['konf_fil']
                oplande.gem_parameterlog(konf, stier['log'], koersel_id)
                feedback.setProgress(45)
            else:
                if dem_sti is None:
                    dem_sti, epsg, maal_crs = self._skaf_terraen(
                        om, parameters, context, feedback, stier, projekt)
                    konf = byg_konf(dem_sti)
                om.gem_konfiguration(konf, stier['konf_fil'])
                oplande.AKTIV_KONFIG = stier['konf_fil']
                oplande.gem_parameterlog(konf, stier['log'], koersel_id)

                feedback.setProgress(20)
                feedback.setProgressText('klargøring af terrænet')
                dem_analyse = oplande.trin0_klargoer(konf, log)
                if feedback.isCanceled():
                    return {}
                # Foerst her kan det afgoeres om der overhovedet er vandloeb og
                # tilpasningslinjer i udsnittet. Er der ikke, slaas de fra i stedet
                # for at stoppe koerslen — for et lille omraade ved kysten er det en
                # rigtig beskrivelse af stedet.
                for fravalg in om.undgaa_tomme_input(konf, dem_analyse, log):
                    feedback.pushInfo(om.NOEGLETAL + fravalg)

                feedback.setProgress(30)
                feedback.setProgressText('hydrologisk konditionering')
                dem_hydro = oplande.trin1_konditioner(konf, dem_analyse, log)
                if feedback.isCanceled():
                    return {}

                feedback.setProgress(40)
                feedback.setProgressText('strømningsretning og -akkumulering')
                oplande.trin2_stroemning(konf, dem_hydro, log)
                feedback.setProgress(45)

            if feedback.isCanceled():
                return {}

            def fremdrift(procent, tekst):
                feedback.setProgress(45 + procent * 0.5)
                feedback.setProgressText(tekst)

            if har_punkt:
                svar = self._opland_fra_punkt(om, oplande, konf, stier, punkt,
                                              log, feedback, parameters, context)
            elif har_punkter:
                svar = self._oplande_fra_punkter(om, oplande, konf, stier, punkter,
                                                 id_felt, punkter_kilde.fields(),
                                                 log, feedback, parameters, context)
            else:
                res = oplande.koer_analyse(konf, log, koersel_id, foerste_trin=3,
                                           afbryd=feedback.isCanceled,
                                           fremdrift=fremdrift)
                oplande.skriv_resultat(res, log)
                svar = self._skriv_fra_leverance(parameters, context, feedback,
                                                 stier['gpkg'], maal_crs, kilde)
        except oplande.Afbrudt:
            log.skriv('afbrudt af brugeren')
            return {}
        finally:
            log.luk()

        return svar

    # ── område fra et eller flere punkter ───────────────────────────────────

    @staticmethod
    def _rektangel_lag(sti: Path, crs, minx, miny, maxx, maxy):
        """Skriver ét rektangel som MultiPolygon i en frisk GeoPackage.

        Modellen læser sit område fra en fil, og flisesøgningen skal have en
        geometri at slå op med. Rektanglet er kun til dét — selve oplandet spores
        fra punktet/punkterne bagefter, ikke fra rektanglet.
        """
        from osgeo import ogr, osr

        sti = Path(sti)
        drv = ogr.GetDriverByName('GPKG')
        if sti.exists():
            drv.DeleteDataSource(str(sti))
        ds = drv.CreateDataSource(str(sti))
        sr = osr.SpatialReference()
        sr.ImportFromEPSG(int(crs.authid().split(':')[-1]))
        lag = ds.CreateLayer('omraade', sr, ogr.wkbMultiPolygon)
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in ((minx, miny), (maxx, miny), (maxx, maxy),
                     (minx, maxy), (minx, miny)):
            ring.AddPoint_2D(x, y)
        poly = ogr.Geometry(ogr.wkbPolygon)
        poly.AddGeometry(ring)
        f = ogr.Feature(lag.GetLayerDefn())
        f.SetGeometry(ogr.ForceToMultiPolygon(poly))
        lag.CreateFeature(f)
        ds = None

    def _punkt_som_omraade(self, punkt, arbejdsmappe, crs, parameters, context):
        """Det ene punkt som en lille firkant på disk."""
        halv = max(2.0, self.parameterAsDouble(parameters, self.OPLOESNING, context))
        sti = Path(arbejdsmappe) / 'punkt_omraade.gpkg'
        self._rektangel_lag(sti, crs, punkt.x() - halv, punkt.y() - halv,
                            punkt.x() + halv, punkt.y() + halv)
        return (sti, 'omraade')

    def _laes_punkter(self, kilde, id_felt, crs, context, feedback):
        """Punktlaget som en liste af (id, x, y) i terrænets CRS.

        Et objekt med flere punkter (MultiPoint) bidrager kun med sit første —
        ét opland pr. objekt. `id` er værdien af det valgte felt, ellers objektets
        id, og den følger med ud i oplandslaget så hvert opland kan spores tilbage.
        """
        src_crs = kilde.sourceCrs()
        xform = None
        if src_crs.isValid() and src_crs != crs:
            xform = QgsCoordinateTransform(src_crs, crs, context.transformContext())
            feedback.pushInfo(
                f'  punkter reprojiceres fra {src_crs.authid()} til {crs.authid()}')
        idx = kilde.fields().lookupField(id_felt) if id_felt else -1

        ud = []
        flerpunkt = 0
        for f in kilde.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            hjoerner = [QgsPointXY(v) for v in g.vertices()]
            if not hjoerner:
                continue
            if len(hjoerner) > 1:
                flerpunkt += 1
            p = hjoerner[0]
            if xform is not None:
                try:
                    p = xform.transform(p)
                except Exception:
                    feedback.pushWarning(
                        '  et punkt kunne ikke reprojiceres — springes over')
                    continue
            ident = f.attributes()[idx] if idx >= 0 else f.id()
            ud.append((ident, p.x(), p.y()))
        if flerpunkt:
            feedback.pushInfo(
                f'  {flerpunkt} objekt(er) har flere punkter — kun det første bruges')
        return ud

    def _punkter_som_omraade(self, punkter, arbejdsmappe, crs, parameters, context,
                             feedback):
        """Alle punkter som ét dækkende rektangel på disk.

        Terrænet skal dække hvert punkt OG hele oplandet opstrøms for det. Her
        skaffes terrænet for punkternes fælles omgivende rektangel; download-
        bufferen ("Terræn hentes så langt uden om området") lægges udenom.
        Ligger punkterne meget spredt, bliver rektanglet stort — så er ét punkt
        ad gangen billigere.
        """
        halv = max(2.0, self.parameterAsDouble(parameters, self.OPLOESNING, context))
        xs = [x for _, x, _ in punkter]
        ys = [y for _, _, y in punkter]
        minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
        km2 = (maxx - minx + 2 * halv) * (maxy - miny + 2 * halv) / 1e6
        feedback.pushInfo(
            f'  punkternes omgivende rektangel: {(maxx - minx) / 1000:.1f} × '
            f'{(maxy - miny) / 1000:.1f} km')
        if km2 > 400.0:
            feedback.pushWarning(
                f'Punkterne spænder over {km2:,.0f} km². Terrænet hentes og '
                'konditioneres for hele det rektangel på én gang — det kan tage '
                'lang tid og fylde meget. Overvej at dele punkterne op i grupper '
                'der ligger i samme egn.')
        sti = Path(arbejdsmappe) / 'punkter_omraade.gpkg'
        self._rektangel_lag(sti, crs, minx - halv, miny - halv,
                            maxx + halv, maxy + halv)
        return (sti, 'omraade')

    def _opland_fra_punkt(self, om, oplande, konf, stier, punkt, log, feedback,
                          parameters, context):
        """Sporer oplandet opstrøms for punktet.

        For et punkt giver opdelingen i vandløbsopland og direkte opland ingen
        mening — begge dele er defineret ud fra en flade. Svaret er ét opland:
        alt hvad der løber forbi punktet.
        """
        import numpy as np
        from osgeo import gdal

        derived = Path(konf['output']['derived_dir'])
        dem_hydro = derived / '01_dem_hydro.tif'
        pointer = derived / '02_d8_pointer.tif'
        akkumulering = derived / '02_d8_akkumulering.tif'
        for sti in (dem_hydro, pointer, akkumulering):
            if not Path(sti).is_file():
                raise QgsProcessingException(
                    f'Strømningen mangler i mellemresultaterne: {sti}')

        feedback.setProgress(60)
        feedback.setProgressText('sporer oplandet')
        graf = oplande.Stroemningsgraf(pointer, dem_hydro, log)

        celle = graf.celle_ved(punkt.x(), punkt.y())
        if celle is None:
            raise QgsProcessingException(
                'Punktet ligger uden for det beregnede terræn. Prik inden for det '
                'område terrænet dækker.')

        # Punktet flyttes hen paa stroemningsvejen. Et prik ved siden af aaen giver
        # ellers oplandet til en markgroeft paa et par hektar — det ser ud som et
        # svar, og det er forkert.
        soeg_m = self.parameterAsDouble(parameters, self.SOEGERADIUS, context)
        celle, flyttet, fundet_ha = self._snap(graf, akkumulering, celle, soeg_m)
        x, y = graf.koordinat(celle)
        if flyttet > 0:
            om.meld(feedback,
                    f'Punktet flyttet {flyttet:.0f} m hen på nærmeste '
                    f'strømningsvej: ({x:.0f}, {y:.0f})')
        if fundet_ha < 1.0:
            feedback.pushWarning(
                f'ADVARSEL: der er intet vandløb af betydning inden for '
                f'{soeg_m:.0f} m af punktet — det kraftigste sted i nærheden har '
                f'kun {fundet_ha:.2f} ha opstrøms. Enten er prikket sat ved siden '
                f'af åen, eller også er der virkelig ingen. Hæv søgeradius, eller '
                f'prik tættere på.')

        sporet = graf.opstroems(np.array([celle], dtype=np.int64))
        maske = (sporet > 0)
        maske[celle] = True
        antal = int(maske.sum())
        if antal == 0:
            raise QgsProcessingException('Der blev ikke sporet en eneste celle.')
        areal_ha = graf.ha(antal)
        om.meld(feedback, f'Opland: {areal_ha:,.1f} ha '
                          f'({areal_ha / 100:,.2f} km²), {antal:,} celler')
        self._advar_om_kanten(maske, graf, feedback)

        # Vektoriseres med motorens egne hjaelpere, saa arealkontrollen mod
        # celleantallet er den samme som i den store kaede.
        feedback.setProgress(85)
        raster = derived / '03_opland_punkt.tif'
        oplande._skriv_raster(maske.astype(np.int32), graf, raster, gdal.GDT_Int32)
        gpkg = derived / '03_opland_punkt.gpkg'
        dele = oplande._polygoniser(raster, gpkg, graf, {1: antal}, log,
                                    'oplandet til punktet')
        del graf

        geom = dele.get(1)
        if geom is None:
            raise QgsProcessingException('Oplandet kunne ikke vektoriseres.')

        # om.felt() frem for QgsField direkte: konstruktoeren er skiftet undervejs
        # i QGIS 3, og motoren har allerede en der virker begge veje.
        felter = QgsFields()
        for navn, slags in (('areal_ha', 'tal'), ('udloeb_x', 'tal'),
                            ('udloeb_y', 'tal'), ('flyttet_m', 'tal'),
                            ('metode', 'tekst'), ('dato', 'tekst')):
            felter.append(om.felt(navn, slags))
        crs = QgsCoordinateReferenceSystem(f'EPSG:{konf["crs"]["epsg"]}')
        sink, ident = self.parameterAsSink(parameters, self.OPLAND, context, felter,
                                           QgsWkbTypes.MultiPolygon, crs)
        if sink is None:
            raise QgsProcessingException('Outputlaget kunne ikke oprettes.')
        g = QgsGeometry(geom)
        g.convertToMultiType()
        f = QgsFeature(felter)
        f.setGeometry(g)
        f.setAttributes([
            round(areal_ha, 2), round(x, 1), round(y, 1), round(flyttet, 1),
            f'opstrøms for punkt, D8 på {konf["analyse"]["oplaesning_m"]:g} m',
            dt.date.today().isoformat()])
        sink.addFeature(f)
        return {self.OPLAND: ident}

    # ── oplande fra et punktlag ─────────────────────────────────────────────

    def _oplande_fra_punkter(self, om, oplande, konf, stier, punkter, id_felt,
                             kilde_felter, log, feedback, parameters, context):
        """Sporer oplandet opstrøms for hvert punkt i laget.

        Strømningsgrafen bygges én gang og genbruges for alle punkter — trin 0-2
        er den dyre del, og den er den samme uanset hvor punkterne ligger. Hvert
        punkt snappes til nærmeste strømningsvej som i enkeltpunkts-tilfældet.

        Uden "Gør oplandene disjunkte" udpeges hvert punkts FULDE opland, ét ad
        gangen; to punkter på samme vandløb får så overlappende oplande. Med
        flaget spores alle punkter i én omgang, og et punkt der ligger opstrøms
        for et andet, klipper sit opland ud af det nedstrøms — resultatet er en
        disjunkt opdeling, og det er hurtigere fordi vektoriseringen kun sker
        én gang.
        """
        import numpy as np
        from osgeo import gdal
        from qgis.core import QgsField

        derived = Path(konf['output']['derived_dir'])
        dem_hydro = derived / '01_dem_hydro.tif'
        pointer = derived / '02_d8_pointer.tif'
        akkumulering = derived / '02_d8_akkumulering.tif'
        for sti in (dem_hydro, pointer, akkumulering):
            if not Path(sti).is_file():
                raise QgsProcessingException(
                    f'Strømningen mangler i mellemresultaterne: {sti}')

        feedback.setProgress(50)
        feedback.setProgressText('bygger strømningsgraf')
        graf = oplande.Stroemningsgraf(pointer, dem_hydro, log)
        soeg_m = self.parameterAsDouble(parameters, self.SOEGERADIUS, context)
        disjunkte = self.parameterAsBool(parameters, self.DISJUNKTE, context)

        # ── hvert punkt snappes til strømningsvejen ─────────────────────────
        gyldige = []          # (ident, celle, flyttet, fundet_ha, x, y)
        for nr, (ident_vaerdi, px, py) in enumerate(punkter, start=1):
            if feedback.isCanceled():
                return {}
            celle = graf.celle_ved(px, py)
            if celle is None:
                feedback.pushWarning(
                    f'Punkt {nr} ({ident_vaerdi}) ligger uden for det beregnede '
                    'terræn — springes over.')
                continue
            celle, flyttet, fundet_ha = self._snap(graf, akkumulering, celle, soeg_m)
            x, y = graf.koordinat(celle)
            gyldige.append((ident_vaerdi, int(celle), flyttet, fundet_ha, x, y))
        if not gyldige:
            raise QgsProcessingException(
                'Ingen af punkterne ligger inden for det beregnede terræn. Hæv '
                '"Terræn hentes så langt uden om området" under Avanceret, eller '
                'kontrollér at punkterne ligger rigtigt.')
        feedback.pushInfo(f'{len(gyldige)} af {len(punkter)} punkt(er) kan spores.')

        # ── outputlaget ────────────────────────────────────────────────────
        # Id-feltet beholder punktlagets egen type (så et tal-id bliver ved med
        # at være et tal), men får et fast navn så det ikke kolliderer med de
        # felter beregningen selv lægger på.
        felter = QgsFields()
        kilde_idx = kilde_felter.lookupField(id_felt) if id_felt else -1
        if kilde_idx >= 0:
            id_felt_ud = QgsField(kilde_felter.field(kilde_idx))
            id_felt_ud.setName('punkt_id')
            felter.append(id_felt_ud)
        else:
            felter.append(om.felt('punkt_id', 'heltal'))
        for navn, slags in (('areal_ha', 'tal'), ('areal_km2', 'tal'),
                            ('udloeb_x', 'tal'), ('udloeb_y', 'tal'),
                            ('flyttet_m', 'tal'), ('snap_opstroems_ha', 'tal'),
                            ('metode', 'tekst'), ('dato', 'tekst'),
                            ('bemaerkning', 'tekst')):
            felter.append(om.felt(navn, slags))
        crs = QgsCoordinateReferenceSystem(f'EPSG:{konf["crs"]["epsg"]}')
        sink, ident = self.parameterAsSink(parameters, self.OPLAND, context, felter,
                                           QgsWkbTypes.MultiPolygon, crs)
        if sink is None:
            raise QgsProcessingException('Outputlaget kunne ikke oprettes.')

        metode = f'opstrøms for punkt, D8 på {konf["analyse"]["oplaesning_m"]:g} m'
        if disjunkte:
            metode += ', disjunkt'
        dato = dt.date.today().isoformat()
        n = len(gyldige)

        def skriv(nr, felt_id, geom, maske, flyttet, fundet_ha, x, y):
            antal = int(maske.sum())
            areal_ha = graf.ha(antal)
            noter = []
            if flyttet > 0:
                noter.append(f'flyttet {flyttet:.0f} m til strømningsvej')
            if fundet_ha < 1.0:
                noter.append(f'intet vandløb inden for {soeg_m:.0f} m (kraftigste '
                             f'{fundet_ha:.2f} ha opstrøms)')
            kant = self._kant_celler(maske, graf)
            if kant:
                noter.append(f'rører terrænkanten i {kant:,} celler — arealet er '
                             'for lille')
            g = QgsGeometry(geom)
            g.convertToMultiType()
            f = QgsFeature(felter)
            f.setGeometry(g)
            f.setAttributes([
                felt_id, round(areal_ha, 2), round(areal_ha / 100, 4),
                round(x, 1), round(y, 1), round(flyttet, 1), round(fundet_ha, 2),
                metode, dato, '; '.join(noter)])
            sink.addFeature(f)
            om.meld(feedback, f'punkt {nr}/{n} ({felt_id}): {areal_ha:,.1f} ha'
                              + (f' — {"; ".join(noter)}' if noter else ''))

        if disjunkte:
            feedback.setProgress(60)
            feedback.setProgressText('sporer oplandene')
            celler = np.array([c for _, c, *_ in gyldige], dtype=np.int64)
            etiketter = np.arange(1, n + 1, dtype=np.int32)
            lab = graf.opstroems(celler, etiketter=etiketter)
            for i, (_, c, *_r) in enumerate(gyldige, start=1):
                lab[c] = i                 # startcellen hører altid til sit punkt
            celletal = {v: int((lab == v).sum()) for v in range(1, n + 1)}
            celletal = {v: t for v, t in celletal.items() if t > 0}
            raster = derived / '03_oplande_punkter.tif'
            oplande._skriv_raster(lab, graf, raster, gdal.GDT_Int32)
            feedback.setProgress(80)
            feedback.setProgressText('vektoriserer oplandene')
            dele = oplande._polygoniser(raster, derived / '03_oplande_punkter.gpkg',
                                        graf, celletal, log,
                                        'oplandene til punkterne')
            for i, (ident_vaerdi, c, flyttet, fundet_ha, x, y) in enumerate(
                    gyldige, start=1):
                geom = dele.get(i)
                if geom is None:
                    feedback.pushWarning(
                        f'Punkt {i} ({ident_vaerdi}): tomt opland — ligger et andet '
                        'punkt lige nedstrøms? Springes over.')
                    continue
                skriv(i, ident_vaerdi, geom, (lab == i), flyttet, fundet_ha, x, y)
        else:
            for i, (ident_vaerdi, c, flyttet, fundet_ha, x, y) in enumerate(
                    gyldige, start=1):
                if feedback.isCanceled():
                    return {}
                feedback.setProgress(50 + i / n * 45)
                feedback.setProgressText(f'opland {i}/{n}')
                sporet = graf.opstroems(np.array([c], dtype=np.int64))
                maske = sporet > 0
                maske[c] = True
                if not maske.any():
                    feedback.pushWarning(
                        f'Punkt {i} ({ident_vaerdi}): ingen celler sporet — '
                        'springes over.')
                    continue
                raster = derived / f'03_opland_punkt_{i:04d}.tif'
                gpkg = derived / f'03_opland_punkt_{i:04d}.gpkg'
                oplande._skriv_raster(maske.astype(np.int32), graf, raster,
                                      gdal.GDT_Int32)
                dele = oplande._polygoniser(raster, gpkg, graf,
                                            {1: int(maske.sum())}, log,
                                            f'oplandet til punkt {i}')
                geom = dele.get(1)
                if geom is None:
                    feedback.pushWarning(
                        f'Punkt {i} ({ident_vaerdi}): oplandet kunne ikke '
                        'vektoriseres — springes over.')
                else:
                    skriv(i, ident_vaerdi, geom, maske, flyttet, fundet_ha, x, y)
                # Ét sæt mellemfiler pr. punkt ville fylde arbejdsmappen med
                # hundreder af filer — de er kun brugt her og ryddes med det samme.
                for p in (raster, gpkg):
                    try:
                        p.unlink()
                    except OSError:
                        pass

        del graf
        feedback.setProgress(97)
        return {self.OPLAND: ident}

    # Hvor lille et vandløb må være for stadig at tælle med, målt mod det
    # kraftigste inden for søgeradius. En grøft med 0,01 ha opstrøms skal ikke
    # vinde over åen ved siden af, bare fordi den ligger ti meter nærmere.
    ANDEL_AF_STOERSTE = 0.5

    @staticmethod
    def _snap(graf, akkumulering_sti, celle, radius_m):
        """Flytter punktet hen på nærmeste punkt af det dominerende vandløb.

        To fælder skal undgås på én gang:

        Vælger man cellen med STØRST opstrøms areal, ligger den altid i
        radiussens nedstrøms kant, og svaret rykker et helt radiusspring ned ad
        åen — et opland der hører til et andet sted end det, brugeren prikkede på.

        Vælger man den NÆRMESTE celle i vandløbsnettet, vinder den første den
        bedste markgrøft. Nettet er udvidet op langs de kortlagte vandløb, så det
        indeholder masser af celler med nul opstrøms.

        Derfor: find det kraftigste inden for radius, behold kun det der er
        mindst halvt så stort, og tag det nærmeste af dem.
        Returnerer (celle, flyttet i meter, opstrøms ha på den valgte).
        """
        import numpy as np
        from osgeo import gdal

        raekke, kol = divmod(int(celle), graf.bredde)
        ds = gdal.Open(str(akkumulering_sti))
        akk = ds.GetRasterBand(1).ReadAsArray()
        ds = None
        i_ha = graf.celleareal / 1e4

        radius = int(round(radius_m / graf.opl))
        if radius <= 0:
            return celle, 0.0, float(akk[raekke, kol]) * i_ha

        r0 = max(0, raekke - radius)
        r1 = min(graf.hoejde, raekke + radius + 1)
        k0 = max(0, kol - radius)
        k1 = min(graf.bredde, kol + radius + 1)
        udsnit = akk[r0:r1, k0:k1]
        if udsnit.size == 0:
            return celle, 0.0, float(akk[raekke, kol]) * i_ha

        maks = float(udsnit.max())
        if maks <= 0:
            return celle, 0.0, 0.0
        kandidat = udsnit >= maks * UdpegOpland.ANDEL_AF_STOERSTE
        raekker, kolonner = np.nonzero(kandidat)
        afstande = ((raekker + r0 - raekke) ** 2 + (kolonner + k0 - kol) ** 2)
        naermest = np.flatnonzero(afstande == afstande.min())
        # Ved lige afstand vinder den kraftigste — ellers kunne et sidetilløb
        # slå hovedløbet.
        bedst = naermest[int(np.argmax(udsnit[raekker[naermest],
                                              kolonner[naermest]]))]
        ny_raekke = int(raekker[bedst]) + r0
        ny_kol = int(kolonner[bedst]) + k0
        fundet_ha = float(akk[ny_raekke, ny_kol]) * i_ha
        if (ny_raekke, ny_kol) == (raekke, kol):
            return celle, 0.0, fundet_ha
        afstand = float(afstande[bedst]) ** 0.5 * graf.opl
        return ny_raekke * graf.bredde + ny_kol, afstand, fundet_ha

    @staticmethod
    def _kant_celler(maske, graf):
        """Antal oplandsceller på selve rasterkanten. >0 = oplandet er skåret over."""
        flad = maske.reshape(graf.hoejde, graf.bredde)
        return int(flad[0, :].sum() + flad[-1, :].sum()
                   + flad[:, 0].sum() + flad[:, -1].sum())

    def _advar_om_kanten(self, maske, graf, feedback):
        """Rører oplandet rasterkanten, er det skåret over."""
        paa_kanten = self._kant_celler(maske, graf)
        if paa_kanten:
            feedback.pushWarning(
                f'ADVARSEL: oplandet rører terrænets kant i {paa_kanten:,} celler — '
                'det er skåret over, og arealet er for lille. Hæv "Terræn hentes så '
                'langt uden om området" under Avanceret, eller brug et område der '
                'ligger inden for det præberegnede grundlag.')

    # ── grundlag og terræn ──────────────────────────────────────────────────

    def _find_flise(self, om, parameters, context, feedback, projekt, noegle):
        """Den præberegnede flise der dækker området, lokalt eller online."""
        sti = self.parameterAsString(parameters, self.GRUNDLAG, context)
        if not sti:
            return None, None
        bibliotek = Path(sti)
        bibliotek.mkdir(parents=True, exist_ok=True)

        geom = om.omraade_geometri(projekt)
        if geom is None:
            raise QgsProcessingException('Området har ingen gyldig geometri.')

        feedback.pushInfo(f'Søger præberegnet grundlag i {bibliotek} …')
        fund = om.find_flise(bibliotek, geom, noegle, feedback)
        if not fund:
            url = self.parameterAsString(parameters, self.GRUNDLAG_URL, context)
            if url and url.strip().lower() not in ('ingen', 'nej', 'off'):
                indeks = om.hent_indeks(url, bibliotek, feedback)
                post = om.find_flise_online(indeks, geom, noegle, feedback)
                if post is not None:
                    if om.hent_flise_online(url, post, bibliotek, feedback,
                                            feedback.isCanceled) is not None:
                        fund = om.find_flise(bibliotek, geom, noegle, feedback)
                elif indeks:
                    feedback.pushInfo(
                        '  ingen udgivet flise dækker området med de her parametre.')
        if not fund:
            om.meld(feedback, 'Intet præberegnet grundlag dækker området — '
                              'terrænet hentes og konditioneres.')
            return None, None

        flise, manifest = fund
        om.meld(feedback,
                f'Præberegnet grundlag brugt: {manifest.get("navn") or flise.name} '
                f'({manifest.get("areal_km2")} km², beregnet '
                f'{str(manifest.get("beregnet"))[:10]}). '
                'Terrænet blev ikke hentet eller konditioneret.')
        return flise, f'præberegnet flise {flise.name}'

    def _skaf_terraen(self, om, parameters, context, feedback, stier, projekt):
        """Henter DHM/Terræn for området plus buffer."""
        from . import dhm_wcs

        geom = om.omraade_geometri(projekt)
        if geom is None:
            raise QgsProcessingException('Området har ingen gyldig geometri.')
        buffer_m = self.parameterAsDouble(parameters, self.BUFFER, context)
        kasse = geom.boundingBox()
        minx = kasse.xMinimum() - buffer_m
        miny = kasse.yMinimum() - buffer_m
        maxx = kasse.xMaximum() + buffer_m
        maxy = kasse.yMaximum() + buffer_m
        res = self.parameterAsDouble(parameters, self.OPLOESNING, context)

        ud = stier['dem_raa']
        if ud.exists():
            try:
                ud.unlink()
            except OSError as e:
                raise QgsProcessingException(
                    f'{ud} kunne ikke slettes: {e}. Ligger den åben i QGIS?')
        token = self.parameterAsString(parameters, self.TOKEN, context)
        dhm_wcs.hent(token, str(ud), minx, miny, maxx, maxy, res, feedback)

        lag = QgsRasterLayer(str(ud), 'DHM_raa')
        if not lag.isValid():
            raise QgsProcessingException(f'Terrænet kunne ikke indlæses: {ud}')
        epsg, maal_crs = om.epsg_af_dem(lag, feedback)
        return om.dem_som_fil(lag, feedback), epsg, maal_crs

    def _arbejdsmappe(self, parameters, context):
        valgt = self.parameterAsString(parameters, self.ARBEJDSMAPPE, context)
        if valgt:
            sti = Path(valgt)
        else:
            sti = Path(tempfile.gettempdir()) / 'udpeg_opland'
        sti.mkdir(parents=True, exist_ok=True)
        return sti

    # ── output ──────────────────────────────────────────────────────────────

    def _skriv_fra_leverance(self, parameters, context, feedback, gpkg, crs, kilde):
        """Kopierer de tre lag fra modellens leverance ud i Processing-lagene."""
        svar = {}
        for param, lagnavn, hvad in (
                (self.OPLAND, 'total_opland', 'oplandet'),
                (self.VANDLOEBSOPLANDE, 'vandloebsoplande', 'vandløbsoplandene'),
                (self.DIREKTE_OPLAND, 'direkte_opland', 'det direkte opland')):
            kilde_lag = QgsVectorLayer(f'{gpkg}|layername={lagnavn}', lagnavn, 'ogr')
            if not kilde_lag.isValid():
                if param == self.OPLAND:
                    raise QgsProcessingException(
                        f'{hvad} blev ikke skrevet af modellen ({gpkg}, lag '
                        f'{lagnavn}).')
                continue
            felter = kilde_lag.fields()
            sink, ident = self.parameterAsSink(
                parameters, param, context, felter, QgsWkbTypes.MultiPolygon,
                kilde_lag.crs() if kilde_lag.crs().isValid() else crs)
            if sink is None:
                continue
            n = 0
            for f in kilde_lag.getFeatures():
                ny = QgsFeature(felter)
                g = QgsGeometry(f.geometry())
                g.convertToMultiType()
                ny.setGeometry(g)
                ny.setAttributes(f.attributes())
                sink.addFeature(ny)
                n += 1
            feedback.pushInfo(f'  {hvad}: {n} objekt(er)')
            svar[param] = ident
            del kilde_lag
        return svar
