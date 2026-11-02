# -*- coding: utf-8 -*-
import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer,
    QgsProcessingMultiStepFeedback,
    QgsCoordinateReferenceSystem,
    QgsProcessing,
    QgsWkbTypes,
)
import processing

#: AIS-vandløbene, som de ligger under et plugins Grunddata-mappe.
VANDLOEB_REL = os.path.join('Grunddata', 'Vandloeb_AIS',
                            'AIS_Jylland_med_inspire_geometri.shp')
ROD = os.path.dirname(os.path.realpath(__file__))


def vandloeb_standard():
    """Stien til AIS-vandløbene, eller None hvis de ikke er der.

    Filen ligger ÉT sted i repoet (N-regnearkets Grunddata), men lægges af
    deploy'et med i begge plugin-zip. Derfor ses der først i pluginnets egen
    Grunddata-mappe — det er den der er med i zippen og den eneste der
    findes i QGIS 4, hvor N-regnearket ikke kan installeres — og dernæst hos
    N-regnearket, som er tilfældet når man kører direkte fra repoet.

    En sti der peger på noget der ikke findes, må ikke blive stående i
    dialogen og lyve: QGIS afviser den med "Incorrect parameter value",
    længe før koden når at forklare hvad der mangler. Et tomt felt er
    ærligt — vandløbene er valgfrie her.
    """
    egen = os.path.join(ROD, VANDLOEB_REL)
    if os.path.isfile(egen):
        return egen
    try:
        from .udpeg_opland.algoritme import nregneark_mappe
        rod = nregneark_mappe()
    except Exception:
        return None
    if not rod:
        return None
    sti = os.path.join(rod, VANDLOEB_REL)
    return sti if os.path.isfile(sti) else None


def _en_decimal(param):
    """Vis feltet med én decimal i stedet for QGIS' seks."""
    param.setMetadata({'widget_wrapper': {'decimals': 1}})
    return param


class GridTilLER(QgsProcessingAlgorithm):

    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'
    CELL_WIDTH = 'CELL_WIDTH'
    CELL_HEIGHT = 'CELL_HEIGHT'
    BUFFER_DISTANCE = 'BUFFER_DISTANCE'
    VANDLOEB = 'VANDLOEB'
    VANDLOEB_BUFFER = 'VANDLOEB_BUFFER'

    def name(self):
        return 'grid_til_ler'

    def displayName(self):
        return 'Grid til LER'

    def group(self):
        return 'Limfjordssekretariatet tools'

    def groupId(self):
        return 'limf_tools'

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, 'Projektgrænse',
            [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(_en_decimal(QgsProcessingParameterNumber(
            self.CELL_WIDTH, 'Cellebredde (meter)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=100.0)))
        self.addParameter(_en_decimal(QgsProcessingParameterNumber(
            self.CELL_HEIGHT, 'Cellehøjde (meter)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=100.0)))
        self.addParameter(_en_decimal(QgsProcessingParameterNumber(
            self.BUFFER_DISTANCE, 'Bufferafstand (meter)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=1.0)))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.VANDLOEB, 'Vandløb (linjer) — søges med i LER',
            types=[QgsProcessing.TypeVectorLine],
            defaultValue=vandloeb_standard(), optional=True))
        self.addParameter(_en_decimal(QgsProcessingParameterNumber(
            self.VANDLOEB_BUFFER, 'Bufferafstand om vandløb (meter)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=1.0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, 'Til LER søgning'))

    def processAlgorithm(self, parameters, context, model_feedback):
        feedback = QgsProcessingMultiStepFeedback(8, model_feedback)
        # Afrundes til én decimal, så geometrien svarer til det tal der står
        # i dialogen — ikke til en flydende rest bag sjette decimal.
        cell_width = round(
            self.parameterAsDouble(parameters, self.CELL_WIDTH, context), 1)
        cell_height = round(
            self.parameterAsDouble(parameters, self.CELL_HEIGHT, context), 1)
        # Halver: linjebuffer går begge sider, så total bredde = angivet afstand
        buffer_dist = round(
            self.parameterAsDouble(parameters, self.BUFFER_DISTANCE,
                                   context), 1) / 2
        vandloeb_dist = round(
            self.parameterAsDouble(parameters, self.VANDLOEB_BUFFER,
                                   context), 1) / 2
        # parameterAsVectorLayer falder tilbage til standardvaerdien, naar
        # feltet er tomt — og saa ville AIS-vandloebene komme med, selv om
        # brugeren havde ryddet feltet. Et felt der ER sendt med og er tomt
        # betyder fra; mangler noeglen helt (kald fra et script), bruges
        # standarden som ellers.
        if self.VANDLOEB in parameters and not parameters[self.VANDLOEB]:
            vandloeb = None
        else:
            vandloeb = self.parameterAsVectorLayer(parameters, self.VANDLOEB,
                                                   context)
        crs = QgsCoordinateReferenceSystem('EPSG:25832')

        # 0. Reprojectér input til EPSG:25832 så buffer er i meter
        input_25832 = processing.run('native:reprojectlayer', {
            'INPUT': parameters[self.INPUT],
            'TARGET_CRS': crs,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        # 1. Konvertér projektgrænse til linje og buffer → yderkant-strip
        boundary_line = processing.run('native:polygonstolines', {
            'INPUT': input_25832,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        boundary_buffered = processing.run('native:buffer', {
            'INPUT': boundary_line,
            'DISTANCE': buffer_dist,
            'SEGMENTS': 5,
            'DISSOLVE': False,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        # 2. Opret fishnet af linjer
        grid_lines = processing.run('qgis:creategrid', {
            'TYPE': 1,
            'EXTENT': input_25832,
            'HSPACING': cell_width,
            'VSPACING': cell_height,
            'CRS': crs,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # 3. Klip gridlinjer til projektgrænsen
        grid_clipped = processing.run('native:clip', {
            'INPUT': grid_lines,
            'OVERLAY': input_25832,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # 4. Buffer gridlinjer → smalle strips
        grid_buffered = processing.run('native:buffer', {
            'INPUT': grid_clipped,
            'DISTANCE': buffer_dist,
            'SEGMENTS': 5,
            'DISSOLVE': False,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(5)
        if feedback.isCanceled():
            return {}

        # 5. Vandløbene med: klip FØR buffer, ellers bufres hele Jylland.
        #    Laget er valgfrit — uden det er resultatet som før.
        lag_til_merge = [boundary_buffered, grid_buffered]
        if vandloeb is not None:
            vandloeb_25832 = processing.run('native:reprojectlayer', {
                'INPUT': vandloeb,
                'TARGET_CRS': crs,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']

            vandloeb_clipped = processing.run('native:clip', {
                'INPUT': vandloeb_25832,
                'OVERLAY': input_25832,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']

            if vandloeb_clipped.featureCount() == 0:
                feedback.pushInfo(
                    'Ingen vandløb inden for projektgrænsen — søgefladen '
                    'bliver som uden vandløbslaget.')
            else:
                vandloeb_buffered = processing.run('native:buffer', {
                    'INPUT': vandloeb_clipped,
                    'DISTANCE': vandloeb_dist,
                    'SEGMENTS': 5,
                    'DISSOLVE': False,
                    'OUTPUT': 'memory:'
                }, context=context, feedback=feedback)['OUTPUT']
                lag_til_merge.append(vandloeb_buffered)
                feedback.pushInfo(
                    '%d vandløbslinjer inden for projektgrænsen bufret med '
                    '%.1f m (samlet bredde).'
                    % (vandloeb_clipped.featureCount(), vandloeb_dist * 2))

        feedback.setCurrentStep(6)
        if feedback.isCanceled():
            return {}

        # 6. Merge yderkant-strip + grid-strips (+ vandløbs-strips)
        merged = processing.run('native:mergevectorlayers', {
            'LAYERS': lag_til_merge,
            'CRS': crs,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(7)
        if feedback.isCanceled():
            return {}

        # 7. Dissolve alt til ét samlet feature
        dissolved = processing.run('native:dissolve', {
            'INPUT': merged,
            'FIELD': [],
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(8)

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            dissolved.fields(), QgsWkbTypes.MultiPolygon, crs)

        for feat in dissolved.getFeatures():
            sink.addFeature(feat)

        return {self.OUTPUT: dest_id}

    def createInstance(self):
        return GridTilLER()
