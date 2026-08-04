# -*- coding: utf-8 -*-
"""Afvandingsanalyse: afstanden fra terrænet ned til det beregnede vandspejl.

Kopi af afvandingsmodellen fra Limf_WetlandTools, men med vandspejlet hentet
direkte fra VASP i stedet for et vilkårligt punktlag. Brugeren vælger en
vandspejlsberegning — og et scenarie, hvis det er en multiberegning — i
VASP-dialogen, som bygger punktlaget og starter dette værktøj med lag og
vandspejlsfelt udfyldt.

Selve metoden er uændret fra WetlandTools-udgaven:

1. Vandspejlspunkterne interpoleres til et raster (IDW med nærmeste naboer).
2. Terræn minus vandspejl giver afvandingsdybden i cm.
3. Dybden klassificeres i afvandingsklasser (frit vandspejl, sump, eng …).
4. Klasserne polygoniseres, navngives og får den faste legende.
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
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
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
        return "Afvandingsanalyse (VASP-vandspejl → DHM)"

    def group(self):
        return "VASP"

    def groupId(self):
        return "vasp"

    def shortHelpString(self):
        return (
            "Beregner hvor langt der er fra terrænet ned til det beregnede "
            "vandspejl, og klassificerer resultatet i afvandingsklasser.\n\n"
            "Vandspejlet kommer fra VASP. Værktøjet startes fra "
            "VASP-dialogen, hvor beregningen — og scenariet, hvis det er en "
            "multiberegning — vælges; punktlag og vandspejlsfelt er derfor "
            "allerede udfyldt. Du vælger terrænmodel, udstrækning og "
            "output.\n\n"
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
        param = QgsProcessingParameterVectorLayer(
            self.PARAM_VSP, "Vandspejlspunkter (udfyldes fra VASP-dialogen)",
            types=[QgsProcessing.TypeVectorPoint])
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        param = QgsProcessingParameterField(
            self.PARAM_FIELD, "Felt med vandspejlskoten",
            type=QgsProcessingParameterField.Numeric,
            parentLayerParameterName=self.PARAM_VSP,
            allowMultiple=False, defaultValue="vsp")
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
        feedback = QgsProcessingMultiStepFeedback(5, model_feedback)
        results = {}
        outputs = {}

        # --- 1) vandspejlet som raster ----------------------------------
        alg_params = {
            "DATA_TYPE": 5,
            "INPUT": parameters[self.PARAM_VSP],
            "MAX_POINTS": parameters[self.PARAM_MAX_POINTS],
            "MIN_POINTS": parameters[self.PARAM_MIN_POINTS],
            "NODATA": 0,
            "POWER": parameters[self.PARAM_POWER],
            "RADIUS": parameters[self.PARAM_RADIUS],
            "SMOOTHING": 0,
            "Z_FIELD": parameters[self.PARAM_FIELD],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        }
        extra = self._grid_udstraekning(parameters, context, feedback)
        if extra:
            alg_params["EXTRA"] = extra
        outputs["Vandspejlsraster"] = processing.run(
            "gdal:gridinversedistancenearestneighbor", alg_params,
            context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        self._tjek_terraen(parameters, context, feedback)

        # --- 2) afvandingsdybden i cm -----------------------------------
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

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        # --- 3) klassificering ------------------------------------------
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

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # --- 4) fra raster til polygoner --------------------------------
        # Polygoniseringen sker med GDAL's Python-binding i stedet for
        # gdal_polygonize.bat, der fejler på danske tegn i stier under Windows.
        outputs["Polygoner"] = {
            "OUTPUT": self._polygonize(outputs["Klasser"]["OUTPUT"])}

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # --- 5) navngivning ---------------------------------------------
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

    def _tjek_terraen(self, parameters, context, feedback):
        """Sammenlign terrænet med vandspejlet i punkterne.

        Ligger vandspejlet over terrænet stort set overalt, bliver hele
        kortet til "frit vandspejl". Det skyldes så godt som altid inputtet
        — forkert terrænmodel, huller fyldt med nul, eller et vandspejl fra
        et andet vandløb — og ikke selve beregningen. Derfor siges det
        tydeligt i loggen frem for at lade brugeren gætte.
        """
        lag = self.parameterAsVectorLayer(parameters, self.PARAM_VSP, context)
        dhm = self.parameterAsRasterLayer(parameters, self.PARAM_DHM, context)
        felt = self.parameterAsString(parameters, self.PARAM_FIELD, context)
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

    def _grid_udstraekning(self, parameters, context, feedback):
        """Kommandolinje-tilføjelse der lægger vandspejlsrasteret på området.

        Uden den interpolerer gdal_grid kun inden for punkternes egen
        bounding box. Vandspejlspunkter fra VASP ligger på en snor langs
        vandløbet, så boksen er et smalt bånd — og alt uden for båndet ville
        havne uden for klasserne, uanset hvilket område brugeren valgte.
        Med -txe/-tye dækker vandspejlsfladen hele beregningsområdet, og
        søgeradius afgør så, hvor langt fra vandløbet der stadig regnes.
        """
        lag = self.parameterAsVectorLayer(parameters, self.PARAM_VSP, context)
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
