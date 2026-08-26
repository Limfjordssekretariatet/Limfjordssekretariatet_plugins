import processing

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSink,
    QgsFeatureRequest,
    QgsWkbTypes,
)


class UdpegMidtersteLinjer(QgsProcessingAlgorithm):

    INPUT    = 'INPUT'
    DISTANCE = 'DISTANCE'
    OUTPUT   = 'OUTPUT'

    def name(self):
        return 'udpeg_midterste_linjer'

    def displayName(self):
        return 'Udpeg midterste linjer'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return UdpegMidtersteLinjer()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT, 'Linjetema', [QgsWkbTypes.LineGeometry]
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.DISTANCE, 'Søgeafstand (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=8.0, minValue=0.1
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, 'Midterste linjer'
        ))

    def processAlgorithm(self, parameters, context, feedback):
        layer    = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        distance = self.parameterAsDouble(parameters, self.DISTANCE, context)

        feedback.pushInfo(f'Grupperer {layer.featureCount()} linjer med søgeafstand {distance}m ...')

        # Buffer + dissolve + singleparts → én polygon pr. gruppe af parallelle linjer
        buf_result = processing.run('native:buffer', {
            'INPUT':    layer,
            'DISTANCE': distance,
            'DISSOLVE': True,
            'OUTPUT':   'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        buffered = processing.run('native:multiparttosingleparts', {
            'INPUT':  buf_result,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.pushInfo(f'Fandt {buffered.featureCount()} grupper')

        selected_ids = set()
        total = buffered.featureCount()

        for i, poly in enumerate(buffered.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(int(i / total * 100))

            poly_centroid = poly.geometry().centroid().asPoint()
            request = QgsFeatureRequest().setFilterRect(poly.geometry().boundingBox())

            best_id   = None
            best_dist = float('inf')

            for line in layer.getFeatures(request):
                if not line.geometry().intersects(poly.geometry()):
                    continue
                c  = line.geometry().centroid().asPoint()
                dx = c.x() - poly_centroid.x()
                dy = c.y() - poly_centroid.y()
                d  = (dx * dx + dy * dy) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_id   = line.id()

            if best_id is not None:
                selected_ids.add(best_id)

        feedback.pushInfo(f'Valgte {len(selected_ids)} linjer')

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            layer.fields(), layer.wkbType(), layer.sourceCrs()
        )

        for line in layer.getFeatures():
            if line.id() in selected_ids:
                sink.addFeature(line)

        return {self.OUTPUT: dest_id}
