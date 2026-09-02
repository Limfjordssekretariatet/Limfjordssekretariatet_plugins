from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFile,
    QgsProcessingOutputNumber,
    QgsProcessingException,
    QgsProcessing,
    QgsDistanceArea,
    QgsProject,
    QgsUnitTypes,
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

ARK_NAVN       = '1. Tilførsel'
CELLE          = 'C27'


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


class IndsaetArealVandloeb(QgsProcessingAlgorithm):

    INPUT     = 'INPUT'
    EXCEL_STI = 'EXCEL_STI'
    AREAL_HA  = 'AREAL_HA'

    def name(self):
        return 'indsaet_areal_vandloeb_i_excel'

    def displayName(self):
        return 'Indsæt areal i Excel (vandløbsopland)'

    def group(self):
        return 'DMI Grid'

    def groupId(self):
        return 'dmigrid'

    def createInstance(self):
        return IndsaetArealVandloeb()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                'Vandløbsopland-lag',
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
            QgsProcessingOutputNumber(
                self.AREAL_HA,
                'Areal (ha)',
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        lag       = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        excel_sti = self.parameterAsFile(parameters, self.EXCEL_STI, context)

        if lag is None:
            raise QgsProcessingException('Vandløbsopland-lag kunne ikke indlæses.')

        # Slå Resultat_<omraade>.xlsx op automatisk hvis brugeren ikke har angivet
        # en fil — eller den angivne fil ikke findes (fx en hårdkodet sti i modellen).
        if not excel_sti or not os.path.isfile(excel_sti):
            auto = find_regneark()
            if auto:
                excel_sti = auto
                feedback.pushInfo(f'Regneark slået op automatisk: {excel_sti}')
            else:
                raise QgsProcessingException(
                    'Resultat_<omraade>.xlsx blev ikke fundet.\n\n'
                    'Kør "Projektområde" i interfacet først — det opretter Excel-filen.\n'
                    'Tjek også at mappe, projektnavn og projektområde er udfyldt i boks 1.'
                )

        # Tjek eksplicit at stien er en fil (ikke en mappe eller tom streng)
        if not excel_sti or not os.path.isfile(excel_sti):
            raise QgsProcessingException(
                f'Excel-filen blev ikke fundet:\n{excel_sti}'
            )

        # Advar hvis filen er åben i Excel (låsefil ~$...)
        laase_fil = os.path.join(
            os.path.dirname(excel_sti),
            '~$' + os.path.basename(excel_sti)
        )
        if os.path.isfile(laase_fil):
            feedback.pushWarning(
                'Excel-filen ser ud til at være åben i Excel (~$-låsefil fundet). '
                'Luk filen i Excel og kør modellen igen for at undgå konflikter.'
            )

        # ── Beregn areal i hektar ─────────────────────────────────────────────
        calc = QgsDistanceArea()
        calc.setSourceCrs(lag.crs(), QgsProject.instance().transformContext())
        calc.setEllipsoid(QgsProject.instance().ellipsoid())

        total_m2 = 0.0
        for feature in lag.getFeatures():
            total_m2 += calc.measureArea(feature.geometry())

        areal_ha = round(
            calc.convertAreaMeasurement(total_m2, QgsUnitTypes.AreaHectares), 2
        )

        feedback.pushInfo(f'Areal beregnet: {areal_ha} ha')
        if not areal_ha:
            feedback.pushWarning(
                'Vandløbsoplandet er 0 ha — der løber intet kortlagt vandløb ind i '
                'projektområdet. Nul skrives i regnearket; hele oplandet står som '
                'direkte opland.')

        if feedback.isCanceled():
            return {}

        # ── Skriv til Excel via openpyxl ──────────────────────────────────────
        excel_abs = os.path.abspath(excel_sti)

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
            wb[ARK_NAVN][CELLE] = areal_ha
            wb.save(excel_abs)
        finally:
            wb.close()

        feedback.pushInfo(f"Indsat '{areal_ha} ha' i {ARK_NAVN} → {CELLE}")

        return {self.AREAL_HA: areal_ha}
