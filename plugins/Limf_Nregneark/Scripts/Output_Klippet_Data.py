import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterVectorLayer,
    QgsProcessingOutputVectorLayer,
    QgsProcessingException,
    QgsProject,
    QgsVectorFileWriter,
    QgsCoordinateTransformContext,
)


# Sørg for at scriptets egen mappe er på sys.path, så 'utils' altid kan
# importeres uanset hvordan QGIS' script-provider loader filen.
import os as _os, sys as _sys
try:
    _d = _os.path.dirname(_os.path.abspath(__file__))
    if _d and _d not in _sys.path:
        _sys.path.insert(0, _d)
except NameError:
    pass  # __file__ ikke defineret i denne load-kontekst
from utils import find_output_filer  # noqa: F401


class OutputKlippetData(QgsProcessingAlgorithm):

    JORDBUND2024_KLIPPET = 'JORDBUND2024_KLIPPET'
    MARKER2024_KLIPPET = 'MARKER2024_KLIPPET'
    BEFAESTET2024_KLIPPET = 'BEFAESTET2024_KLIPPET'
    OMDRIFTSAREALER_KLIPPET = 'OMDRIFTSAREALER_KLIPPET'
    NATUR_KLIPPET = 'NATUR_KLIPPET'

    OUTPUT_JORDBUND = 'OUTPUT_JORDBUND'
    OUTPUT_MARKER = 'OUTPUT_MARKER'
    OUTPUT_BEFAESTET = 'OUTPUT_BEFAESTET'
    OUTPUT_OMDRIFTSAREALER = 'OUTPUT_OMDRIFTSAREALER'
    OUTPUT_NATUR = 'OUTPUT_NATUR'

    def name(self):
        return 'output_klippet_data'

    def displayName(self):
        return 'Output Klippet Data'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return OutputKlippetData()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.JORDBUND2024_KLIPPET,
            'Jordbund 2024 Klippet'
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.MARKER2024_KLIPPET,
            'Marker 2024 Klippet'
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.BEFAESTET2024_KLIPPET,
            'Befaestet 2024 Klippet'
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.OMDRIFTSAREALER_KLIPPET,
            'Omdriftsarealer Klippet'
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.NATUR_KLIPPET,
            'Natur Klippet'
        ))

        self.addOutput(QgsProcessingOutputVectorLayer(
            self.OUTPUT_JORDBUND,
            'Jordbund_Klippet (gemt)'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.OUTPUT_MARKER,
            'Marker_klippet (gemt)'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.OUTPUT_BEFAESTET,
            'Befaestet_Areal (gemt)'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.OUTPUT_OMDRIFTSAREALER,
            'Omdriftsarealer (gemt)'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.OUTPUT_NATUR,
            'Natur (gemt)'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        output_mappe = find_output_filer()
        if not output_mappe:
            raise QgsProcessingException(
                'Outputfiler_<omraade>-mappen blev ikke fundet. '
                'Kør "Navngiv projekt" og "Tilføj dit projektområde (.shp)" i interfacet først.'
            )

        lag_map = {
            self.JORDBUND2024_KLIPPET:    ('Jordbund_Klippet.gpkg',    self.OUTPUT_JORDBUND),
            self.MARKER2024_KLIPPET:      ('Marker_klippet.gpkg',      self.OUTPUT_MARKER),
            self.BEFAESTET2024_KLIPPET:   ('Befaestet_Areal.gpkg',     self.OUTPUT_BEFAESTET),
            self.OMDRIFTSAREALER_KLIPPET: ('Omdriftsarealer.gpkg',     self.OUTPUT_OMDRIFTSAREALER),
            self.NATUR_KLIPPET:           ('Natur.gpkg',               self.OUTPUT_NATUR),
        }

        resultater = {}
        transform_context = QgsCoordinateTransformContext()

        for param_navn, (filnavn, output_navn) in lag_map.items():
            lag = self.parameterAsVectorLayer(parameters, param_navn, context)
            if lag is None:
                raise QgsProcessingException(f'{param_navn} er ikke et gyldigt lag.')

            sti = os.path.join(output_mappe, filnavn)

            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            options.fileEncoding = 'UTF-8'

            fejl, fejlbesked = QgsVectorFileWriter.writeAsVectorFormatV2(
                lag, sti, transform_context, options
            )
            if fejl != QgsVectorFileWriter.NoError:
                raise QgsProcessingException(
                    f'Fejl ved gem af {filnavn}: {fejlbesked}'
                )

            feedback.pushInfo(f'{filnavn} gemt: {sti}')
            resultater[output_navn] = sti

        return resultater
