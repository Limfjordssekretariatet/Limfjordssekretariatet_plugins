"""
Reference-implementation af SAGA-modellen som Python-script (ikke i brug i produktionen).
Hardkodede koordinater og SEGMENT_IDs herunder er eksempler og skal udskiftes.
"""

from typing import Any, Optional

from qgis.core import QgsProcessing
from qgis.core import QgsProcessingAlgorithm
from qgis.core import QgsProcessingContext
from qgis.core import QgsProcessingFeedback, QgsProcessingMultiStepFeedback
from qgis.core import QgsProcessingParameterRasterLayer
from qgis.core import QgsProcessingParameterVectorLayer
from qgis.core import QgsProcessingParameterNumber
from qgis.core import QgsProcessingParameterFeatureSink
from qgis import processing


class Supersejmodel(QgsProcessingAlgorithm):

    def initAlgorithm(self, config: Optional[dict[str, Any]] = None):
        self.addParameter(QgsProcessingParameterRasterLayer('dem_2x2', 'DEM_2x2', defaultValue=None))
        self.addParameter(QgsProcessingParameterVectorLayer('dmi_grid', 'DMI_GRID', types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterVectorLayer('polygon_omraade', 'Polygon_omraade', types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterNumber('x_1', 'x_1', type=QgsProcessingParameterNumber.Double, defaultValue=None))
        self.addParameter(QgsProcessingParameterNumber('y_1', 'y_1', type=QgsProcessingParameterNumber.Double, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('Udvidet_linje', 'Udvidet_linje', type=QgsProcessing.TypeVectorLine, createByDefault=True, supportsAppend=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('Punkt_1', 'punkt_1', type=QgsProcessing.TypeVectorAnyGeometry, createByDefault=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('Punkt_2', 'punkt_2', type=QgsProcessing.TypeVectorAnyGeometry, createByDefault=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('Kordinater', 'Kordinater', type=QgsProcessing.TypeVectorPoint, createByDefault=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('Direkte_opland', 'Direkte_opland', type=QgsProcessing.TypeVectorAnyGeometry, createByDefault=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('Polygon_omraade_linje', 'Polygon_omraade_linje', type=QgsProcessing.TypeVectorLine, createByDefault=True, supportsAppend=True, defaultValue=None))

    def processAlgorithm(self, parameters: dict[str, Any], context: QgsProcessingContext, model_feedback: QgsProcessingFeedback) -> dict[str, Any]:
        # Use a multi-step feedback, so that individual child algorithm progress reports are adjusted for the
        # overall progress through the model
        feedback = QgsProcessingMultiStepFeedback(17, model_feedback)
        results = {}
        outputs = {}

        # Polygoner til linjer
        alg_params = {
            'INPUT': parameters['polygon_omraade'],
            'OUTPUT': parameters['Polygon_omraade_linje']
        }
        outputs['PolygonerTilLinjer'] = processing.run('native:polygonstolines', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Polygon_omraade_linje'] = outputs['PolygonerTilLinjer']['OUTPUT']

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        # Fill Sinks (Wang & Liu)
        alg_params = {
            'ELEV': parameters['dem_2x2'],
            'MINSLOPE': 0.01,
            'FDIR': QgsProcessing.TEMPORARY_OUTPUT,
            'FILLED': QgsProcessing.TEMPORARY_OUTPUT,
            'WSHED': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['FillSinksWangLiu'] = processing.run('sagang:fillsinkswangliu', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        # Upslope Area hele
        alg_params = {
            'CONVERGE': 1.1,
            'ELEVATION': outputs['FillSinksWangLiu']['FILLED'],
            'METHOD': 0,  # [0] Deterministic 8
            'MFD_CONTOUR': False,
            'SINKROUTE': None,
            'TARGET': None,
            'TARGET_PT_X': parameters['x_1'],
            'TARGET_PT_Y': parameters['y_1'],
            'AREA': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['UpslopeAreaHele'] = processing.run('sagang:upslopearea', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # Channel Network and Drainage Basins
        alg_params = {
            'DEM': outputs['FillSinksWangLiu']['FILLED'],
            'SUBBASINS': True,
            'THRESHOLD': 5,
            'BASINS': QgsProcessing.TEMPORARY_OUTPUT,
            'SEGMENTS': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ChannelNetworkAndDrainageBasins'] = processing.run('sagang:channelnetworkanddrainagebasins', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # Upslope Area mindre
        # OBS: TARGET_PT_X og TARGET_PT_Y skal sættes til projektspecifikke koordinater
        alg_params = {
            'CONVERGE': 1.1,
            'ELEVATION': outputs['FillSinksWangLiu']['FILLED'],
            'METHOD': 0,  # [0] Deterministic 8
            'MFD_CONTOUR': False,
            'SINKROUTE': None,
            'TARGET': None,
            'TARGET_PT_X': parameters['x_1'],
            'TARGET_PT_Y': parameters['y_1'],
            'AREA': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['UpslopeAreaMindre'] = processing.run('sagang:upslopearea', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(5)
        if feedback.isCanceled():
            return {}

        # Polygonisér (raster til vektor) hele område
        alg_params = {
            'BAND': 1,
            'EIGHT_CONNECTEDNESS': False,
            'EXTRA': None,
            'FIELD': 'DN',
            'INPUT': outputs['UpslopeAreaHele']['AREA'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PolygonisrRasterTilVektorHeleOmrde'] = processing.run('gdal:polygonize', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(6)
        if feedback.isCanceled():
            return {}

        # Polygonisér (raster til vektor)
        alg_params = {
            'BAND': 1,
            'EIGHT_CONNECTEDNESS': False,
            'EXTRA': None,
            'FIELD': 'DN',
            'INPUT': outputs['UpslopeAreaMindre']['AREA'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PolygonisrRasterTilVektor'] = processing.run('gdal:polygonize', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(7)
        if feedback.isCanceled():
            return {}

        # Klip
        alg_params = {
            'INPUT': outputs['ChannelNetworkAndDrainageBasins']['SEGMENTS'],
            'OVERLAY': parameters['polygon_omraade'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Klip'] = processing.run('native:clip', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(8)
        if feedback.isCanceled():
            return {}

        # Udtræk vha. udtryk
        alg_params = {
            'EXPRESSION': '"ORDER" = aggregate(@layer,\'max\',"ORDER")',
            'INPUT': outputs['Klip']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['UdtrkVhaUdtryk'] = processing.run('native:extractbyexpression', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(9)
        if feedback.isCanceled():
            return {}

        # Udvid linjer
        alg_params = {
            'END_DISTANCE': 5,
            'INPUT': outputs['UdtrkVhaUdtryk']['OUTPUT'],
            'START_DISTANCE': 5,
            'OUTPUT': parameters['Udvidet_linje']
        }
        outputs['UdvidLinjer'] = processing.run('native:extendlines', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Udvidet_linje'] = outputs['UdvidLinjer']['OUTPUT']

        feedback.setCurrentStep(10)
        if feedback.isCanceled():
            return {}

        # Forskel (flere lag)
        alg_params = {
            'INPUT': outputs['PolygonisrRasterTilVektorHeleOmrde']['OUTPUT'],
            'OVERLAYS': outputs['PolygonisrRasterTilVektor']['OUTPUT'],
            'OUTPUT': parameters['Direkte_opland']
        }
        outputs['ForskelFlereLag'] = processing.run('native:multidifference', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Direkte_opland'] = outputs['ForskelFlereLag']['OUTPUT']

        feedback.setCurrentStep(11)
        if feedback.isCanceled():
            return {}

        # Linjeskæringer
        alg_params = {
            'INPUT': outputs['UdvidLinjer']['OUTPUT'],
            'INPUT_FIELDS': [''],
            'INTERSECT': outputs['PolygonerTilLinjer']['OUTPUT'],
            'INTERSECT_FIELDS': [''],
            'INTERSECT_FIELDS_PREFIX': None,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Linjeskringer'] = processing.run('native:lineintersections', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(12)
        if feedback.isCanceled():
            return {}

        # Centroider
        alg_params = {
            'ALL_PARTS': False,
            'INPUT': outputs['ForskelFlereLag']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['Centroider'] = processing.run('native:centroids', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(13)
        if feedback.isCanceled():
            return {}

        # Middelkoordinat(er)
        alg_params = {
            'INPUT': outputs['Linjeskringer']['OUTPUT'],
            'UID': 'SEGMENT_ID',
            'WEIGHT': None,
            'OUTPUT': parameters['Kordinater']
        }
        outputs['Middelkoordinater'] = processing.run('native:meancoordinates', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Kordinater'] = outputs['Middelkoordinater']['OUTPUT']

        feedback.setCurrentStep(14)
        if feedback.isCanceled():
            return {}

        # Udtræk vha. udtryk mindre opland
        # OBS: SEGMENT_ID-værdien skal sættes til det projektspecifikke ID
        alg_params = {
            'EXPRESSION': '"SEGMENT_ID" IS NOT NULL',
            'INPUT': outputs['Middelkoordinater']['OUTPUT'],
            'OUTPUT': parameters['Punkt_2']
        }
        outputs['UdtrkVhaUdtrykMindreOpland'] = processing.run('native:extractbyexpression', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Punkt_2'] = outputs['UdtrkVhaUdtrykMindreOpland']['OUTPUT']

        feedback.setCurrentStep(15)
        if feedback.isCanceled():
            return {}

        # Udtræk ud fra placering
        alg_params = {
            'INPUT': parameters['dmi_grid'],
            'INTERSECT': outputs['Centroider']['OUTPUT'],
            'PREDICATE': [1],  # indeholder ('contain')
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['UdtrkUdFraPlacering'] = processing.run('native:extractbylocation', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(16)
        if feedback.isCanceled():
            return {}

        # Udtræk vha. udtryk hele opland
        # OBS: SEGMENT_ID-værdien skal sættes til det projektspecifikke ID
        alg_params = {
            'EXPRESSION': '"SEGMENT_ID" IS NOT NULL',
            'INPUT': outputs['Middelkoordinater']['OUTPUT'],
            'OUTPUT': parameters['Punkt_1']
        }
        outputs['UdtrkVhaUdtrykHeleOpland'] = processing.run('native:extractbyexpression', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Punkt_1'] = outputs['UdtrkVhaUdtrykHeleOpland']['OUTPUT']
        return results

    def name(self) -> str:
        return 'SUPERSEJMODEL'

    def displayName(self) -> str:
        return 'SUPERSEJMODEL'

    def group(self) -> str:
        return ''

    def groupId(self) -> str:
        return ''

    def createInstance(self):
        return self.__class__()
