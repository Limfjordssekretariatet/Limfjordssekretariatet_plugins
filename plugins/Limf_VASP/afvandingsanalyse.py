# -*- coding: utf-8 -*-
"""Afvandingsanalyse: afstanden fra terrænet ned til vandspejlet.

Kopi af afvandingsmodellen fra Limf_WetlandTools. Vandspejlet kan komme fra
VASP, fra egne punktlag med opmålte vandspejlskoter — eller fra begge dele på
én gang. Fra VASP-dialogen samles kilderne først, og de flettes her til ét
vandspejl; åbnes værktøjet fra Værktøjskassen, vælges lagene direkte.

Metoden er uændret fra WetlandTools-udgaven:

1. Vandspejlslagene flettes til ét punktlag.
2. Punkterne interpoleres til et raster (IDW med nærmeste naboer).
3. Terræn minus vandspejl giver afvandingsdybden i cm.
4. Dybden klassificeres i afvandingsklasser (frit vandspejl, sump, eng …).
5. Klasserne polygoniseres, navngives og får den faste legende.
"""

from osgeo import gdal, ogr, osr

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsProcessing,
    QgsProcessingParameterBoolean,
    QgsProcessingAlgorithm,
    QgsProcessingMultiStepFeedback,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterExtent,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingUtils,
    QgsRendererCategory,
)

import processing

# Afvandingsklasserne: (gridkode, øvre grænse i cm, navn, farve). Grænserne
# bruges både til reklassifikationen og til navngivningen, så legenden og
# rasterklasserne ikke kan komme til at pege forskellige steder hen.
KLASSER = [
    (1, 0, "< 0 cm Frit vandspejl", "#07256C"),
    (2, 25, "0-25 cm Sump", "#3987ee"),
    (3, 50, "25-50 cm Våd eng", "#46e31a"),
    (4, 75, "50-75 cm Fugtig eng", "#0C3D04"),
    (5, 100, "75-100 cm Tør eng", "#E0E323"),
    (6, 125, "100-125 cm Mark", "#cc840f"),
]
OEVRIGT = ("Øvrigt", "#cccccc")

# Yderpunkter for reklassifikationstabellen (cm) og værdien for "uden for
# klasserne".
_LAVEST = -9000
_HOEJEST = 9000
_UDENFOR = -9999

# Ønsket cellestørrelse i vandspejlsrasteret, og loft over antal celler pr.
# side. Rasteret bruges kun til at bære den (glatte) vandspejlsflade —
# resultatets opløsning følger DHM'et, uanset hvad der står her.
_VSP_CELLE_M = 5.0
_VSP_MAX_CELLER = 2000

# Vandspejlspunkter fortættes til denne afstand langs vandløbet, før der
# interpoleres — se _fortaet_vandspejl.
_FORTAET_M = 10.0
# Ligger punkterne allerede tættere end dette, er der intet at hente.
_FORTAET_GRAENSE_M = 15.0
# Forbindelser længere end så mange gange den typiske punktafstand springes
# over: der er det ikke to nabopunkter på samme vandløb.
_FORTAET_MAKS_FAKTOR = 4.0
# Over så mange punkter bygges nettet ikke. Så tæt ligger punkterne
# alligevel kun, når de ikke trænger til at blive fortættet.
_FORTAET_MAKS_PUNKTER = 5000

# Loft over vandspejlsrasteret, når det lægges over på terrænmodellens net.
# Dybderasteret bliver alligevel lige så stort, så loftet er kun der, for at
# et urimeligt stort område ikke vælter på netop dette trin.
_GLAT_MAKS_CELLER = 250_000_000


def _advar(feedback, besked):
    """Advarsel der virker på både nyere og ældre QGIS-versioner."""
    if hasattr(feedback, "pushWarning"):
        feedback.pushWarning(besked)
    else:
        feedback.pushInfo("ADVARSEL: %s" % besked)


def saet_afvandingslegende(layer):
    """Giv et afvandingslag den faste legende med klassernes farver."""
    legende = [(navn, farve) for _, _, navn, farve in KLASSER]
    legende.append(OEVRIGT)
    kategorier = []
    for navn, farve in legende:
        symbol = QgsFillSymbol.createSimple({
            "color": farve, "outline_color": "black", "outline_width": "0.3"})
        kategorier.append(QgsRendererCategory(navn, symbol, navn))
    layer.setRenderer(QgsCategorizedSymbolRenderer("Navn", kategorier))
    layer.triggerRepaint()


class _Afvandingslegende(QgsProcessingLayerPostProcessorInterface):
    """Sætter legenden på resultatet, når QGIS indlæser det i projektet.

    C++-siden overtager ikke ejerskabet, og algoritmen selv køres på en klon
    der ryddes væk efter kørslen. Uden en reference der lever videre, bliver
    Python-objektet samlet op, og QGIS kalder basisklassens tomme udgave i
    stedet. Derfor holdes den seneste instans i klassevariablen.
    """

    _levende = None

    @classmethod
    def opret(cls):
        cls._levende = cls()
        return cls._levende

    def postProcessLayer(self, layer, context, feedback):
        saet_afvandingslegende(layer)


class AfvandingsanalyseAlgorithm(QgsProcessingAlgorithm):
    """Afvandingsklasser ud fra DHM og et beregnet vandspejl fra VASP."""

    PARAM_VSP = "VSP"
    PARAM_FIELD = "VSP_FIELD"
    PARAM_DHM = "DHM"
    PARAM_EXTENT = "EXTENT"
    PARAM_RADIUS = "RADIUS"
    PARAM_MAX_POINTS = "MAX_POINTS"
    PARAM_MIN_POINTS = "MIN_POINTS"
    PARAM_POWER = "POWER"
    PARAM_NUL_ER_HUL = "NUL_ER_HUL"
    PARAM_OUTPUT = "OUTPUT"

    def name(self):
        return "vasp_afvandingsanalyse"

    def displayName(self):
        return "Afvandingsanalyse (vandspejl → DHM)"

    def group(self):
        return "VASP"

    def groupId(self):
        return "vasp"

    def shortHelpString(self):
        return (
            "Beregner hvor langt der er fra terrænet ned til vandspejlet, og "
            "klassificerer resultatet i afvandingsklasser.\n\n"
            "Vandspejlet kan komme fra VASP, fra egne punktlag med opmålte "
            "vandspejlskoter — eller fra begge dele på én gang. Startes "
            "værktøjet fra VASP-dialogen, samles kilderne der, og lag og "
            "vandspejlsfelt er allerede udfyldt. Herfra vælger du selv "
            "punktlagene under Avancerede parametre; de skal have "
            "vandspejlskoten i et felt med samme navn (standard 'vsp').\n\n"
            "Flere lag flettes til ét vandspejl, før der interpoleres. De "
            "skal høre til samme vandløbssystem — ellers bliver den fælles "
            "vandspejlsflade forkert.\n\n"
            "Du vælger terrænmodel, udstrækning og output.\n\n"
            "Udstrækningen er forudfyldt med vandspejlspunkternes område "
            "plus en margin og kan frit ændres.\n\n"
            "Punkterne interpoleres med IDW (nærmeste naboer), så områder "
            "længere væk end søgeradius ikke får en vandspejlskote og "
            "falder uden for klasserne.\n\n"
            "Celler hvor terrænkoten er præcis 0,00 m regnes som huller i "
            "DHM'et og holdes uden for klasserne — ellers ville et hul "
            "fyldt med nul blive til 'frit vandspejl'. Slå det fra under "
            "Avanceret, hvis 0,00 m er en rigtig kote i dit område.\n\n"
            "Loggen viser terræn minus vandspejl i punkterne, så det kan "
            "ses med det samme, hvis DHM og vandspejl ikke passer sammen.\n\n"
            "Alt hvad der er valgfrit ligger under Avancerede parametre."
        )

    def createInstance(self):
        return AfvandingsanalyseAlgorithm()

    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        # Kun det brugeren skal tage stilling til står frit; resten udfyldes
        # fra VASP-dialogen eller har en fornuftig standardværdi.
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.PARAM_DHM, "Terrænmodel (DHM)"))

        self.addParameter(QgsProcessingParameterExtent(
            self.PARAM_EXTENT, "Udstrækning for beregningen"))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.PARAM_OUTPUT, "Afvandingsklasser",
            QgsProcessing.TypeVectorPolygon))

        # --- avanceret --------------------------------------------------
        # Ét eller flere vandspejlslag, der flettes før analysen. Fra
        # VASP-dialogen er de udfyldt; herfra kan man selv pege på punktlag
        # med opmålte vandspejl — eller blande dem med VASP's beregnede.
        param = QgsProcessingParameterMultipleLayers(
            self.PARAM_VSP, "Vandspejlslag der flettes",
            layerType=QgsProcessing.TypeVectorPoint)
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        # Feltnavnet skal være ens i alle lagene. VASP-dialogen normaliserer
        # kilderne til 'vsp', så derfra passer det af sig selv.
        param = QgsProcessingParameterString(
            self.PARAM_FIELD, "Felt med vandspejlskoten (samme navn i alle lag)",
            defaultValue="vsp")
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        param = QgsProcessingParameterNumber(
            self.PARAM_RADIUS, "Søgeradius ved interpolation (m)",
            QgsProcessingParameterNumber.Double,
            defaultValue=1000.0, minValue=1.0)
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        param = QgsProcessingParameterNumber(
            self.PARAM_MAX_POINTS, "Maks. antal punkter pr. celle",
            QgsProcessingParameterNumber.Integer,
            defaultValue=12, minValue=1)
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        param = QgsProcessingParameterNumber(
            self.PARAM_MIN_POINTS, "Min. antal punkter pr. celle",
            QgsProcessingParameterNumber.Integer,
            defaultValue=3, minValue=0)
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        param = QgsProcessingParameterNumber(
            self.PARAM_POWER, "Vægtningseksponent (IDW)",
            QgsProcessingParameterNumber.Double,
            defaultValue=2.0, minValue=0.1)
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        param = QgsProcessingParameterBoolean(
            self.PARAM_NUL_ER_HUL,
            "Behandl 0,00 m i terrænmodellen som hul", defaultValue=True)
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, model_feedback):
        feedback = QgsProcessingMultiStepFeedback(6, model_feedback)
        results = {}
        outputs = {}

        felt = (self.parameterAsString(parameters, self.PARAM_FIELD, context)
                or "vsp")

        # --- 1) flet vandspejlslagene sammen ----------------------------
        vsp_kilde, vsp_lag, z_felt = self._flet_vandspejl(
            parameters, context, feedback, felt)

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        # --- 2) vandspejlet som raster ----------------------------------
        # parameterAs* frem for parameters[...]: de avancerede tal er ikke
        # med i kaldet, når værktøjet køres fra en model eller et script,
        # og så skal standardværdien bruges i stedet for at fejle.
        alg_params = {
            "DATA_TYPE": 5,
            "INPUT": vsp_kilde,
            "MAX_POINTS": self.parameterAsInt(
                parameters, self.PARAM_MAX_POINTS, context),
            "MIN_POINTS": self.parameterAsInt(
                parameters, self.PARAM_MIN_POINTS, context),
            "NODATA": 0,
            "POWER": self.parameterAsDouble(
                parameters, self.PARAM_POWER, context),
            "RADIUS": self.parameterAsDouble(
                parameters, self.PARAM_RADIUS, context),
            "SMOOTHING": 0,
            "Z_FIELD": z_felt,
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        extra = self._grid_udstraekning(parameters, context, feedback, vsp_lag)
        if extra:
            alg_params["EXTRA"] = extra
        outputs["Vandspejlsraster"] = processing.run(
            "gdal:gridinversedistancenearestneighbor", alg_params,
            context=context, feedback=feedback, is_child_algorithm=True)

        # Vandspejlet lægges over på terrænmodellens eget net, glat.
        # Uden det ville det blive læst som blokke — se _glat_vandspejl.
        outputs["Vandspejlsraster"] = {
            "OUTPUT": self._glat_vandspejl(
                parameters, context, feedback,
                outputs["Vandspejlsraster"]["OUTPUT"])}

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        self._tjek_terraen(parameters, context, feedback, vsp_lag, z_felt)

        # --- 3) afvandingsdybden i cm -----------------------------------
        alg_params = {
            "CELL_SIZE": None,
            "CRS": None,
            "EXPRESSION": self._dybde_udtryk(parameters, context),
            "EXTENT": parameters[self.PARAM_EXTENT],
            "LAYERS": [parameters[self.PARAM_DHM],
                       outputs["Vandspejlsraster"]["OUTPUT"]],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["Dybde"] = processing.run(
            "native:modelerrastercalc", alg_params,
            context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # --- 4) klassificering ------------------------------------------
        alg_params = {
            "DATA_TYPE": 11,
            "INPUT_RASTER": outputs["Dybde"]["OUTPUT"],
            "NODATA_FOR_MISSING": True,
            "NO_DATA": _UDENFOR,
            "RANGE_BOUNDARIES": 0,
            "RASTER_BAND": 1,
            "TABLE": self._reclass_table(),
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        outputs["Klasser"] = processing.run(
            "native:reclassifybytable", alg_params,
            context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # --- 5) fra raster til polygoner --------------------------------
        # Polygoniseringen sker med GDAL's Python-binding i stedet for
        # gdal_polygonize.bat, der fejler på danske tegn i stier under Windows.
        outputs["Polygoner"] = {
            "OUTPUT": self._polygonize(outputs["Klasser"]["OUTPUT"])}

        feedback.setCurrentStep(5)
        if feedback.isCanceled():
            return {}

        # --- 6) navngivning ---------------------------------------------
        alg_params = {
            "FIELD_LENGTH": 0,
            "FIELD_NAME": "Navn",
            "FIELD_PRECISION": 0,
            "FIELD_TYPE": 2,
            "FORMULA": self._navn_formel(),
            "INPUT": outputs["Polygoner"]["OUTPUT"],
            "OUTPUT": parameters[self.PARAM_OUTPUT],
        }
        outputs["Navngivning"] = processing.run(
            "native:fieldcalculator", alg_params,
            context=context, feedback=feedback, is_child_algorithm=True)

        self.dest_id = outputs["Navngivning"]["OUTPUT"]
        results[self.PARAM_OUTPUT] = self.dest_id

        # Legenden sættes på det lag QGIS selv indlæser bagefter.
        if context.willLoadLayerOnCompletion(self.dest_id):
            context.layerToLoadOnCompletionDetails(
                self.dest_id).setPostProcessor(_Afvandingslegende.opret())
        return results

    def postProcessAlgorithm(self, context, feedback):
        """Legende til de kørsler hvor laget ikke indlæses af QGIS selv."""
        dest_id = getattr(self, "dest_id", "")
        if not dest_id or context.willLoadLayerOnCompletion(dest_id):
            return {}
        layer = QgsProcessingUtils.mapLayerFromString(dest_id, context)
        if layer is not None:
            saet_afvandingslegende(layer)
        return {}

    # ------------------------------------------------------------------
    def _dybde_udtryk(self, parameters, context):
        """Udtrykket der giver afvandingsdybden i cm.

        DHM-fliser fra en klipning eller sammenfletning kan være fyldt med
        0,00 m i stedet for nodata. Uden maskering læses de som terræn i
        havniveau, og alt med et vandspejl over 0 m havner i "frit
        vandspejl". Rigtige LiDAR-koter rammer aldrig præcis 0,000000, så
        de kan skilles fra ved lighed med nul. Maskerede celler får -9999,
        som falder uden for klassetabellen og bliver til "Øvrigt".
        """
        if not self.parameterAsBool(parameters, self.PARAM_NUL_ER_HUL,
                                    context):
            return '("A@1" - "B@1")*100'
        return ('(("A@1" != 0) * (("A@1" - "B@1")*100))'
                ' + (("A@1" = 0) * %d)' % _UDENFOR)

    def _flet_vandspejl(self, parameters, context, feedback, felt):
        """Flet alle valgte vandspejlslag til ét renset punktlag.

        Lag der mangler kote-feltet, får ingen vandspejlskote ved
        sammenfletningen; de punkter sorteres fra bagefter, så et forkert
        feltnavn i ét af lagene ikke trækker hele vandspejlsfladen mod nul.
        Er koten gemt i et tekstfelt (fx en håndlavet shapefil), regnes den
        om til et talfelt — komma og punktum som decimaltegn begge.

        Returnerer (kilde_streng, lag, z_felt): kilde_strengen og z_felt
        gives videre til gdal_grid, laget bruges til udstrækning og tjek.
        """
        lag_liste = self.parameterAsLayerList(
            parameters, self.PARAM_VSP, context)
        if not lag_liste:
            raise QgsProcessingException(
                "Vælg mindst ét vandspejlslag under Avancerede parametre.")

        mangler = [lag.name() for lag in lag_liste
                   if lag.fields().lookupField(felt) < 0]
        if mangler:
            _advar(feedback,
                   "Feltet '%s' findes ikke i: %s. Punkterne derfra får ingen "
                   "vandspejlskote og indgår ikke i sammenfletningen."
                   % (felt, ", ".join(mangler)))
        if len(mangler) == len(lag_liste):
            raise QgsProcessingException(
                "Ingen af de valgte lag har feltet '%s' med vandspejlskoten."
                % felt)

        flettet = processing.run(
            "native:mergevectorlayers",
            {"LAYERS": lag_liste, "CRS": lag_liste[0].crs(),
             "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
            context=context, feedback=feedback, is_child_algorithm=True)

        kilde = flettet["OUTPUT"]
        z_felt = felt

        # Er koten et tekstfelt — enten fordi et af lagene har den som tekst,
        # eller fordi sammenfletningen udvidede den til tekst — kan gdal_grid
        # ikke bruge den som Z. Læg et rigtigt talfelt ved siden af.
        flettet_lag = QgsProcessingUtils.mapLayerFromString(kilde, context)
        if flettet_lag is not None:
            idx = flettet_lag.fields().lookupField(felt)
            if idx >= 0 and not flettet_lag.fields().at(idx).isNumeric():
                _advar(feedback,
                       "Feltet '%s' er et tekstfelt. Værdierne regnes om til "
                       "tal (komma og punktum som decimaltegn); værdier der "
                       "ikke er tal, sorteres fra." % felt)
                # Kun celler der ER et tal, regnes om. to_real() på en
                # tekst som "ikke målt" stopper hele feltberegningen med
                # en fejl — den skal bare springes over.
                formel = (
                    'CASE WHEN regexp_match(trim("{f}"),'
                    " '^-?[0-9]*[.,]?[0-9]+$') > 0"
                    ' THEN to_real(replace(trim("{f}"), \',\', \'.\')) END'
                ).format(f=felt)
                talfelt = processing.run(
                    "native:fieldcalculator",
                    {"INPUT": kilde, "FIELD_NAME": "vsp_tal",
                     "FIELD_TYPE": 0, "FIELD_LENGTH": 0, "FIELD_PRECISION": 0,
                     "FORMULA": formel,
                     "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
                    context=context, feedback=feedback, is_child_algorithm=True)
                kilde = talfelt["OUTPUT"]
                z_felt = "vsp_tal"

        # Punkter uden en kote i feltet ville blive læst som kote 0 af
        # interpolationen — sortér dem fra.
        renset = processing.run(
            "native:extractbyexpression",
            {"INPUT": kilde,
             "EXPRESSION": '"%s" IS NOT NULL' % z_felt,
             "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
            context=context, feedback=feedback, is_child_algorithm=True)

        lag = QgsProcessingUtils.mapLayerFromString(renset["OUTPUT"], context)
        if lag is None or not lag.isValid():
            raise QgsProcessingException(
                "Kunne ikke sammenflette vandspejlslagene.")
        antal = lag.featureCount()
        if antal < 3:
            raise QgsProcessingException(
                "Kun %d vandspejlspunkter med en kote i '%s' efter "
                "sammenfletningen. Der skal mindst være 3 for at kunne "
                "interpolere en vandspejlsflade." % (antal, felt))
        feedback.pushInfo(
            "Sammenflettede %d vandspejlslag til %d punkter med en kote i "
            "'%s'." % (len(lag_liste), antal, felt))

        bbox = lag.extent()
        if not bbox.isEmpty() and max(bbox.width(), bbox.height()) > 6000:
            _advar(feedback,
                   "Vandspejlspunkterne spænder over %.1f × %.1f km. Hvis der "
                   "er valgt vandspejl fra flere forskellige vandløb, eller "
                   "et punktlag i et forkert koordinatsystem, bliver den "
                   "flettede vandspejlsflade — og resultatet — forkert. "
                   "Vandspejl der skal flettes, skal høre til samme "
                   "vandløbssystem."
                   % (bbox.width() / 1000, bbox.height() / 1000))
        # Til interpolationen bruges et fortættet punktsæt, så IDW'en kan
        # følge faldet. Kontrollen mod terrænet og udstrækningen bygger
        # fortsat på de målte punkter — det er dem, tallene skal handle om.
        fortaettet = self._fortaet_vandspejl(lag, z_felt, feedback)
        return fortaettet or renset["OUTPUT"], lag, z_felt


    def _tjek_terraen(self, parameters, context, feedback, lag, felt):
        """Sammenlign terrænet med vandspejlet i punkterne.

        Ligger vandspejlet over terrænet stort set overalt, bliver hele
        kortet til "frit vandspejl". Det skyldes så godt som altid inputtet
        — forkert terrænmodel, huller fyldt med nul, eller et vandspejl fra
        et andet vandløb — og ikke selve beregningen. Derfor siges det
        tydeligt i loggen frem for at lade brugeren gætte.
        """
        dhm = self.parameterAsRasterLayer(parameters, self.PARAM_DHM, context)
        if lag is None or dhm is None or not felt:
            return

        transform = None
        if (lag.crs().isValid() and dhm.crs().isValid()
                and lag.crs() != dhm.crs()):
            transform = QgsCoordinateTransform(
                lag.crs(), dhm.crs(), context.transformContext())

        maskeret = self.parameterAsBool(
            parameters, self.PARAM_NUL_ER_HUL, context)
        provider = dhm.dataProvider()
        forskelle, nuller, udenfor = [], 0, 0
        for feature in lag.getFeatures():
            vsp = feature[felt]
            geometri = feature.geometry()
            if vsp is None or geometri.isEmpty():
                continue
            punkt = geometri.asPoint()
            if transform is not None:
                punkt = transform.transform(punkt)
            vaerdi, fandtes = provider.sample(punkt, 1)
            if not fandtes or vaerdi is None:
                udenfor += 1
                continue
            if vaerdi == 0.0:
                nuller += 1
                # Tælles ikke med i sammenligningen, når de alligevel
                # sorteres fra af beregningen.
                if maskeret:
                    continue
            forskelle.append(float(vaerdi) - float(vsp))

        if nuller:
            _advar(feedback,
                   "%d af punkterne ligger på celler med terrænkoten 0,00 m. "
                   "Det er næsten altid huller i DHM'et, der er fyldt med nul "
                   "i stedet for nodata, og de ville ellers blive til 'frit "
                   "vandspejl'. De %s."
                   % (nuller, "sorteres fra" if maskeret
                      else "regnes med, fordi 0,00 m ikke behandles som hul"))

        if not forskelle:
            _advar(feedback,
                   "Terrænmodellen har ingen brugbare koter der hvor "
                   "vandspejlspunkterne ligger (%d uden for modellen, %d i "
                   "nul-fyldte huller). Tjek at DHM'et dækker samme område "
                   "som beregningen." % (udenfor, nuller))
            return

        forskelle.sort()
        median = forskelle[len(forskelle) // 2]
        under = sum(1 for d in forskelle if d < 0)
        feedback.pushInfo(
            "Terræn minus vandspejl i de %d punkter: mindst %.2f m, median "
            "%.2f m, størst %.2f m."
            % (len(forskelle), forskelle[0], median, forskelle[-1]))
        if udenfor:
            feedback.pushInfo(
                "%d punkter ligger uden for terrænmodellen." % udenfor)

        if under > len(forskelle) // 2:
            _advar(feedback,
                   "Vandspejlet ligger over terrænet i %d af %d punkter "
                   "(median %.2f m). Så bliver stort set hele kortet til "
                   "'frit vandspejl'. Tjek at terrænmodellen dækker "
                   "vandløbet, at den er i meter over DVR90, og at "
                   "vandspejlsberegningen hører til netop dette vandløb."
                   % (under, len(forskelle), median))

    def _grid_udstraekning(self, parameters, context, feedback, lag):
        """Kommandolinje-tilføjelse der lægger vandspejlsrasteret på området.

        Uden den interpolerer gdal_grid kun inden for punkternes egen
        bounding box. Vandspejlspunkter fra VASP ligger på en snor langs
        vandløbet, så boksen er et smalt bånd — og alt uden for båndet ville
        havne uden for klasserne, uanset hvilket område brugeren valgte.
        Med -txe/-tye dækker vandspejlsfladen hele beregningsområdet, og
        søgeradius afgør så, hvor langt fra vandløbet der stadig regnes.

        ``lag`` er det flettede vandspejlslag.
        """
        if lag is None:
            return ""
        omraade = self.parameterAsExtent(
            parameters, self.PARAM_EXTENT, context, lag.crs())
        if omraade.isEmpty():
            return ""
        kolonner = self._celler(omraade.width())
        raekker = self._celler(omraade.height())
        feedback.pushInfo(
            "Vandspejlsraster: %d × %d celler (%.1f × %.1f m) over "
            "beregningsområdet."
            % (kolonner, raekker, omraade.width() / kolonner,
               omraade.height() / raekker))
        return ("-txe %.3f %.3f -tye %.3f %.3f -outsize %d %d"
                % (omraade.xMinimum(), omraade.xMaximum(),
                   omraade.yMinimum(), omraade.yMaximum(),
                   kolonner, raekker))

    def _fortaet_vandspejl(self, lag, z_felt, feedback):
        """Læg punkter ind mellem vandspejlspunkterne, før der interpoleres.

        IDW er et vejet gennemsnit af naboerne. Derfor kan den ikke gengive
        et jævnt fald mellem to punkter: fladen buer imellem dem, og buen
        vokser med punktafstanden gange faldet. Med 300 m mellem punkterne
        og 3 ‰ fald måltes en typisk afvigelse på 9,5 cm og op til 23 cm —
        og med 25 cm mellem afvandingsklasserne flytter det klassegrænsen
        frem og tilbage. Det er de hak, der ses, hvor punkterne er få og
        faldet stejlt.

        Punkterne forbindes derfor i et net, hvor hvert punkt hænger
        sammen med sin nærmeste nabo (et mindste udspændende træ), og der
        lægges punkter ind langs forbindelserne med koten interpoleret
        lineært imellem. Så er faldet mellem to nabopunkter lille, og buen
        forsvinder: samme måling gav 0,4 cm i stedet for 9,5 cm.

        Nettet følger vandløbet af sig selv, fordi vandspejlspunkter ligger
        på en snor langs det. Forbindelser, der er meget længere end de
        øvrige, springes over — der er det ikke to nabopunkter på samme
        vandløb, men to systemer, der tilfældigvis kommer nær hinanden.

        Returnerer stien til et punktlag, eller None hvis der ikke er
        noget at hente.
        """
        import numpy as np

        xy, koter = [], []
        for objekt in lag.getFeatures():
            geometri = objekt.geometry()
            vaerdi = objekt[z_felt]
            if geometri.isEmpty() or vaerdi is None:
                continue
            punkt = geometri.asPoint()
            xy.append((punkt.x(), punkt.y()))
            koter.append(float(vaerdi))
        antal = len(xy)
        if antal < 3:
            return None
        if antal > _FORTAET_MAKS_PUNKTER:
            return None

        xy = np.asarray(xy, dtype=float)
        koter = np.asarray(koter, dtype=float)

        kanter, laengder = self._naermeste_nabo_net(xy)
        if not len(laengder):
            return None
        typisk = float(np.median(laengder))
        if typisk <= _FORTAET_GRAENSE_M:
            feedback.pushInfo(
                "Vandspejlspunkterne ligger typisk %.0f m fra hinanden — tæt "
                "nok til at interpolationen kan følge faldet." % typisk)
            return None

        graense = max(_FORTAET_MAKS_FAKTOR * typisk, _FORTAET_GRAENSE_M)
        nye_x, nye_y, nye_z = [], [], []
        sprunget = 0
        for (i, j), laengde in zip(kanter, laengder):
            if laengde > graense:
                sprunget += 1
                continue
            skridt = int(laengde // _FORTAET_M)
            for m in range(1, skridt):
                t = m * _FORTAET_M / laengde
                nye_x.append(xy[i, 0] + t * (xy[j, 0] - xy[i, 0]))
                nye_y.append(xy[i, 1] + t * (xy[j, 1] - xy[i, 1]))
                nye_z.append(koter[i] + t * (koter[j] - koter[i]))
        if not nye_x:
            return None

        sti = self._skriv_punkter(
            lag.crs(), z_felt,
            list(zip(xy[:, 0], xy[:, 1], koter))
            + list(zip(nye_x, nye_y, nye_z)))
        if sti is None:
            return None

        feedback.pushInfo(
            "Vandspejlspunkterne ligger typisk %.0f m fra hinanden. De er "
            "fortættet til %.0f m langs vandløbet (%d → %d punkter), så "
            "interpolationen kan gengive faldet."
            % (typisk, _FORTAET_M, antal, antal + len(nye_x)))
        if sprunget:
            feedback.pushInfo(
                "%d forbindelser over %.0f m blev sprunget over — der ligger "
                "punkterne for langt fra hinanden til at være naboer på "
                "samme vandløb." % (sprunget, graense))
        return sti

    @staticmethod
    def _naermeste_nabo_net(xy):
        """Mindste udspændende træ over punkterne (Prim).

        Returnerer (kanter, længder). Afstandene regnes en søjle ad gangen,
        så der aldrig står en hel afstandsmatrix i hukommelsen.
        """
        import numpy as np

        antal = len(xy)
        i_nettet = np.zeros(antal, dtype=bool)
        i_nettet[0] = True
        bedste = np.hypot(xy[:, 0] - xy[0, 0], xy[:, 1] - xy[0, 1])
        forael = np.zeros(antal, dtype=int)
        kanter, laengder = [], []
        for _ in range(antal - 1):
            skjult = np.where(i_nettet, np.inf, bedste)
            j = int(np.argmin(skjult))
            if not np.isfinite(skjult[j]):
                break
            kanter.append((int(forael[j]), j))
            laengder.append(float(skjult[j]))
            i_nettet[j] = True
            afstand = np.hypot(xy[:, 0] - xy[j, 0], xy[:, 1] - xy[j, 1])
            bedre = (afstand < bedste) & ~i_nettet
            bedste = np.where(bedre, afstand, bedste)
            forael = np.where(bedre, j, forael)
        return kanter, np.asarray(laengder)

    @staticmethod
    def _skriv_punkter(crs, feltnavn, poster):
        """Skriv (x, y, kote) til en midlertidig GeoPackage. Sti eller None."""
        from qgis.PyQt.QtCore import QVariant
        from qgis.core import (QgsFeature, QgsField, QgsFields, QgsGeometry,
                               QgsPointXY, QgsProject, QgsVectorFileWriter,
                               QgsWkbTypes)

        felter = QgsFields()
        felter.append(QgsField(feltnavn, QVariant.Double))
        sti = QgsProcessingUtils.generateTempFilename(
            "vandspejl_fortaettet.gpkg")
        valg = QgsVectorFileWriter.SaveVectorOptions()
        valg.driverName = "GPKG"
        valg.layerName = "vsp"
        skriver = QgsVectorFileWriter.create(
            sti, felter, QgsWkbTypes.Point, crs,
            QgsProject.instance().transformContext(), valg)
        if skriver.hasError() != QgsVectorFileWriter.NoError:
            del skriver
            return None
        for x, y, kote in poster:
            objekt = QgsFeature(felter)
            objekt.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            objekt.setAttributes([float(kote)])
            skriver.addFeature(objekt)
        del skriver
        return "%s|layername=vsp" % sti

    def _glat_vandspejl(self, parameters, context, feedback, vsp_raster):
        """Læg vandspejlet over på terrænmodellens net — glat.

        Vandspejlet interpoleres i 5 m celler, for et vandspejl er en blød
        flade, og at regne IDW i terrænmodellens 0,4 m ville tage mange
        minutter uden at blive klogere. Men trækkes de to rastre fra
        hinanden direkte, læses vandspejlet med nærmeste nabo: det står
        så fast inden for hver 5 m firkant, og afvandingsdybden kommer til
        at ligge i trapper.

        Det ses ikke, hvor terrænet har fald — der er det terrænet, der
        afgør, hvor klassegrænsen løber. Men på det flade, hvor dybden
        ligger lige omkring en klassegrænse, følger grænsen vandspejlets
        firkanter, og resultatet bliver kantet med lodrette og vandrette
        spring.

        Derfor samples vandspejlet om til terrænets eget net med bilineær
        interpolation, før der trækkes fra. Nettet lægges oven i
        terrænmodellens egne celler, så det sidste fratræk er celle mod
        celle. Kan det ikke lade sig gøre, bruges rasteret som det er —
        et kantet resultat er bedre end intet.
        """
        dhm = self.parameterAsRasterLayer(parameters, self.PARAM_DHM, context)
        if dhm is None:
            return vsp_raster
        celle_x = dhm.rasterUnitsPerPixelX()
        celle_y = dhm.rasterUnitsPerPixelY()
        if not celle_x or not celle_y:
            return vsp_raster

        omraade = self.parameterAsExtent(
            parameters, self.PARAM_EXTENT, context, dhm.crs())
        if omraade.isEmpty():
            return vsp_raster

        # Snap til terrænmodellens net, så cellerne flugter nøjagtigt.
        dhm_omraade = dhm.extent()
        x0 = (dhm_omraade.xMinimum()
              + (omraade.xMinimum() - dhm_omraade.xMinimum())
              // celle_x * celle_x)
        y0 = (dhm_omraade.yMinimum()
              + (omraade.yMinimum() - dhm_omraade.yMinimum())
              // celle_y * celle_y)
        kolonner = int((omraade.xMaximum() - x0) / celle_x) + 1
        raekker = int((omraade.yMaximum() - y0) / celle_y) + 1
        if kolonner * raekker > _GLAT_MAKS_CELLER:
            _advar(feedback,
                   "Området er for stort til at lægge vandspejlet over på "
                   "terrænets net (%d × %d celler). Vandspejlet bruges i %.1f "
                   "m celler, og klassegrænserne kan derfor blive kantede på "
                   "det flade. Vælg et mindre område, hvis det generer."
                   % (kolonner, raekker, _VSP_CELLE_M))
            return vsp_raster

        # Hvilken værdi der betyder "ingen vandspejl", læses af rasteret
        # selv frem for at blive antaget — ellers ville de tomme celler
        # blive blandet ind i fladen ved omsamplingen.
        tom = self._nodata(vsp_raster)

        ud = QgsProcessingUtils.generateTempFilename("vandspejl_glat.tif")
        try:
            resultat = gdal.Warp(
                ud, vsp_raster,
                xRes=celle_x, yRes=celle_y,
                outputBounds=(x0, y0, x0 + kolonner * celle_x,
                              y0 + raekker * celle_y),
                resampleAlg="bilinear",
                srcNodata=tom, dstNodata=tom,
                outputType=gdal.GDT_Float32)
        except Exception as exc:
            _advar(feedback,
                   "Vandspejlet kunne ikke lægges over på terrænets net "
                   "(%s). Klassegrænserne kan blive kantede." % exc)
            return vsp_raster
        if resultat is None:
            _advar(feedback,
                   "Vandspejlet kunne ikke lægges over på terrænets net. "
                   "Klassegrænserne kan blive kantede.")
            return vsp_raster
        resultat = None

        feedback.pushInfo(
            "Vandspejlet lagt over på terrænets net: %d × %d celler á %.2f m "
            "(interpoleret i %.1f m)."
            % (kolonner, raekker, celle_x, _VSP_CELLE_M))
        return ud

    @staticmethod
    def _nodata(raster_sti):
        """Rasterets egen "ingen værdi", eller _UDENFOR hvis den ikke er sat."""
        try:
            ds = gdal.Open(raster_sti)
            if ds is not None:
                vaerdi = ds.GetRasterBand(1).GetNoDataValue()
                ds = None
                if vaerdi is not None:
                    return vaerdi
        except Exception:
            pass
        return _UDENFOR

    @staticmethod
    def _celler(laengde):
        """Antal celler på en side — ca. _VSP_CELLE_M, men aldrig for mange."""
        antal = int(round(laengde / _VSP_CELLE_M))
        return max(2, min(antal, _VSP_MAX_CELLER))

    @staticmethod
    def _reclass_table():
        """Reklassifikationstabellen: min, maks, gridkode — som tekst."""
        tabel = []
        nedre = _LAVEST
        for kode, oevre, _, _ in KLASSER:
            tabel += [str(nedre), str(oevre), str(kode)]
            nedre = oevre
        # Alt dybere end den sidste grænse falder uden for klasserne.
        tabel += [str(nedre), str(_HOEJEST), str(_UDENFOR)]
        return tabel

    @staticmethod
    def _navn_formel():
        """Feltberegner-udtryk der oversætter gridkoden til klassenavnet."""
        linjer = ["CASE"]
        for kode, _, navn, _ in KLASSER:
            linjer.append("  WHEN \"Gridkode\" = %d THEN '%s'" % (kode, navn))
        linjer.append("  ELSE '%s'" % OEVRIGT[0])
        linjer.append("END")
        return "\n".join(linjer)

    @staticmethod
    def _polygonize(raster_path):
        """Polygonisér klasserasteret til en midlertidig GeoPackage."""
        gpkg = QgsProcessingUtils.generateTempFilename(
            "afvanding_polygoner.gpkg")
        src_ds = gdal.Open(raster_path)
        if src_ds is None:
            raise ValueError("Kunne ikke åbne klasserasteret: %s" % raster_path)
        try:
            srs = osr.SpatialReference()
            srs.ImportFromWkt(src_ds.GetProjection())
            driver = ogr.GetDriverByName("GPKG")
            dst_ds = driver.CreateDataSource(gpkg)
            dst_layer = dst_ds.CreateLayer(
                "OUTPUT", srs=srs, geom_type=ogr.wkbPolygon)
            dst_layer.CreateField(ogr.FieldDefn("Gridkode", ogr.OFTInteger))
            gdal.Polygonize(src_ds.GetRasterBand(1), None, dst_layer, 0, [],
                            callback=None)
        finally:
            dst_layer = None
            dst_ds = None
            src_ds = None
        return "%s|layername=OUTPUT" % gpkg
