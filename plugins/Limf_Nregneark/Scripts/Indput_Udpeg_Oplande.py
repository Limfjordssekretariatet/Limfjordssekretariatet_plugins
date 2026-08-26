import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingOutputRasterLayer,
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
from utils import find_output_filer, find_resultater_mappe  # noqa: F401


def find_resultater():
    """Returnerer Resultater/ (robust over for flade mappe-skemaer) eller None."""
    return find_resultater_mappe()


class IndputUdpegOplande(QgsProcessingAlgorithm):

    HOEJDEMODEL = 'HOEJDEMODEL'

    def name(self):
        return 'indput_udpeg_oplande'

    def displayName(self):
        return 'Indput Udpeg Oplande'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return IndputUdpegOplande()

    def initAlgorithm(self, config=None):
        self.addOutput(QgsProcessingOutputRasterLayer(
            self.HOEJDEMODEL,
            'Hoejdemodel'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        output_mappe = find_output_filer()
        if not output_mappe:
            raise QgsProcessingException(
                'Outputfiler_<omraade>-mappen blev ikke fundet. '
                'Kør "Navngiv projekt" og "Tilføj dit projektområde (.shp)" i interfacet først.'
            )

        sti = None
        for filnavn in ('Hoejdemodel.tif', 'Hoejdemodel.sdat'):
            kandidat = os.path.join(output_mappe, filnavn)
            if os.path.isfile(kandidat):
                sti = kandidat
                break

        if sti is None:
            raise QgsProcessingException(
                'Højdemodellen blev ikke fundet for dette projektområde.\n\n'
                'Kør FØRST "Højdemodel"-trinnet (Download & kør, eller Brug lokal '
                '& kør) — det laver Hoejdemodel.tif. Kør derefter "Udpeg oplande".\n\n'
                f'Forventet i: {output_mappe}'
            )

        feedback.pushInfo(f'Hoejdemodel fundet: {sti}')

        return {
            self.HOEJDEMODEL: sti
        }
