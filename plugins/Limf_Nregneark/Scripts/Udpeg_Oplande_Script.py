import os
import shutil

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorDestination,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsProject,
)
import processing

GRUNDDATA_REGNEARK = "mst_n_beregning_jul2023.xlsx"


def get_resultat_regneark_navn():
    """Returnerer dynamisk navn for resultat-regnearket baseret på projektomraade-navn."""
    from qgis.core import QgsSettings
    s = QgsSettings()
    omraade = s.value('vaadomraade_modeller/projektomraade_navn', '')
    if not omraade:
        return "Resultat.xlsx"  # Fallback hvis omraade ikke er tilgængelig
    return f"Resultat_{omraade}.xlsx"


# Grunddata følger altid med pluginnet — hentes centralt fra utils.
# Sørg for at scriptets egen mappe er på sys.path, så 'utils' altid kan
# importeres uanset hvordan QGIS' script-provider loader filen.
import os as _os, sys as _sys
try:
    _d = _os.path.dirname(_os.path.abspath(__file__))
    if _d and _d not in _sys.path:
        _sys.path.insert(0, _d)
except NameError:
    pass  # __file__ ikke defineret i denne load-kontekst
from utils import find_grunddata, find_output_filer, find_resultater_mappe as find_resultater  # noqa: F401


class BeregDirekteOpland(QgsProcessingAlgorithm):

    AREA1           = 'AREA1'
    AREA2           = 'AREA2'
    DN_FELT         = 'DN_FELT'
    POLYGON_OMRAADE = 'POLYGON_OMRAADE'
    DIREKTE_OPLAND  = 'DIREKTE_OPLAND'
    VANDOPLANDET    = 'VANDOPLANDET'
    KLIPPE_OMRAADE  = 'KLIPPE_OMRAADE'

    def name(self):
        return 'direkte_opland'

    def displayName(self):
        return 'Udpeg Oplande Script'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return BeregDirekteOpland()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.AREA1,
            'AREA1 (opstrømsareal 1)',
            types=[QgsProcessing.TypeVectorPolygon]
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.AREA2,
            'AREA2 (opstrømsareal 2)',
            types=[QgsProcessing.TypeVectorPolygon]
        ))
        self.addParameter(QgsProcessingParameterString(
            self.DN_FELT,
            'DN-felt (rasterværdi fra polygonisering)',
            defaultValue='DN',
            optional=True
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.POLYGON_OMRAADE,
            'Polygon område',
            types=[QgsProcessing.TypeVectorPolygon]
        ))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.DIREKTE_OPLAND,
            'Direkte opland'
        ))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.VANDOPLANDET,
            'Vandoplandet'
        ))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.KLIPPE_OMRAADE,
            'Klippe område'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        dn_felt = self.parameterAsString(parameters, self.DN_FELT, context) or 'DN'

        # ── Filtrer: behold kun features hvor DN != 0 ────────────────────────
        udtryk = f'"{dn_felt}" != 0'

        area1_filtreret = processing.run('native:extractbyexpression', {
            'INPUT':      parameters[self.AREA1],
            'EXPRESSION': udtryk,
            'OUTPUT':     QgsProcessing.TEMPORARY_OUTPUT
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        area2_filtreret = processing.run('native:extractbyexpression', {
            'INPUT':      parameters[self.AREA2],
            'EXPRESSION': udtryk,
            'OUTPUT':     QgsProcessing.TEMPORARY_OUTPUT
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        if feedback.isCanceled():
            return {}

        # ── Dissolv til ét polygon per areal ────────────────────────────────
        area1_dissolvet = processing.run('native:dissolve', {
            'INPUT':  area1_filtreret,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        area2_dissolvet = processing.run('native:dissolve', {
            'INPUT':  area2_filtreret,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        if feedback.isCanceled():
            return {}

        # ── Find størst og mindst ved at sammenligne arealer ─────────────────
        lag1 = QgsProcessingUtils.mapLayerFromString(area1_dissolvet, context)
        lag2 = QgsProcessingUtils.mapLayerFromString(area2_dissolvet, context)

        areal1 = sum(f.geometry().area() for f in lag1.getFeatures())
        areal2 = sum(f.geometry().area() for f in lag2.getFeatures())

        if areal1 >= areal2:
            stoerst, mindst = area1_dissolvet, area2_dissolvet
            feedback.pushInfo(f'AREA1 er størst ({areal1:.1f} m²), AREA2 er mindst ({areal2:.1f} m²)')
        else:
            stoerst, mindst = area2_dissolvet, area1_dissolvet
            feedback.pushInfo(f'AREA2 er størst ({areal2:.1f} m²), AREA1 er mindst ({areal1:.1f} m²)')

        if feedback.isCanceled():
            return {}

        # ── VANDOPLANDET = mindste areal minus projektomraade ────────────────
        vandoplandet_dissolved = processing.run('native:dissolve', {
            'INPUT':  mindst,
            'FIELD':  [],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        vandoplandet = processing.run('native:difference', {
            'INPUT':   vandoplandet_dissolved,
            'OVERLAY': parameters[self.POLYGON_OMRAADE],
            'OUTPUT':  parameters[self.VANDOPLANDET]
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        if feedback.isCanceled():
            return {}

        # ── DIREKTE OPLAND = (største minus mindste) minus projektomraade ─────
        forskel = processing.run('native:difference', {
            'INPUT':   stoerst,
            'OVERLAY': mindst,
            'OUTPUT':  QgsProcessing.TEMPORARY_OUTPUT
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        direkte_opland = processing.run('native:difference', {
            'INPUT':   forskel,
            'OVERLAY': parameters[self.POLYGON_OMRAADE],
            'OUTPUT':  parameters[self.DIREKTE_OPLAND]
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        if feedback.isCanceled():
            return {}

        # ── KLIPPE_OMRAADE = union af største areal og projektomraade ─────────
        union_result = processing.run('native:union', {
            'INPUT':   stoerst,
            'OVERLAY': parameters[self.POLYGON_OMRAADE],
            'OUTPUT':  QgsProcessing.TEMPORARY_OUTPUT
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        klippe_omraade = processing.run('native:dissolve', {
            'INPUT':  union_result,
            'FIELD':  [],
            'OUTPUT': parameters[self.KLIPPE_OMRAADE]
        }, context=context, feedback=feedback, is_child_algorithm=True)['OUTPUT']

        # ── Eksportér lag til Outputfiler og indlæs i QGIS ─────────────────
        output_mappe = find_output_filer()
        if output_mappe:
            from qgis.core import QgsVectorFileWriter, QgsCoordinateTransformContext
            for lagnavn, lag_sti in [
                ('Direkte_Opland', direkte_opland),
                ('Vandoplandet',   vandoplandet),
                ('Klippe_Omraade', klippe_omraade),
            ]:
                eksport_sti = os.path.join(output_mappe, f'{lagnavn}.gpkg')

                # Hent det egentlige lag-objekt fra processing-konteksten.
                # native:savefeatures kan ikke finde memory-lag via URI i child-kontekst.
                lag = QgsProcessingUtils.mapLayerFromString(lag_sti, context)
                if lag is None:
                    feedback.reportError(f'Kunne ikke finde lag "{lagnavn}" — springes over.')
                    continue

                opts = QgsVectorFileWriter.SaveVectorOptions()
                opts.driverName  = 'GPKG'
                opts.fileEncoding = 'UTF-8'
                opts.layerName   = lagnavn

                fejl, fejlbesked = QgsVectorFileWriter.writeAsVectorFormatV2(
                    lag, eksport_sti, QgsCoordinateTransformContext(), opts
                )
                if fejl == QgsVectorFileWriter.NoError:
                    feedback.pushInfo(f'Eksporteret: {eksport_sti}')
                    details = QgsProcessingContext.LayerDetails(
                        lagnavn, QgsProject.instance(), lagnavn
                    )
                    context.addLayerToLoadOnCompletion(eksport_sti, details)
                else:
                    feedback.reportError(f'Fejl ved gem af "{lagnavn}": {fejlbesked}')
        else:
            feedback.reportError('Outputfiler_<omraade>-mappen blev ikke fundet — lag ikke eksporteret.')

        # ── Kopiér regneark fra Grunddata til Resultater (hvis ikke der) ───
        grunddata_mappe = find_grunddata()
        resultater_mappe = find_resultater()
        if grunddata_mappe and resultater_mappe:
            kilde = os.path.join(grunddata_mappe, GRUNDDATA_REGNEARK)
            maal  = os.path.join(resultater_mappe, get_resultat_regneark_navn())
            if not os.path.isfile(maal):
                shutil.copy2(kilde, maal)
                feedback.pushInfo(f'Regneark kopieret til: {maal}')
            else:
                feedback.pushInfo(f'Regneark findes allerede: {maal} — ikke overskrevet.')
        else:
            feedback.reportError('Grunddata- eller Resultater-mappen blev ikke fundet — regneark ikke kopieret.')

        return {
            self.DIREKTE_OPLAND: direkte_opland,
            self.VANDOPLANDET:   vandoplandet,
            self.KLIPPE_OMRAADE: klippe_omraade,
        }
