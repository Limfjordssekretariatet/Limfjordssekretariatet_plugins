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
from utils import find_grunddata  # noqa: F401


class IndputDHMLinje(QgsProcessingAlgorithm):

    DHMLINJE = 'DHMLINJE'

    def name(self):
        return 'indput_dhmlinje'

    def displayName(self):
        return 'Indput DHMLinje'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return IndputDHMLinje()

    def initAlgorithm(self, config=None):
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.DHMLINJE,
            'DHMLinje'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        grunddata = find_grunddata()
        if not grunddata:
            raise QgsProcessingException(
                'Grunddata-mappen blev ikke fundet. Sørg for at QGIS-projektet er gemt i projektmappen.'
            )

        gd = _grunddata_modul()
        if not gd.sikr('DHMLinje', feedback=feedback):
            raise QgsProcessingException(
                'DHMLinje (de hydrologiske tilpasningslinjer) kunne ikke hentes. '
                'Se loggen ovenfor.')
        sti = gd.sti('DHMLinje') or os.path.join(grunddata, 'DHMLinje', 'DHMLinje.shp')
        if not os.path.isfile(sti):
            raise QgsProcessingException(
                f'DHMLinje.shp blev ikke fundet: {sti}'
            )

        feedback.pushInfo(f'DHMLinje fundet: {sti}')

        return {self.DHMLINJE: sti}


def _grunddata_modul():
    """Indlæser Scripts/grunddata.py fra DENNE plugin-kopi."""
    import importlib.util
    import sys

    rod = os.path.dirname(os.path.realpath(__file__))
    navn = '_grunddata_' + os.path.basename(os.path.dirname(rod)).lower()
    if navn in sys.modules:
        return sys.modules[navn]
    spec = importlib.util.spec_from_file_location(
        navn, os.path.join(rod, 'grunddata.py'))
    modul = importlib.util.module_from_spec(spec)
    sys.modules[navn] = modul
    spec.loader.exec_module(modul)
    return modul
