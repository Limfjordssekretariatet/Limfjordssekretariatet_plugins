import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingOutputVectorLayer,
    QgsProcessingException,
    QgsProject,
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


class IndputDirekteOpland(QgsProcessingAlgorithm):

    DIREKTE_OPLAND = 'DIREKTE_OPLAND'

    def name(self):
        return 'indput_direkte_opland'

    def displayName(self):
        return 'Indput Direkte Opland'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return IndputDirekteOpland()

    def initAlgorithm(self, config=None):
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.DIREKTE_OPLAND,
            'Direkte Opland'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        output_mappe = find_output_filer()
        if not output_mappe:
            raise QgsProcessingException(
                'Outputfiler_<omraade>-mappen blev ikke fundet. '
                'Kør "Navngiv projekt" og "Tilføj dit projektområde (.shp)" i interfacet først.'
            )

        sti = os.path.join(output_mappe, 'Direkte_Opland.gpkg')
        if not os.path.isfile(sti):
            raise QgsProcessingException(
                f'Direkte_Opland.gpkg blev ikke fundet i: {output_mappe}'
            )

        feedback.pushInfo(f'Direkte_Opland fundet: {sti}')

        return {
            self.DIREKTE_OPLAND: sti
        }
