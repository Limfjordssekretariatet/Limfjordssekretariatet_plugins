from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFile,
    QgsProcessingOutputString,
    QgsProcessingException,
    QgsProcessing,
)
import os

# Sørg for at scriptets egen mappe er på sys.path, så 'utils' altid kan importeres.
import os as _os, sys as _sys
try:
    _d = _os.path.dirname(_os.path.abspath(__file__))
    if _d and _d not in _sys.path:
        _sys.path.insert(0, _d)
except NameError:
    pass
from utils import find_resultater_mappe, get_resultat_excel_navn

ARK_NAVN      = '1. Tilførsel'
CELLE         = 'B16'
GRID_FELT     = 'cellId'
PRÆFIKS       = '10km_'


def get_resultat_regneark_navn():
    """Returnerer dynamisk navn for resultat-regnearket (Resultat_<omraade>.xlsx)."""
    return get_resultat_excel_navn()


def find_regneark():
    """Returnerer Resultater/Resultat_<omraade>.xlsx (robust over for flade
    mappe-skemaer) eller None hvis filen ikke findes."""
    resultater = find_resultater_mappe()
    if not resultater:
        return None
    sti = os.path.join(resultater, get_resultat_excel_navn())
    return sti if os.path.isfile(sti) else None


class IndsaetGridnummerVandloeb(QgsProcessingAlgorithm):

    INPUT      = 'INPUT'
    EXCEL_STI  = 'EXCEL_STI'
    GRIDNUMMER = 'GRIDNUMMER'

    def name(self):
        return 'indsaet_gridnummer_vandloeb_i_excel'

    def displayName(self):
        return 'Indsæt gridnummer i Excel (vandløbsopland)'

    def group(self):
        return 'DMI Grid'

    def groupId(self):
        return 'dmigrid'

    def createInstance(self):
        return IndsaetGridnummerVandloeb()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                'Udtrukket DMI-grid lag',
                types=[QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.EXCEL_STI,
                'Excel-fil (Resultat_<omraade>.xlsx) — valgfri, slås op automatisk',
                extension='xlsx',
                optional=True,
            )
        )
        self.addOutput(
            QgsProcessingOutputString(
                self.GRIDNUMMER,
                'DMI-gridnummer',
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        lag = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        excel_sti = self.parameterAsFile(parameters, self.EXCEL_STI, context)

        # Slå Resultat_<omraade>.xlsx op automatisk hvis brugeren ikke har angivet
        # en fil — eller den angivne fil ikke findes (fx en hårdkodet sti i modellen).
        if not excel_sti or not os.path.isfile(excel_sti):
            auto = find_regneark()
            if auto:
                excel_sti = auto
                feedback.pushInfo(f'Regneark slået op automatisk: {excel_sti}')

        features = list(lag.getFeatures())
        if not features:
            # Uden vandloebsopland er der ingen DMI-celle at slaa op. Det er ikke
            # en fejl: arealet er nul, og nedboeren indgaar ikke i regnestykket.
            # At stoppe her ville forhindre resten af regnearket i at blive fyldt ud.
            feedback.pushWarning(
                'Ingen DMI-celle rammer vandløbsoplandet — det er 0 ha, fordi der '
                'ikke løber et kortlagt vandløb ind i projektområdet. '
                f'Cellen {CELLE} i "{ARK_NAVN}" lades urørt; vandløbsoplandets '
                'areal skrives som nul.')
            return {}

        gridnummer = features[0][GRID_FELT]
        if isinstance(gridnummer, str) and gridnummer.startswith(PRÆFIKS):
            gridnummer = gridnummer[len(PRÆFIKS):]

        feedback.pushInfo(f'Gridnummer: {gridnummer}')

        if feedback.isCanceled():
            return {}

        # Skriv til Excel via openpyxl
        excel_abs = os.path.abspath(excel_sti)
        if not os.path.exists(excel_abs):
            raise QgsProcessingException(f'Excel-filen blev ikke fundet:\n{excel_abs}')

        try:
            import openpyxl
        except ImportError:
            raise QgsProcessingException(
                'openpyxl er ikke installeret. Kør i OSGeo4W Shell: pip install openpyxl'
            )

        wb = openpyxl.load_workbook(excel_abs)
        try:
            if ARK_NAVN not in wb.sheetnames:
                raise QgsProcessingException(
                    f'Arket "{ARK_NAVN}" findes ikke i {excel_abs}'
                )
            wb[ARK_NAVN][CELLE] = gridnummer
            wb.save(excel_abs)
        finally:
            wb.close()

        feedback.pushInfo(f"Indsat '{gridnummer}' i {CELLE}")

        return {self.GRIDNUMMER: str(gridnummer)}
