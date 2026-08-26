import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingOutputVectorLayer,
    QgsProcessingException,
)

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
from utils import find_grunddata


def find_grunddata_grid():
    """Returnerer <plugin>/Grunddata/grid/ (følger med pluginnet)."""
    grunddata = find_grunddata()
    if not grunddata:
        return None
    grid = os.path.join(grunddata, 'grid')
    return grid if os.path.isdir(grid) else None


class IndputDMIGrid(QgsProcessingAlgorithm):

    DMI_GRID = 'DMI_GRID'

    def name(self):
        return 'indput_dmi_grid'

    def displayName(self):
        return 'Indput DMI Grid'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return IndputDMIGrid()

    def initAlgorithm(self, config=None):
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.DMI_GRID,
            'DMI Grid'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        grid_mappe = find_grunddata_grid()
        if not grid_mappe:
            raise QgsProcessingException(
                'Grunddata/grid-mappen blev ikke fundet. '
                'Udpeg vedlagte mappe i interfacet først.'
            )

        sti = os.path.join(grid_mappe, '10x10km.shp')
        if not os.path.isfile(sti):
            raise QgsProcessingException(
                f'10x10km.shp blev ikke fundet i: {grid_mappe}'
            )

        feedback.pushInfo(f'10x10km fundet: {sti}')

        return {
            self.DMI_GRID: sti
        }
