import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterVectorLayer,
    QgsProcessingOutputVectorLayer,
    QgsProcessingException,
    QgsField,
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsCoordinateTransformContext,
    QgsProject,
)
from qgis.PyQt.QtCore import QVariant

# Sørg for at scriptets egen mappe er på sys.path, så 'utils' altid kan
# importeres uanset hvordan QGIS' script-provider loader filen.
import os as _os, sys as _sys
try:
    _d = _os.path.dirname(_os.path.abspath(__file__))
    if _d and _d not in _sys.path:
        _sys.path.insert(0, _d)
except NameError:
    pass  # __file__ ikke defineret i denne load-kontekst
from utils import find_resultater_mappe, get_resultat_excel_navn as _get_resultat_excel_navn  # noqa: F401


def _fil_er_laast(sti):
    """Returnerer True hvis filen ikke kan åbnes til skrivning (typisk åben i Excel)."""
    try:
        with open(sti, 'r+b'):
            return False
    except (PermissionError, OSError):
        return True


def _excel_read_worker(excel_sti, mapping, resultat):
    """Kører i baggrundstråd. Læser celleværdier via Excel COM (Excel evaluerer formler live).

    resultat: liste hvor (ok: bool, vaerdier_eller_fejl: dict|str) appendes.
    """
    import pythoncom
    pythoncom.CoInitialize()
    excel = None
    wb = None
    try:
        import win32com.client as win32
        excel = win32.DispatchEx('Excel.Application')
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.Interactive = False
        excel.EnableEvents = False
        excel.AskToUpdateLinks = False
        try:
            excel.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        except Exception:
            pass
        wb = excel.Workbooks.Open(
            os.path.abspath(excel_sti),
            UpdateLinks=0,
            ReadOnly=True,
            IgnoreReadOnlyRecommended=True,
            Notify=False,
            AddToMru=False,
        )
        excel.CalculateFull()

        ark_cache = {}
        vaerdier = {}
        for felt, (ark_navn, celle, _beskrivelse) in mapping.items():
            if ark_navn not in ark_cache:
                ark_cache[ark_navn] = wb.Sheets(ark_navn)
            vaerdier[felt] = ark_cache[ark_navn].Range(celle).Value
        resultat.append((True, vaerdier))
    except Exception as e:
        resultat.append((False, f'{type(e).__name__}: {e}'))
    finally:
        try:
            if wb is not None:
                wb.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            if excel is not None:
                excel.Quit()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def laes_via_excel_com(excel_sti, mapping, feedback, timeout=45):
    """Læser celleværdier direkte via Excel COM. Returnerer dict eller None ved fejl.

    Why: openpyxl evaluerer ikke formler. Excel COM = ægte Excel, så formler
    bliver beregnet live uanset om cachen er tom eller ej.
    """
    if _fil_er_laast(excel_sti):
        feedback.pushWarning(
            f'Excel-filen er åben i et andet program: {excel_sti}\n'
            'Luk Excel (også baggrundsprocesser i Task Manager) og kør igen.'
        )
        return None

    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
    except ImportError:
        feedback.pushWarning(
            'pywin32 er ikke installeret — kan ikke læse Excel via COM. '
            'Installer via OSGeo4W Shell: pip install pywin32'
        )
        return None

    import threading
    feedback.pushInfo(f'Læser Excel-værdier via Excel COM (timeout: {timeout}s)…')
    resultat = []
    t = threading.Thread(
        target=_excel_read_worker,
        args=(excel_sti, mapping, resultat),
        daemon=True,
    )
    t.start()
    t.join(timeout)

    if t.is_alive():
        feedback.pushWarning(
            f'Excel COM overskred timeout ({timeout}s) — fortsætter med fallback. '
            'Tjek Task Manager for hængende EXCEL.EXE-processer.'
        )
        return None

    if not resultat:
        feedback.pushWarning('Excel COM gav intet resultat — fortsætter med fallback.')
        return None

    ok, data = resultat[0]
    if ok:
        feedback.pushInfo(f'Læste {len(data)} celleværdier via Excel COM.')
        return data

    feedback.pushWarning(f'Excel COM fejlede ({data}) — fortsætter med fallback.')
    return None


# Korte feltnavne (max 10 tegn — shapefile-kompatibelt) og deres mapping
# til Excel-celler. Beskrivelse er kun til log/dokumentation.
# Format: kort_navn -> (ark, celle, beskrivelse)
CELLE_MAPPING = {
    'VOAreal_ha': ('1. Tilførsel', 'C27', 'Vandopland areal (ha)'),
    'VOKgNtb_ha': ('1. Tilførsel', 'C30', 'Vandopland Kg Ntab/ha'),
    'VOTotNtab':  ('1. Tilførsel', 'C32', 'Vandopland samlet Kg Ntab'),
    'DOAreal_ha': ('1. Tilførsel', 'C48', 'Direkte opland areal (ha)'),
    'DOKgNtb_ha': ('1. Tilførsel', 'C51', 'Direkte opland Kg Ntab/ha'),
    'DOTotNtab':  ('1. Tilførsel', 'C53', 'Direkte opland samlet Kg Ntab'),
    'POUdAgerKg': ('1. Tilførsel', 'C73', 'Projektomraade Kg N ift. ager'),
    'POUdBrakKg': ('1. Tilførsel', 'C74', 'Projektomraade Kg N ift. brak'),
    'POUdGrsKg':  ('1. Tilførsel', 'C75', 'Projektomraade Kg N ift. græs'),
    'POUdNatKg':  ('1. Tilførsel', 'C76', 'Projektomraade Kg N ift. natur'),
    'POUdBefKg':  ('1. Tilførsel', 'C77', 'Projektomraade Kg N ift. befæstet'),
    'POUdSamKg':  ('1. Tilførsel', 'C79', 'Projektomraade samlet Kg N'),
    'OvSvKgHaDg': ('2. Omsætning', 'D32', 'Oversvømmelse Kg N/ha/døgn'),
    'OvSvTotKg':  ('2. Omsætning', 'D34', 'Oversvømmelse total Kg N'),
    'OvRsNOmsP':  ('2. Omsætning', 'D44', 'Overrisling N-omsætningsprocent'),
    'OvRsTotKg':  ('2. Omsætning', 'D46', 'Overrisling total Kg N'),
    'EkstPOKgN':  ('2. Omsætning', 'D59', 'Ekstensivering af landbrug Kg N'),
    'TotNRedKg':  ('2. Omsætning', 'D69', 'Samlet N-reduktion Kg N'),
    'TotNRdKgHa': ('2. Omsætning', 'D72', 'Samlet N-reduktion Kg N/ha'),
}


class IndputRegnearkData(QgsProcessingAlgorithm):

    PROJEKTOMRAADE = 'PROJEKTOMRAADE'
    OUTPUT = 'OUTPUT'

    def name(self):
        return 'indput_regneark_data'

    def displayName(self):
        return 'Indput Regneark Data'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return IndputRegnearkData()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.PROJEKTOMRAADE,
            'Projektomraade'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.OUTPUT,
            'Projektomraade'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        try:
            import openpyxl
        except ImportError:
            raise QgsProcessingException(
                'openpyxl er ikke installeret. Installer det via OSGeo4W Shell: pip install openpyxl'
            )

        lag = self.parameterAsVectorLayer(parameters, self.PROJEKTOMRAADE, context)
        if not lag:
            raise QgsProcessingException('Projektomraade-laget kunne ikke indlæses.')

        # Brug filnavnet (uden extension) som base — ikke lag.name(), som kan
        # indeholde QGIS-præfiks (fx "Projektomraade_<navn>") og dermed give
        # mismatch med interfacets sti-lookup i _kor_retuner().
        uri = lag.dataProvider().dataSourceUri().split('|')[0]
        lag_navn = os.path.splitext(os.path.basename(uri))[0]
        feedback.pushInfo(f'Input-lag: {lag.name()}  |  Filnavn-base: {lag_navn}  |  Kilde: {uri}')

        resultater_mappe = find_resultater_mappe()
        if not resultater_mappe:
            raise QgsProcessingException(
                'Resultater-mappen blev ikke fundet. '
                'Kør "Navngiv projekt" og "Tilføj dit projektområde (.shp)" i interfacet først.'
            )

        excel_filnavn = _get_resultat_excel_navn()
        excel_sti = os.path.join(resultater_mappe, excel_filnavn)
        if not os.path.isfile(excel_sti):
            raise QgsProcessingException(
                f'Excel-filen "{excel_filnavn}" blev ikke fundet i: {resultater_mappe}'
            )

        # Kopiér input-shapefilen til Resultater-mappen — originalen røres ikke.
        kopi_navn = f'Resultat_{lag_navn}.shp'
        kopi_sti = os.path.join(resultater_mappe, kopi_navn)
        feedback.pushInfo(f'Kopierer shapefile til: {kopi_sti}')

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'ESRI Shapefile'
        options.fileEncoding = 'UTF-8'
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

        skriv_fejl, *_rest = QgsVectorFileWriter.writeAsVectorFormatV2(
            lag, kopi_sti, QgsCoordinateTransformContext(), options
        )
        skriv_besked = _rest[0] if _rest else ''
        if skriv_fejl != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(
                f'Kunne ikke kopiere shapefilen: {skriv_besked}'
            )

        # Indlæs kopien som et nyt lag — det er kopien vi skriver Excel-værdier til.
        kopi_lag = QgsVectorLayer(kopi_sti, f'Resultat_{lag_navn}', 'ogr')
        if not kopi_lag.isValid():
            raise QgsProcessingException(
                f'Kopien kunne ikke indlæses som lag: {kopi_sti}'
            )

        # Primær: læs via Excel COM (Excel evaluerer formler live).
        # Fallback: openpyxl med data_only (læser kun cachede formelværdier).
        raa_vaerdier = laes_via_excel_com(excel_sti, CELLE_MAPPING, feedback)

        if raa_vaerdier is None:
            feedback.pushInfo(f'Falder tilbage til openpyxl: {excel_sti}')
            wb = openpyxl.load_workbook(excel_sti, data_only=True)
            ark_cache = {}
            raa_vaerdier = {}
            for felt, (ark_navn, celle, _b) in CELLE_MAPPING.items():
                if ark_navn not in ark_cache:
                    ark_cache[ark_navn] = wb[ark_navn]
                raa_vaerdier[felt] = ark_cache[ark_navn][celle].value

        # Konverter til float / NULL
        vaerdier = {}
        for felt, (ark_navn, celle, beskrivelse) in CELLE_MAPPING.items():
            raav = raa_vaerdier.get(felt)
            if raav is None or (isinstance(raav, str) and (raav.startswith('#') or raav == '')):
                feedback.pushWarning(
                    f'{felt}: ugyldig værdi i {ark_navn}!{celle} ("{raav}") — sættes til NULL'
                )
                vaerdier[felt] = None
            else:
                try:
                    vaerdier[felt] = float(raav)
                except (TypeError, ValueError):
                    feedback.pushWarning(
                        f'{felt}: kunne ikke konvertere "{raav}" til tal — sættes til NULL'
                    )
                    vaerdier[felt] = None
            feedback.pushInfo(f'  {felt} ({ark_navn}!{celle}) = {vaerdier[felt]}  [{beskrivelse}]')

        # Tilføj manglende felter til kopien
        manglende = [
            QgsField(felt, QVariant.Double, 'double', 10, 3)
            for felt in CELLE_MAPPING
            if kopi_lag.fields().indexOf(felt) == -1
        ]
        if manglende:
            provider = kopi_lag.dataProvider()
            if not provider.addAttributes(manglende):
                raise QgsProcessingException(
                    'Kunne ikke tilføje felter til kopi-laget.'
                )
            kopi_lag.updateFields()
            feedback.pushInfo(f'{len(manglende)} nye felter tilføjet til kopien.')
        else:
            feedback.pushInfo('Alle felter findes allerede på kopien.')

        # Verificer at alle feltnavne kan findes EFTER tilføjelse
        # (shapefile trunkerer feltnavne >10 tegn stille)
        felt_indeks = {}
        for felt in CELLE_MAPPING:
            idx = kopi_lag.fields().indexOf(felt)
            if idx == -1:
                raise QgsProcessingException(
                    f'Feltet "{felt}" ({len(felt)} tegn) findes ikke på kopien efter tilføjelse. '
                    f'Shapefile-feltnavne skal være ≤10 tegn.'
                )
            felt_indeks[felt] = idx

        # Skriv værdier via edit-session
        if not kopi_lag.startEditing():
            raise QgsProcessingException('Kunne ikke starte edit-session på kopien.')

        try:
            antal = 0
            for feature in kopi_lag.getFeatures():
                fid = feature.id()
                for felt, idx in felt_indeks.items():
                    if not kopi_lag.changeAttributeValue(fid, idx, vaerdier[felt]):
                        feedback.pushWarning(
                            f'changeAttributeValue fejlede: feature {fid}, felt {felt}'
                        )
                antal += 1

            if not kopi_lag.commitChanges():
                fejl = kopi_lag.commitErrors()
                kopi_lag.rollBack()
                raise QgsProcessingException(f'Commit fejlede: {fejl}')

            feedback.pushInfo(f'Skrev værdier til {antal} feature(s) i kopien og gemte ændringerne.')
        except Exception:
            kopi_lag.rollBack()
            raise

        # Sørg eksplicit for at kopien indlæses i QGIS-projektet når modellen er færdig.
        try:
            details = QgsProcessingContext.LayerDetails(
                f'Resultat_{lag_navn}', QgsProject.instance(), self.OUTPUT
            )
            context.addLayerToLoadOnCompletion(kopi_sti, details)
        except Exception as e:
            feedback.pushWarning(f'Kunne ikke registrere lag til auto-load ({e}).')

        return {self.OUTPUT: kopi_sti}
