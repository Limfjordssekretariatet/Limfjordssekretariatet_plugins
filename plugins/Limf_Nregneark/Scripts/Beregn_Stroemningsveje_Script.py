"""Beregn strømningsveje — henter terrænet, konditionerer det og udleder vandløbsnettet.

Dette er trin 0-2 i oplandsmodellen (Scripts/oplande.py), altså samme beregning som
"Beregn oplande" bygger videre på:

  Trin 0  Terrænet hentes fra Dataforsyningen, resamples til analyseopløsningen og
          vandflader vippes umærkeligt mod deres udløb (helt flade flader kvæler
          breaching; maskering ville fjerne landskabets udløb).
  Trin 1  DHM/Hydros tilpasningslinjer påtrykker deres koter — START_Z/END_Z
          ERSTATTER terrænet, så broer og rørunderføringer ikke bliver kunstige
          vandskel. Derefter brændes det kortlagte vandløbsnetværk ind med
          påtvunget monotont fald, så beregnede vandløb ikke vandrer væk fra de
          faktiske i fladt terræn. Til sidst breaching og fill — i den rækkefølge:
          fyldes der først, er der ingen lavninger tilbage at grave igennem, og
          resultatet bliver identisk med ren fill, som i dansk lavland gør
          oplandsgrænserne til algoritmeartefakter.
  Trin 2  D8-strømningsretning, akkumulering i celler, og vandløbsnetværket udledt
          ved akkumuleringstærsklen.

Output i Outputfiler_<omraade>:

    DHM_raa.tif           det RÅ downloadede terræn (uden konditionering)
    Hoejdemodel.tif       det konditionerede terræn — strømningsvejene er beregnet på det
    Channel_Networks.gpkg de beregnede strømningsveje, med Strahler-orden i feltet ORDER

Mellemresultaterne i Outputfiler_<omraade>/Oplandsmodel/mellemresultater/ er de samme
som "Beregn oplande" bruger, og de genbruges af det trin i stedet for at blive lavet
forfra — hele konfigurationen bygges i oplandsmodel.py, netop for at de to trin ikke
kan komme til at regne på hver sit grundlag. Ændrer du en parameter her, laver
oplandstrinnet konditioneringen om, og det er som det skal være.

Erstatter den tidligere kæde (terraen_model.model3): grov 3 m brænding af
vandløbspolygoner, cosinus-rende langs DHMLinje, sagang:fillsinkswangliu og
sagang:channelnetworkanddrainagebasins. Modelfilen ligger stadig i Modeller/ som
reference, men interfacet kalder dette script.

Kræver plugin'et "Whitebox Workflows for QGIS" (Udvidelser > Hent flere).
"""

import datetime as dt
import os
import sys
from pathlib import Path

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingOutputRasterLayer,
    QgsProcessingOutputVectorLayer,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
import processing

# Sørg for at scriptets egen mappe er på sys.path, så modulerne kan importeres
# uanset hvordan QGIS' script-provider loader filen.
import os as _os, sys as _sys
try:
    _d = _os.path.dirname(_os.path.abspath(__file__))
    if _d and _d not in _sys.path:
        _sys.path.insert(0, _d)
except NameError:
    pass


def _fælles():
    """Indlæser oplandsmodel.py fra DENNE plugin-kopi.

    Samme begrundelse som i oplandsmodel.indlaes_oplande(): et almindeligt import
    ville kunne ramme en anden kopi af pluginnet, som ligger først i Processings
    SCRIPTS_FOLDERS.
    """
    import importlib.util

    rod = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    navn = '_oplandsmodel_' + os.path.basename(rod).lower()
    if navn in _sys.modules:
        return _sys.modules[navn]
    sti = os.path.join(rod, 'Scripts', 'oplandsmodel.py')
    spec = importlib.util.spec_from_file_location(navn, sti)
    modul = importlib.util.module_from_spec(spec)
    _sys.modules[navn] = modul
    spec.loader.exec_module(modul)
    return modul


om = _fælles()


class BeregnStroemningsveje(QgsProcessingAlgorithm):

    PROJEKTOMRAADE = 'PROJEKTOMRAADE'
    TOKEN = 'TOKEN'
    DHM = 'DHM'
    VANDLOEB = 'VANDLOEB'
    TILPASNINGER = 'TILPASNINGER'
    OPLOESNING = 'OPLOESNING'
    STROEM_TAERSKEL = 'STROEM_TAERSKEL'
    BURN = 'BURN'
    BURN_DYBDE = 'BURN_DYBDE'
    BREACH_DIST = 'BREACH_DIST'
    DOWNLOAD_BUFFER = 'DOWNLOAD_BUFFER'
    HENT_IGEN = 'HENT_IGEN'
    GRUNDLAG = 'GRUNDLAG'
    GRUNDLAG_URL = 'GRUNDLAG_URL'

    OUT_DHM_RAA = 'DHM_RAA'
    OUT_HOEJDEMODEL = 'HOEJDEMODEL'
    OUT_CHANNEL = 'CHANNEL_NETWORKS'

    def name(self):
        return 'beregn_stroemningsveje'

    def displayName(self):
        return 'Beregn strømningsveje (konditionering + vandløbsnet)'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return BeregnStroemningsveje()

    def shortHelpString(self):
        return (
            '<p>Henter terrænmodellen for projektområdets opland, konditionerer den '
            'hydrologisk og udleder strømningsretning, akkumulering og '
            'vandløbsnetværk med Whitebox.</p>'
            '<p>Konditioneringen er det der afgør om oplandene bliver rigtige: '
            '<b>DHM/Hydros tilpasningskoter</b> påtrykkes, så broer og '
            'rørunderføringer ikke bliver kunstige vandskel; det <b>kortlagte '
            'vandløbsnetværk brændes ind med monotont fald</b>, så de beregnede '
            'vandløb følger de faktiske; og der <b>breaches før der fyldes</b>, så '
            'lavningerne graves igennem i stedet for at blive plateauer med '
            'vilkårlig strømningsretning.</p>'
            '<p>Skriver <code>DHM_raa.tif</code> (råt terræn), '
            '<code>Hoejdemodel.tif</code> (konditioneret) og '
            '<code>Channel_Networks.gpkg</code> (beregnede strømningsveje med '
            'Strahler-orden i feltet <code>ORDER</code>) i '
            'Outputfiler_&lt;omraade&gt;. "Beregn oplande" genbruger '
            'mellemresultaterne, så kør det trin bagefter.</p>'
            '<p>Kræver plugin\'et <b>Whitebox Workflows for QGIS</b> og et '
            'Dataforsyningen-token, medmindre du selv peger på en terrænmodel.</p>'
        )

    # ── parametre ────────────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        def avanceret(p):
            p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
            return p

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.PROJEKTOMRAADE, 'Projektområde',
            types=[QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterString(
            self.TOKEN, 'Dataforsyningen-token (kun til download)',
            optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DHM, 'Terrænmodel — valgfri; angives den, hentes der ikke',
            optional=True))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.VANDLOEB, 'Kortlagt vandløbsnetværk (linjer)',
            types=[QgsProcessing.TypeVectorLine],
            defaultValue=om.vandloeb_standard(), optional=True))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.TILPASNINGER, 'Hydrologiske tilpasninger, DHM/Hydro (linjer)',
            types=[QgsProcessing.TypeVectorLine],
            defaultValue=om.tilpasninger_standard(), optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.OPLOESNING, 'Opløsning for download og analyse (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=om.STD_OPLOESNING, minValue=0.1))

        # ── avanceret ────────────────────────────────────────────────────────
        # Standardværdierne er de samme konstanter som "Beregn oplande" bruger.
        # Afviger de to trin, konditioneres terrænet forfra i oplandstrinnet.
        self.addParameter(avanceret(QgsProcessingParameterNumber(
            self.STROEM_TAERSKEL,
            'Akkumuleringstærskel for beregnet vandløb (ha)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=om.STD_STROEM_TAERSKEL, minValue=0.01)))
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
            self.DOWNLOAD_BUFFER, 'Buffer om projektområdet ved download (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=om.STD_DOWNLOAD_BUFFER, minValue=0.0)))
        self.addParameter(avanceret(QgsProcessingParameterBoolean(
            self.HENT_IGEN, 'Hent terrænet igen, selv om DHM_raa.tif findes',
            defaultValue=False)))
        # Adressen på det udgivne grundlag. Findes flisen ikke lokalt, hentes den
        # herfra — kun den ene, og kun hvis den passer til parametrene.
        self.addParameter(avanceret(QgsProcessingParameterString(
            self.GRUNDLAG_URL, 'Adresse på udgivet oplandsgrundlag',
            defaultValue=om.grundlag_url() or '', optional=True)))

        # Biblioteket med præberegnede oplande. Dækker en flise projektområdet, og
        # er den lavet med de samme parametre, springes hele konditioneringen over.
        std_bibliotek = om.grundlag_mappe()
        self.addParameter(avanceret(QgsProcessingParameterFile(
            self.GRUNDLAG, 'Bibliotek med præberegnet oplandsgrundlag',
            behavior=QgsProcessingParameterFile.Folder, optional=True,
            defaultValue=str(std_bibliotek) if std_bibliotek else None)))

        # Stierne bestemmes af Outputfiler_<omraade>, ikke af dialogen — de øvrige
        # trin slår filerne op på faste navne. Derfor outputs, ikke destinationer.
        self.addOutput(QgsProcessingOutputRasterLayer(
            self.OUT_DHM_RAA, 'DHM_raa.tif (råt terræn)'))
        self.addOutput(QgsProcessingOutputRasterLayer(
            self.OUT_HOEJDEMODEL, 'Hoejdemodel.tif (konditioneret)'))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.OUT_CHANNEL, 'Channel_Networks.gpkg (strømningsveje)'))

    # ── kørsel ───────────────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context: QgsProcessingContext,
                         feedback: QgsProcessingFeedback):
        om.tjek_forudsaetninger(feedback)
        self._flise_channel_networks = None
        oplande = om.indlaes_oplande()
        ud_mappe = om.output_mappe()
        stier = om.arbejdsstier(ud_mappe)
        # Trinnet laegger selv sine lag i lagpanelet, saa ved anden koersel holder
        # de filerne aabne. Paa Windows fejler skrivningen saa — eller filen bliver
        # skrevet halvt.
        om.frigiv_filer([stier['dem_raa'], stier['hoejdemodel'],
                         stier['channel_networks']], feedback)

        # ── 1) hvilket koordinatsystem regner vi i? ──────────────────────────
        # Terrænet hentes IKKE her. Findes der en præberegnet flise, skal der ikke
        # hentes noget overhovedet, og opslaget kræver kun projektområdet.
        dem_lag = self.parameterAsRasterLayer(parameters, self.DHM, context)
        dem_sti = None
        if dem_lag is not None:
            dem_sti = om.dem_som_fil(dem_lag, feedback)
            epsg, maal_crs = om.epsg_af_dem(dem_lag, feedback)
        else:
            epsg, maal_crs = om.arbejds_crs(ud_mappe, feedback)
        feedback.setProgress(10)

        # ── 2) vektorinput i det koordinatsystem ─────────────────────────────
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
            om._hentet_datasaet('DHMLinje', 'DHMLinje.shp')
            _gd = sys.modules.get('_grunddata_' + os.path.basename(
                om.PLUGIN_ROOT).lower())
            if _gd is not None:
                _gd.sikr('DHMLinje', feedback=feedback)
        except Exception as e:
            feedback.pushInfo(f'DHMLinje kunne ikke hentes: {e!r}')

        tilpasninger = om.materialiser(self, parameters, self.TILPASNINGER, context,
                                       feedback, maal_crs, stier['arbejdsmappe'],
                                       standard=om.tilpasninger_standard())
        if vandloeb is None:
            feedback.pushWarning(
                'Intet kortlagt vandløbsnetværk: ingen stream burning. I fladt '
                'terræn vandrer de beregnede vandløb væk fra de faktiske, og '
                'oplandsgrænserne følger med.')
        if tilpasninger is None:
            feedback.pushWarning(
                'Ingen hydrologiske tilpasninger (DHMLinje): hver vejdæmning og bane '
                'bliver et ubrudt vandskel, fordi laserscanningen ser dæmningen men '
                'ikke røret under den. Oplandene bliver systematisk for små.')

        # ── 3) konfiguration — samme kilde som oplandstrinnet ────────────────
        def byg_konf(dem):
            return om.byg_konfiguration(
                epsg=epsg, dem_sti=dem, projekt=projekt, vandloeb=vandloeb,
                tilpasninger=tilpasninger, stier=stier,
                oploesning=self.parameterAsDouble(parameters, self.OPLOESNING, context),
                stroem_taerskel_ha=self.parameterAsDouble(
                    parameters, self.STROEM_TAERSKEL, context),
                burn=self.parameterAsBool(parameters, self.BURN, context),
                burn_dybde=self.parameterAsDouble(parameters, self.BURN_DYBDE, context),
                breach_dist=self.parameterAsInt(parameters, self.BREACH_DIST, context))

        konf = byg_konf(dem_sti or "(afgoeres nedenfor)")
        noegle = om.konditioneringsnoegle(konf)

        # ── 3b) er konditioneringen lavet i forvejen? ────────────────────────
        # Nøglen dækker præcis det der bestemmer trin 0-2, og INTET om hvor
        # projektområdet ligger. Derfor kan biblioteket slås op før terrænet er
        # hentet — findes der en dækkende flise, skal der ikke hentes noget.
        flise = kilde = None
        bibliotek = self._bibliotek(parameters, context)
        if bibliotek is not None:
            omraade_geom = om.omraade_geometri(projekt)
            if omraade_geom is None:
                raise QgsProcessingException(
                    'Projektområdet har ingen gyldig geometri.')
            feedback.pushInfo(f'Søger præberegnet grundlag i {bibliotek} …')
            fund = om.find_flise(bibliotek, omraade_geom, noegle, feedback)
            if not fund:
                # Ikke lokalt — er der et udgivet grundlag online? Fortegnelsen er
                # smaa kilobyte, og der hentes kun den ene flise der daekker
                # projektomraadet og er beregnet med de samme parametre.
                url = self.parameterAsString(parameters, self.GRUNDLAG_URL, context)                     or om.grundlag_url()
                if url:
                    indeks = om.hent_indeks(url, bibliotek, feedback)
                    post = om.find_flise_online(indeks, omraade_geom, noegle, feedback)
                    if post is not None:
                        hentet = om.hent_flise_online(
                            url, post, bibliotek, feedback, feedback.isCanceled)
                        if hentet is not None:
                            fund = om.find_flise(bibliotek, omraade_geom, noegle,
                                                 feedback)
                    elif indeks:
                        feedback.pushInfo(
                            '  ingen udgivet flise daekker projektomraadet med de '
                            'her parametre.')
            if fund:
                flise, manifest = fund
                kilde = f'praeberegnet flise {flise.name}'
                dem_sti = flise / '01_dem_hydro.tif'
                epsg = int(manifest.get('epsg', epsg))
                om.meld(feedback,
                        f'Præberegnet grundlag brugt: '
                        f'{manifest.get("navn") or flise.name} '
                        f'({manifest.get("areal_km2")} km², beregnet '
                        f'{str(manifest.get("beregnet"))[:10]}). '
                        'Terrænet blev ikke hentet eller konditioneret.')
            else:
                om.meld(feedback, 'Intet præberegnet grundlag dækker '
                        'projektområdet — terrænet hentes og konditioneres lokalt.')

        koersel_id = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
        log = oplande.Log(stier['log'] / f'{koersel_id}.log', koersel_id,
                          ekstra=feedback.pushInfo)

        # ── 4) trin 0-2: konditionering og strømning ─────────────────────────
        try:
            oplande.registrer_whitebox(log)
            om.vaelg_fill(oplande, konf, log)
            om.haardfoer_whitebox(oplande, log)

            if flise is not None:
                om.hent_flise(flise, stier['derived'], feedback, log)
                # Linjelaget ligger i flisen fra præberegningen — det er lavet paa
                # nøjagtig det samme raster, saa der er intet at genberegne.
                fra_flise = flise / om.CHANNEL_NETWORKS
                self._flise_channel_networks = fra_flise if fra_flise.is_file() else None
                konf = byg_konf(dem_sti)
                om.gem_konfiguration(konf, stier['konf_fil'])
                oplande.AKTIV_KONFIG = stier['konf_fil']
                oplande.gem_parameterlog(konf, stier['log'], koersel_id)
                dem_hydro = stier['derived'] / '01_dem_hydro.tif'
                stroem = {'pointer': stier['derived'] / '02_d8_pointer.tif',
                          'akkumulering': stier['derived'] / '02_d8_akkumulering.tif',
                          'streams': stier['derived'] / '02_streams.tif'}
                feedback.setProgress(70)
            else:
                # Intet præberegnet grundlag: skaf terrænet og regn trin 0-2.
                if dem_sti is None:
                    from qgis.core import QgsRasterLayer
                    projekt_lag = self.parameterAsVectorLayer(
                        parameters, self.PROJEKTOMRAADE, context)
                    if projekt_lag is None:
                        raise QgsProcessingException(
                            'Projektområde-laget kunne ikke indlæses.')
                    if not self._kan_genbruge_terraen(
                            parameters, context, feedback, stier, projekt_lag):
                        self._hent_terraen(parameters, context, feedback, stier,
                                           projekt_lag)
                    dem_lag = QgsRasterLayer(str(stier['dem_raa']), 'DHM_raa')
                    if not dem_lag.isValid():
                        raise QgsProcessingException(
                            f'Terrænmodellen kunne ikke indlæses: {stier["dem_raa"]}')
                    dem_sti = om.dem_som_fil(dem_lag, feedback)
                    epsg, maal_crs = om.epsg_af_dem(dem_lag, feedback)
                konf = byg_konf(dem_sti)
                om.gem_konfiguration(konf, stier['konf_fil'])
                oplande.AKTIV_KONFIG = stier['konf_fil']
                oplande.gem_parameterlog(konf, stier['log'], koersel_id)

                feedback.setProgress(20)
                feedback.setProgressText('klargøring af terrænet')
                dem_analyse = oplande.trin0_klargoer(konf, log)
                if feedback.isCanceled():
                    return {}

                feedback.setProgress(35)
                feedback.setProgressText('hydrologisk konditionering')
                for _fravalg in om.undgaa_tomme_input(konf, dem_analyse, log):
                    feedback.pushInfo(om.NOEGLETAL + _fravalg)
                dem_hydro = oplande.trin1_konditioner(konf, dem_analyse, log)
                if feedback.isCanceled():
                    return {}

                feedback.setProgress(70)
                feedback.setProgressText('strømningsretning og -akkumulering')
                stroem = oplande.trin2_stroemning(konf, dem_hydro, log)
                if feedback.isCanceled():
                    return {}

                # Akkumuleringstærsklen stopper nettet i toppen af hvert forløb,
                # hvor der er under tærsklen opstrøms. De kortlagte vandløb rækker
                # længere, og så mangler der stumper. Her spores der nedstrøms fra
                # de steder hvor der er hul.
                if vandloeb is not None:
                    feedback.setProgressText('udvider nettet til de kortlagte vandløb')
                    om.udvid_stroemme_til_kortlagte(
                        oplande, stroem, dem_hydro, vandloeb[0], vandloeb[1], log)

            # Mærket fortæller oplandstrinnet at trin 0-2 passer til DE HER
            # parametre. Uden det måtte det gætte ud fra tidsstempler, og kopierede
            # rastere fra biblioteket er ældre end konfigurationsfilen selv om de
            # er fuldstændig gyldige.
            om.skriv_maerke(stier['arbejdsmappe'], noegle,
                            kilde or 'beregnet lokalt',
                            {'koersel_id': koersel_id})

            feedback.setProgress(80)
            feedback.setProgressText('kontrol mod det kortlagte netværk')
            traef = self._traefprocent(om, stroem, vandloeb, log, feedback)

            feedback.setProgress(85)
            feedback.setProgressText('vandløbsnet med Strahler-orden')
            antal, ordener = self._skriv_channel_networks(
                oplande, konf, stier, stroem, koersel_id, epsg, maal_crs, log,
                feedback)
            self._skriv_hoejdemodel(dem_hydro, stier, feedback)
        except oplande.OplandsFejl as e:
            log.skriv(f'FEJL: {e}')
            raise QgsProcessingException(str(e))
        finally:
            log.luk()

        # ── 5) læs lagene ind, som det gamle trin gjorde ─────────────────────
        for sti, lagnavn in ((stier['hoejdemodel'], 'Hoejdemodel'),
                             (stier['channel_networks'], 'Channel_Networks')):
            if sti.is_file():
                detaljer = QgsProcessingContext.LayerDetails(
                    lagnavn, QgsProject.instance(), lagnavn)
                context.addLayerToLoadOnCompletion(str(sti), detaljer)

        # Den gamle kæde gemte Hoejdemodel i SAGA-format. Ligger filen der endnu,
        # er den fra den gamle model og bliver ikke længere brugt til noget.
        gammel = [p for p in (ud_mappe / 'Hoejdemodel.sdat',
                              ud_mappe / 'Hoejdemodel.sgrd') if p.is_file()]
        if gammel:
            feedback.pushWarning(
                'Disse filer er fra den gamle strømningsvejs-model og bruges ikke '
                'længere: ' + ', '.join(p.name for p in gammel)
                + '. Hoejdemodel.tif er den nye. Slet de gamle når du har '
                  'kontrolleret resultatet.')

        feedback.pushInfo(
            f'Strømningsveje: {antal} vandløbsstrækninger, Strahler-orden '
            + (', '.join(f'{o}: {n}' for o, n in sorted(ordener.items())) or 'ingen'))
        if traef:
            om.meld(feedback,
                    f'Træfprocent mod de kortlagte vandløb: {traef["pct"]:.1f} % '
                    f'(mål: 90 %)')
        feedback.setProgress(100)
        return {
            self.OUT_DHM_RAA: (str(stier['dem_raa'])
                               if stier['dem_raa'].is_file() else ''),
            self.OUT_HOEJDEMODEL: str(stier['hoejdemodel']),
            self.OUT_CHANNEL: str(stier['channel_networks']),
        }

    # ── delTrin ──────────────────────────────────────────────────────────────

    def _bibliotek(self, parameters, context):
        """Biblioteket med præberegnede fliser — fra dialogen eller indstillingen."""
        valgt = self.parameterAsString(parameters, self.GRUNDLAG, context)
        if valgt:
            sti = Path(valgt)
            sti.mkdir(parents=True, exist_ok=True)
            return sti
        # Uden en valgt mappe bruges den under QGIS-profilen — ét bibliotek pr.
        # maskine, saa en flise kun hentes én gang og deles af alle projekter.
        skal_hente = bool(self.parameterAsString(parameters, self.GRUNDLAG_URL,
                                                 context) or om.grundlag_url())
        return om.grundlag_mappe(opret=skal_hente)

    @staticmethod
    def _har_valgt_hoejdemodel():
        """Har brugeren udpeget en lokal højdemodel for det aktive projektområde?

        Samme opslag som downloadscriptet selv laver — her bruges det kun til at
        afgøre om et manglende token er et problem.
        """
        from qgis.core import QgsSettings

        s = QgsSettings()
        omraade = s.value('vaadomraade_modeller/projektomraade_navn', '')
        if not omraade:
            return False
        sti = s.value(f'vaadomraade_modeller/valgt_hoejdemodel/{omraade}', '')
        return bool(sti) and os.path.isfile(sti)

    def _kan_genbruge_terraen(self, parameters, context, feedback, stier, projekt_lag):
        """Kan et tidligere hentet DHM_raa.tif bruges igen?

        Downloadet fylder hundreder af MB og tager minutter, mens en justering af fx
        breaching-afstanden ikke ændrer terrænet. Men den gemte model SKAL dække
        projektområdet og være nyere end det — ellers regner man videre på terræn fra
        et andet område uden at noget siger fra. Ved tvivl hentes der igen.
        """
        from qgis.core import (QgsCoordinateTransform, QgsProject, QgsRasterLayer)

        dem = stier['dem_raa']
        if self.parameterAsBool(parameters, self.HENT_IGEN, context):
            feedback.pushInfo('Der er bedt om at hente terrænet igen.')
            return False
        if not dem.is_file():
            return False

        kilde = projekt_lag.source().split('|', 1)[0]
        if os.path.isfile(kilde) and os.path.getmtime(kilde) > dem.stat().st_mtime:
            feedback.pushInfo('Projektområdet er ændret siden terrænet blev hentet — '
                              'henter igen.')
            return False

        lag = QgsRasterLayer(str(dem), 'DHM_raa')
        if not lag.isValid():
            return False
        omraade = projekt_lag.extent()
        if (projekt_lag.crs().isValid() and lag.crs().isValid()
                and projekt_lag.crs() != lag.crs()):
            omraade = QgsCoordinateTransform(
                projekt_lag.crs(), lag.crs(),
                QgsProject.instance()).transformBoundingBox(omraade)
        if not lag.extent().contains(omraade):
            feedback.pushInfo('Det gemte terræn dækker ikke projektområdet — '
                              'henter igen.')
            return False

        feedback.pushInfo(
            f'Genbruger terrænet fra sidste kørsel: {dem}. Sæt "Hent terrænet igen" '
            'hvis det skal hentes forfra.')
        return True

    def _hent_terraen(self, parameters, context, feedback, stier, projekt_lag):
        """Henter terrænet via 'Hent DHM (WCS)' — uden brænding, så det er råt.

        Downloadscriptet beholdes uændret som datakilde: det håndterer token,
        server-grænser (tiling og genprøvning), afgrænsning til de oplande
        projektområdet rører, og "Brug lokal højdemodel"-valget fra interfacet.
        Kun brændingen springes over — den hører til den gamle model, og
        oplandsmodellen konditionerer selv.
        """
        buffer_m = self.parameterAsDouble(parameters, self.DOWNLOAD_BUFFER, context)

        # Downloaden kræver et token, medmindre brugeren har udpeget en lokal
        # højdemodel. Uden tjekket her fejler det nede i downloadscriptet med
        # "Incorrect parameter value for TOKEN", som ikke fortæller nogen noget.
        token = self.parameterAsString(parameters, self.TOKEN, context)
        if not token and not self._har_valgt_hoejdemodel():
            raise QgsProcessingException(
                'Der er hverken et Dataforsyningen-token, en udpeget lokal '
                'højdemodel eller et præberegnet grundlag der dækker '
                'projektområdet — så er der ingen måde at skaffe terrænet på.'
                + chr(10) + chr(10) +
                'Kør trinnet fra interfacet (det spørger om token), vælg "Brug lokal '
                'højdemodel", eller peg på et bibliotek med præberegnet '
                'oplandsgrundlag under Avanceret.')

        feedback.pushInfo(f'Henter terrænmodel for projektområdet + {buffer_m:.0f} m …')
        buffer_resultat = processing.run('native:buffer', {
            'INPUT': projekt_lag,
            'DISTANCE': buffer_m,
            'SEGMENTS': 5,
            'END_CAP_STYLE': 0,
            'JOIN_STYLE': 0,
            'MITER_LIMIT': 2,
            'DISSOLVE': False,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        if stier['dem_raa'].exists():
            # GDAL kan ikke skrive til en fil der er åben i lagpanelet, og en halvt
            # overskrevet terrænmodel er værre end ingen.
            try:
                stier['dem_raa'].unlink()
            except OSError as e:
                raise QgsProcessingException(
                    f'{stier["dem_raa"]} kunne ikke slettes: {e}. Ligger den åben i '
                    'QGIS? Fjern laget og kør igen.')

        processing.run('script:hent_dhm_wcs', {
            'INPUT': buffer_resultat,
            'TOKEN': self.parameterAsString(parameters, self.TOKEN, context),
            'RESOLUTION': self.parameterAsDouble(parameters, self.OPLOESNING, context),
            # Brændingen hører til den gamle model — vi vil have råt terræn.
            'SPRING_BRAENDING_OVER': True,
            'OUTPUT': str(stier['dem_raa']),
        }, context=context, feedback=feedback, is_child_algorithm=True)

        if not stier['dem_raa'].is_file():
            raise QgsProcessingException(
                f'Terrænmodellen blev ikke hentet til {stier["dem_raa"]}.')

    def _traefprocent(self, om, stroem, vandloeb, log, feedback):
        """Falder modellens vandveje sammen med de kortlagte vandløb?

        Det er DEN kontrol der afgør om konditioneringen virkede. Ligger
        hovedstrømmen ved siden af de kortlagte forløb, er brændingen ikke slået
        igennem — og så bliver oplandsgrænserne forkerte uden at noget andet fejler.
        Kriteriet er bevidst ikke cirkulært, se oplandsmodel.traefprocent.

        Målingen må aldrig vælte kørslen: resultatet er skrevet, og en fejlet måling
        er en manglende oplysning, ikke et manglende resultat.
        """
        if vandloeb is None:
            return None
        try:
            traef = om.traefprocent(stroem['akkumulering'], vandloeb[0], vandloeb[1])
        except Exception as e:
            log.advar(f'traefprocenten kunne ikke maales: {e!r}')
            return None
        if traef is None:
            feedback.pushWarning(
                'Der er ingen kortlagte vandløb i analyseudstrækningen at holde de '
                'beregnede strømningsveje op imod. Dækker vandløbslaget området?')
            return None

        log.skriv(f'  ACCEPTTEST traefprocent: {traef["pct"]:.1f} % af '
                  f'{traef["knuder"]:,} knudepunkter paa det kortlagte netvaerk '
                  f'baerer hovedstroemmen (maal: 90 %)')
        daarligst = sorted(traef['pr_vandloeb'].items(),
                           key=lambda kv: kv[1][0] / max(kv[1][1], 1))[:5]
        for navn, (ramte, knuder, km) in daarligst:
            log.skriv(f'      {navn[:28]:<28s} {km:6.1f} km  '
                      f'{100.0 * ramte / knuder:5.1f} %')

        besked = (f'Træfprocent mod det kortlagte netværk: {traef["pct"]:.1f} % '
                  f'({traef["ramte"]:,} af {traef["knuder"]:,} knudepunkter bærer '
                  f'hovedstrømmen inden for {traef["soeg_m"]:.0f} m).')
        if traef['pct'] >= 90.0:
            feedback.pushInfo(besked + ' Målet er 90 % — BESTÅET.')
        elif traef['pct'] >= 75.0:
            feedback.pushWarning(
                besked + ' Målet er 90 %. Hovedstrømmen ligger typisk '
                f'{traef["median_afvigelse_m"]:.0f} m fra det kortlagte forløb dér '
                'hvor den afviger. Kontrollér Channel_Networks mod vandløbslaget '
                'før du bruger oplandene.')
        else:
            feedback.reportError(
                besked + ' Målet er 90 %. Så lavt betyder normalt at brændingen '
                'ikke er slået igennem — kontrollér at vandløbslaget dækker området '
                'og ligger i samme koordinatsystem som terrænmodellen. '
                'Oplandsgrænserne kan ikke bruges som de er.', fatalError=False)
        return traef

    @staticmethod
    def _skriv_hoejdemodel(dem_hydro: Path, stier, feedback):
        """Gemmer det konditionerede terræn som Hoejdemodel.tif.

        Det er DEN højdemodel strømningsvejene er beregnet på, så det er den der
        skal ligge i outputmappen — ikke en anden variant der ser ens ud.
        """
        from osgeo import gdal

        maal = stier['hoejdemodel']
        projekt = QgsProject.instance()
        # Et indlæst lag holder filen åben på Windows, og så fejler skrivningen.
        sti_norm = os.path.normpath(str(maal)).lower()
        for lag in list(projekt.mapLayers().values()):
            if os.path.normpath(lag.source().split('|', 1)[0]).lower() == sti_norm:
                projekt.removeMapLayer(lag.id())
        if maal.exists():
            try:
                maal.unlink()
            except OSError as e:
                raise QgsProcessingException(
                    f'{maal} kunne ikke overskrives: {e}. Ligger den åben i QGIS?')

        kilde = gdal.Open(str(dem_hydro))
        if kilde is None:
            raise QgsProcessingException(
                f'Det konditionerede terræn kunne ikke åbnes: {dem_hydro}')
        resultat = gdal.Translate(
            str(maal), kilde, format='GTiff',
            creationOptions=['COMPRESS=DEFLATE', 'TILED=YES', 'PREDICTOR=3'])
        kilde = None
        resultat = None
        if not maal.is_file():
            raise QgsProcessingException(f'Højdemodellen blev ikke gemt: {maal}')
        feedback.pushInfo(f'Konditioneret højdemodel gemt: {maal}')

    def _skriv_channel_networks(self, oplande, konf, stier, stroem, koersel_id,
                                epsg, crs, log, feedback):
        """Vektoriserer strømningsvejene — eller tager dem med fra det præberegnede grundlag.

        Ligger linjerne i flisen, er de lavet på præcis det samme raster, så der er
        intet at genberegne. Det er også dét lag man kan åbne og se på uden at have
        kørt et projekt igennem.
        """
        import shutil

        flise_lag = getattr(self, '_flise_channel_networks', None)
        if flise_lag is not None and Path(flise_lag).is_file():
            om.frigiv_filer([stier['channel_networks']], feedback)
            shutil.copy2(str(flise_lag), str(stier['channel_networks']))
            lag = QgsVectorLayer(
                f"{stier['channel_networks']}|layername=Channel_Networks",
                'Channel_Networks', 'ogr')
            ordener = {}
            for f in lag.getFeatures():
                ordener[f['ORDER']] = ordener.get(f['ORDER'], 0) + 1
            log.skriv(f'  Channel_Networks taget fra det praeberegnede grundlag: '
                      f'{lag.featureCount()} straekninger')
            return lag.featureCount(), ordener

        # Et projektomraade paa en smal kyststrimmel kan ligge helt uden kortlagte
        # vandloeb og uden en eneste celle over taersklen. Det er sjaeldent, men det
        # er ikke en fejl — stroemningsretning og -akkumulering er beregnet, og
        # oplandene kan sagtens spores. At stoppe koerslen ville efterlade brugeren
        # uden noget som helst.
        antal, ordener = om.skriv_channel_networks(
            oplande, konf, stier['derived'], stier['channel_networks'], stroem,
            koersel_id, crs, log, feedback, tillad_tomt=True)
        if not antal:
            feedback.pushWarning(
                'Der blev ikke fundet en eneste strømningsvej i området. Er der '
                'kortlagte vandløb her, gik konditioneringen galt — se loggen. '
                'Ellers afvander hele arealet direkte, uden tilløb.')
        return antal, ordener

