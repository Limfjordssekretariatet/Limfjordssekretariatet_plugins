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
from utils import find_grunddata, find_output_filer, find_resultater_mappe  # noqa: F401


class IndputGrunddata(QgsProcessingAlgorithm):

    JORDBUND2024 = 'JORDBUND2024'
    MARKER2024 = 'MARKER2024'
    BEFAESTET_AREAL = 'BEFAESTET_AREAL'
    KLIPPE_OMRAADE = 'KLIPPE_OMRAADE'

    def name(self):
        return 'indput_grunddata'

    def displayName(self):
        return 'Indput Grunddata'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return IndputGrunddata()

    def initAlgorithm(self, config=None):
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.JORDBUND2024,
            'Jordbundskort 2024'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.MARKER2024,
            'Marker 2024'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.BEFAESTET_AREAL,
            'Befaestet Areal'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.KLIPPE_OMRAADE,
            'Klippe Omraade'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        grunddata = find_grunddata()
        if not grunddata:
            raise QgsProcessingException(
                'Grunddata-mappen blev ikke fundet. Sørg for at QGIS-projektet er gemt i projektmappen.'
            )

        output_filer = find_output_filer()
        if not output_filer:
            raise QgsProcessingException(
                'Outputfiler_<omraade>-mappen blev ikke fundet. '
                'Kør "Navngiv projekt" og "Tilføj dit projektområde (.shp)" i interfacet først.'
            )

        # De tre store datasaet foelger ikke med pluginnet — de hentes fra en
        # release foerste gang, og ligger derefter i QGIS-profilen.
        gd = _grunddata_modul()
        if not gd.sikr(['Jordbundskort_2024', 'Marker_2024', 'Befaestet_Areal'],
                       feedback=feedback):
            raise QgsProcessingException(
                'Referencedata (jordbund, marker, befæstet areal) kunne ikke '
                'hentes. Uden dem kan grunddata ikke klippes til '
                'projektområdet. Se loggen ovenfor.')

        lag = {
            self.JORDBUND2024: gd.sti('Jordbundskort_2024'),
            self.MARKER2024: gd.sti('Marker_2024'),
            self.BEFAESTET_AREAL: gd.sti('Befaestet_Areal'),
            self.KLIPPE_OMRAADE: os.path.join(output_filer, 'Klippe_Omraade.gpkg'),
        }

        for navn, sti in lag.items():
            if not sti or not os.path.isfile(sti):
                raise QgsProcessingException(
                    f'{navn} blev ikke fundet: {sti}'
                )
            feedback.pushInfo(f'{navn} fundet: {sti}')

        return lag


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
