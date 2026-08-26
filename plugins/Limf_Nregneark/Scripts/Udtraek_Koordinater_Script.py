from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingOutputNumber,
    QgsProcessingException,
)


class UdtraekKoordinaterSegmentId(QgsProcessingAlgorithm):

    INPUT          = 'INPUT'
    SEGMENT_ID_FELT = 'SEGMENT_ID_FELT'
    X_MIN_SEG      = 'X_MIN_SEG'
    Y_MIN_SEG      = 'Y_MIN_SEG'
    X_MAX_SEG      = 'X_MAX_SEG'
    Y_MAX_SEG      = 'Y_MAX_SEG'

    def name(self):
        return 'udtraek_koordinater_segment_id'

    def displayName(self):
        return 'Udtraek koordinater efter SEGMENT_ID'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return UdtraekKoordinaterSegmentId()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT,
            'Inputlag (punkter med SEGMENT_ID)'
        ))
        self.addParameter(QgsProcessingParameterField(
            self.SEGMENT_ID_FELT,
            'SEGMENT_ID felt',
            defaultValue='SEGMENT_ID',
            parentLayerParameterName=self.INPUT
        ))
        self.addOutput(QgsProcessingOutputNumber(self.X_MIN_SEG, 'X koordinat (laveste SEGMENT_ID)'))
        self.addOutput(QgsProcessingOutputNumber(self.Y_MIN_SEG, 'Y koordinat (laveste SEGMENT_ID)'))
        self.addOutput(QgsProcessingOutputNumber(self.X_MAX_SEG, 'X koordinat (hoejeste SEGMENT_ID)'))
        self.addOutput(QgsProcessingOutputNumber(self.Y_MAX_SEG, 'Y koordinat (hoejeste SEGMENT_ID)'))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        lag = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        seg_felt = self.parameterAsString(parameters, self.SEGMENT_ID_FELT, context)

        features = list(lag.getFeatures())
        if len(features) < 2:
            raise QgsProcessingException(
                f'For faa features i inputlaget - forventede mindst 2, fandt {len(features)}.'
            )

        sorteret = sorted(features, key=lambda f: f[seg_felt])

        min_punkt = sorteret[0].geometry().asPoint()
        max_punkt = sorteret[-1].geometry().asPoint()

        feedback.pushInfo(f'Laveste {seg_felt}: {sorteret[0][seg_felt]}  ->  X={min_punkt.x():.4f}, Y={min_punkt.y():.4f}')
        feedback.pushInfo(f'Hoejeste {seg_felt}: {sorteret[-1][seg_felt]}  ->  X={max_punkt.x():.4f}, Y={max_punkt.y():.4f}')

        return {
            self.X_MIN_SEG: min_punkt.x(),
            self.Y_MIN_SEG: min_punkt.y(),
            self.X_MAX_SEG: max_punkt.x(),
            self.Y_MAX_SEG: max_punkt.y(),
        }
