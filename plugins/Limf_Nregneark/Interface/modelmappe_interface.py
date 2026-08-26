"""
Modelmappe Interface til QGIS 3.4
Starter interfacet via QGIS Python-konsol:

    import importlib, sys
    sys.path.insert(0, r'C:/Users/User/Documents/Vaadomraade_Modeller/Interface')
    import modelmappe_interface
    importlib.reload(modelmappe_interface)
    modelmappe_interface.run()
"""

import os
import sys

from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox, QLineEdit
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsRasterLayer, QgsProject, QgsProcessingFeedback, QgsApplication


# Sjove "vent venligst"-beskeder der roterer mens en model kører. De skifter
# med jævne mellemrum, så brugeren kan se at der stadig arbejdes — også når
# fremskridts-bjælken ikke bevæger sig.
_VENT_BESKEDER = [
    'Laver kommunal sagsbehandling',
    'Søger projektforlængelse',
    'Overtaler lodsejer',
    'Søger tilsagnsforhøjelse',
    'Afventer høringssvar',
    'Kigger efter birkemus',
    'Nedsætter hurtigtarbejdende udvalg',
    'Snakker ved kaffemaskinen',
    'Venter på underskrift fra chefen',
    'Tilføjer Thorstens konstant',
]


class _InterfaceFeedback(QgsProcessingFeedback):
    """Videresender model-fremskridt til en QProgressDialog.

    VIGTIGT: Vi kalder ALDRIG QgsApplication.processEvents() her. Modellerne
    kalder selv processing.run() internt (nestede kørsler), og at pumpe Qt-
    events midt i en sådan kørsel kan udløse en access violation / crash.
    I stedet roterer vi blot vent-beskeden og opdaterer bjælken — Qt maler selv
    dialogen ved de naturlige punkter hvor QGIS kalder disse callbacks."""

    def __init__(self, dialog):
        super().__init__()
        self._dialog = dialog
        self._last_pct = -1
        self._ubestemt = True  # starter i animeret tilstand
        # Beregningen melder forbehold undervejs — kantkontaminering, fejlede
        # QA-tjek, lav træfprocent mod de kortlagte vandløb. De må ikke forsvinde:
        # dialogen viser kun vent-beskeder, og loggen ser brugeren ikke. Derfor
        # samles de her og vises når trinnet er færdigt.
        self.advarsler = []
        self.fejl = []
        # Nøgletallene — hvilket grundlag der blev brugt, hvad oplandet blev,
        # hvor godt nettet rammer de kortlagte vandløb. De er ikke advarsler,
        # men de er det eneste brugeren reelt skal se af beregningens output.
        self.noegletal = []
        # Egen tilfældigheds-generator, seedet pr. kørsel, så rækkefølgen af
        # beskeder varierer fra gang til gang (og ikke altid starter ens).
        import random
        self._rng = random.Random()
        self._besked_rest = []

    def _roter_besked(self):
        if not self._besked_rest:
            self._besked_rest = list(_VENT_BESKEDER)
            self._rng.shuffle(self._besked_rest)
        self._dialog.setLabelText(self._besked_rest.pop() + ' — vent venligst')

    def setProgress(self, progress):
        # Roter KUN vent-beskeden ved hvert fremskridts-kald.
        #
        # VIGTIGT: Vi kalder ALDRIG self._dialog.setValue(pct) her. QProgressDialog
        # i bestemt tilstand pumper Qt's event-loop ved setValue() — og det er
        # FATALT (access violation) når kaldet sker midt i en processing.run().
        # Især SAGA-algoritmer (sagang:*) kalder setProgress() i en tæt løkke
        # under kørslen, og hvert setValue ville pumpe events og crashe QGIS.
        # Derfor bliver dialogen i animeret/ubestemt tilstand (range 0,0) hele
        # vejen igennem, og vi opdaterer kun label-teksten.
        self._roter_besked()

    def pushInfo(self, info):
        # Modellens egne beskeder ignoreres — vi viser kun vores vent-beskeder.
        # Undtaget er nøgletallene, som samles op og vises til sidst.
        tekst = str(info)
        if tekst.startswith('▸ '):
            self.noegletal.append(tekst[len('▸ '):].strip())
        self._roter_besked()

    def setProgressText(self, text):
        self._roter_besked()

    def pushWarning(self, advarsel):
        self.advarsler.append(str(advarsel))
        self._roter_besked()

    def reportError(self, fejl, fatalError=False):
        self.fejl.append(str(fejl))
        self._roter_besked()

    def isCanceled(self):
        return self._dialog.wasCanceled()

# Plugin-mappen (rod) og dens faste undermapper. Scripts, Modeller og Grunddata
# følger ALTID med selve pluginnet, så de er tilgængelige uanset hvilken
# outputmappe brugeren vælger. Den valgte mappe bruges KUN til output/resultater.
_INTERFACE_DIR = os.path.dirname(os.path.realpath(__file__))
_PLUGIN_ROOT   = os.path.dirname(_INTERFACE_DIR)
_PLUGIN_UI        = os.path.join(_INTERFACE_DIR, 'modelmappe_interface.ui')
_PLUGIN_SCRIPTS   = os.path.join(_PLUGIN_ROOT, 'Scripts')
_PLUGIN_MODELLER  = os.path.join(_PLUGIN_ROOT, 'Modeller')
_PLUGIN_GRUNDDATA = os.path.join(_PLUGIN_ROOT, 'Grunddata')

# Standardplacering hvor data gemmes, hvis brugeren ikke selv har valgt en.
# Forudfyldes i feltet ved første åbning og kan til enhver tid ændres.
_STANDARD_MAPPE = r'C:\temp'

# TESTMILJØ: hardcodet Dataforsyningen-token så det ikke skal indtastes hver gang.
# FJERN dette før pluginnet deles/committes offentligt — tokenet er en hemmelighed.
_STANDARD_TOKEN = 'beb3a5dbb9713dd74c1ef7c24e819faa'


def _find_ui_fil():
    """UI-filen ligger altid sammen med pluginnet."""
    return _PLUGIN_UI


# Translitteration af danske/ikke-ASCII-tegn → ASCII. SAGA (9.10.2 i QGIS 3.40)
# kan IKKE læse stier med æ/ø/å m.fl. — de forvanskes (fx 'rødding' → 'r+_dding')
# og SAGA-trinnene fejler ("[Error] input file … Elevation"). Vi transлитererer
# derfor projekt-/mappenavne til ASCII, så alle output-stier er SAGA-sikre.
_ASCII_KORT = {
    'æ': 'ae', 'ø': 'oe', 'å': 'aa',
    'Æ': 'Ae', 'Ø': 'Oe', 'Å': 'Aa',
    'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss',
    'Ä': 'Ae', 'Ö': 'Oe', 'Ü': 'Ue',
    'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
    'á': 'a', 'à': 'a', 'â': 'a',
    'í': 'i', 'ì': 'i', 'î': 'i',
    'ó': 'o', 'ò': 'o', 'ô': 'o',
    'ú': 'u', 'ù': 'u', 'û': 'u',
    'ç': 'c', 'ñ': 'n',
}


def _til_ascii(s):
    """Erstat danske/ikke-ASCII-tegn med ASCII-ækvivalenter (æ→ae, ø→oe, å→aa …).

    Bruges så SAGA kan læse output-stier. Ukendte ikke-ASCII-tegn fjernes helt via
    en afsluttende ASCII-filtrering, så der aldrig havner tegn SAGA ikke kan håndtere
    i en sti."""
    import unicodedata
    out = ''.join(_ASCII_KORT.get(c, c) for c in s)
    # Fjern eventuelle resterende ikke-ASCII-tegn (accenter mv.) ved at dekomponere
    # og smide ikke-ASCII væk.
    out = unicodedata.normalize('NFKD', out)
    return out.encode('ascii', 'ignore').decode('ascii')


def _sanitize_naam(s):
    """Gør et navn til et sikkert fil/mappenavn: translitterér til ASCII (SAGA-sikkert)
    og fjern Windows-ugyldige tegn."""
    s = _til_ascii(s)
    return ''.join(c for c in s if c not in '<>:"/\\|?*').strip()


class ModelMappeDialog(QtWidgets.QDialog):
    """Hoved-dialog for Modelmappe Interface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Indlæs UI-filen frisk ved hver oprettelse fra projektmappen hvis muligt
        uic.loadUi(_find_ui_fil(), self)
        self.setWindowTitle("Vandprojekter – N-regneark")

        # Ikonet i titellinjen er det samme som paa vaerktoejslinjen og i
        # pluginlisten — det ligger i plugin-roden.
        _ikon_sti = os.path.join(_PLUGIN_ROOT, 'icon.png')
        if os.path.isfile(_ikon_sti):
            self.setWindowIcon(QIcon(_ikon_sti))

        # Forbind knapper
        self.btnVaelgFil.clicked.connect(self._vaelg_projektomraade_fil)
        self.btnVaelgMappe.clicked.connect(self._vaelg_mappe)
        self.btnOpretProjekt.clicked.connect(self._opret_projekt_mapper)
        self.btnDataforsyningenLink.clicked.connect(self._aaben_dataforsyningen_token)

        # Tilføj en "Nulstil"-knap øverst i dialogen (altid synlig over scroll-området)
        self._tilfoej_nulstil_knap()

        # Ekstra projektområder er FJERNET: hvert projektområde skal være ét
        # sammenhængende polygon og køres som sit eget projekt. Listerne holdes
        # tomme, så _aktive_projektomraader kun bruger det primære område.
        self._ekstra_raekker         = []
        self._ekstra_projektomraader = []
        self._MAX_EKSTRA             = 0
        self.btnKorHojdemodel.clicked.connect(self._kor_hojdemodel)
        self.btnKorOplande.clicked.connect(self._kor_oplande)
        self.btnKorKlipGrunddata.clicked.connect(self._kor_klip_grunddata)
        self.btnKorVandopland.clicked.connect(self._kor_vandopland)
        self.btnKorDirekteOpland.clicked.connect(self._kor_direkte_opland)
        self.btnKorProjektomraade3.clicked.connect(self._kor_projektomraade)
        if hasattr(self, 'btnKorOverrisling'):
            self.btnKorOverrisling.clicked.connect(self._kor_overrisling)
        if hasattr(self, 'btnRetuner'):
            self.btnRetuner.clicked.connect(self._kor_retuner)
        # "Kør alle"-knapperne er fjernet — hvert trin køres enkeltvis.
        for _navn in ('btnKorAlleForberedende', 'btnKorAlleTilforsel',
                      'btnKorAlleOmsaetning'):
            _knap = getattr(self, _navn, None)
            if _knap is not None:
                _knap.setVisible(False)
        if hasattr(self, 'btnKorEkstensivering'):
            self.btnKorEkstensivering.clicked.connect(self._kor_ekstensivering)

        # Ensartet udseende på trin-knapperne. Skal køre EFTER at kør-knapperne
        # er forbundet ovenfor.
        self._aktiver_kor_eller_vaelg()
        self._kompakt_boks1()

        # Overvag tekstfelter i boks 1 for at låse/låse op boks 2-4
        self.lineEditProjektomraade.textChanged.connect(self._opdater_laase)
        self.lineEditToken.textChanged.connect(self._opdater_laase)
        self.lineEditMappe.textChanged.connect(self._opdater_laase)

        # Token-status flueben
        self.lineEditToken.textChanged.connect(self._opdater_token_status)

        # Lås "Tilføj dit projektområde" indtil projektmappen er oprettet
        self.lineEditMappe.textChanged.connect(self._opdater_projektomraade_laase)
        self.lineEditProjektnavn.textChanged.connect(self._opdater_projektomraade_laase)

        # Sørg for boks 2-4 og projektområde-feltet starter låst
        self._opdater_laase()
        self._opdater_projektomraade_laase()
        self._opdater_token_status()

        # Generer "Åben resultater"-knapper baseret på aktive projektområder.
        # Opdater når primært projektomraade-tekstfelt eller projektnavn/mappe ændres.
        self.lineEditProjektomraade.textChanged.connect(self._opdater_aaben_resultater_knapper)
        self.lineEditProjektnavn.textChanged.connect(self._opdater_aaben_resultater_knapper)
        self.lineEditMappe.textChanged.connect(self._opdater_aaben_resultater_knapper)
        self._opdater_aaben_resultater_knapper()

        # Maskér token-feltet (vises som ****) og gem automatisk ved ændring
        self.lineEditToken.setEchoMode(QLineEdit.Password)
        self.lineEditToken.textChanged.connect(self._gem_token)

        # Token bruges KUN til WCS-download af højdemodel. Skjul derfor token-
        # widgets i boks 1 — token spørges i stedet ved "Beregn strømningsveje" hvis
        # det mangler. Selve lineEditToken beholdes (skjult) som lager for værdien.
        self._skjul_token_widgets()

        # Gem mappe og projektnavn løbende i QgsSettings
        self.lineEditMappe.textChanged.connect(self._gem_mappe)
        self.lineEditProjektnavn.textChanged.connect(self._gem_projektnavn)
        self.lineEditProjektomraade.textChanged.connect(self._gem_projektomraade_fil)

        # Gendan session-state fra forrige kørsel
        self._restaurer_session()

        # Registrér pluginnets medfølgende Scripts/-mappe i QGIS Processing.
        # Sker automatisk i baggrunden — scripts er altid tilgængelige uden
        # at brugeren skal trykke på en "Opdater scripts"-knap.
        self._registrer_scripts_automatisk()

    # ------------------------------------------------------------------
    # Automatisk script-registrering
    # ------------------------------------------------------------------

    def _registrer_scripts_automatisk(self):
        """Tilføjer pluginnets Scripts/-mappe til Processing's SCRIPTS_FOLDERS
        og refresher algoritmerne. Kører stille — fejl logges blot, så en
        manglende mappe aldrig blokerer åbning af dialogen."""
        try:
            if not os.path.isdir(_PLUGIN_SCRIPTS):
                return
            from processing.core.ProcessingConfig import ProcessingConfig
            nuvaerende = ProcessingConfig.getSetting('SCRIPTS_FOLDERS') or ''
            mapper = [m for m in nuvaerende.split(';') if m]

            # Fjern registreringer af de SAMME scripts fra en ANDEN kopi af
            # pluginnet (Vaadomraade_Modeller, _v2, _v3 …).
            #
            # Algoritmerne har faste id'er — script:udpeg_oplande_n,
            # script:beregn_stroemningsveje, script:hent_dhm_wcs. Er to kopier
            # registreret på én gang, afgør indlæsningsrækkefølgen hvilken version
            # der faktisk køres, og man kan komme til at regne med den gamle model
            # uden at noget siger fra. Kun den kopi der hører til DENNE kode, må
            # stå i listen.
            def _er_anden_kopi(sti):
                n = os.path.normpath(sti)
                if n.lower() == os.path.normpath(_PLUGIN_SCRIPTS).lower():
                    return False  # vores egen — behold
                if os.path.basename(n).lower() != 'scripts':
                    return False
                forael = os.path.basename(os.path.dirname(n)).lower()
                return forael.startswith('vaadomraade_modeller')

            renset = [m for m in mapper if not _er_anden_kopi(m)]
            fjernet = [m for m in mapper if _er_anden_kopi(m)]
            if fjernet:
                from qgis.core import QgsMessageLog, Qgis
                QgsMessageLog.logMessage(
                    'Fjernede scripts-mapper fra andre kopier af pluginnet, så '
                    'algoritmerne kommer fra denne: ' + '; '.join(fjernet),
                    'N-regneark (LIMF)', Qgis.Info)
            if _PLUGIN_SCRIPTS not in renset:
                renset.append(_PLUGIN_SCRIPTS)

            # Gem mappe-listen hvis den ændrede sig (fx gammel mappe fjernet).
            if renset != mapper:
                ProcessingConfig.setSettingValue('SCRIPTS_FOLDERS', ';'.join(renset))

            # Refresh ALTID algoritmerne, så pluginnets scripts faktisk er
            # indlæst i registret — også når mappen allerede stod i config men
            # ikke blev loadet ved opstart. Ellers fejler modellerne med
            # "algorithms are not available" selvom mappen er registreret.
            udbyder = QgsApplication.processingRegistry().providerById('script')
            if udbyder:
                udbyder.refreshAlgorithms()
        except Exception as e:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(
                f'Kunne ikke auto-registrere scripts: {e}',
                'N-regneark (LIMF)', Qgis.Warning)

    # ------------------------------------------------------------------
    # Nulstil indstillinger
    # ------------------------------------------------------------------

    def _tilfoej_nulstil_knap(self):
        """Indsætter en 'Nulstil indstillinger'-knap øverst i dialogen."""
        from qgis.PyQt.QtWidgets import QPushButton, QHBoxLayout

        knap = QPushButton('Nulstil indstillinger', self)
        knap.setToolTip('Rydder mappe, projektnavn, token, projektområder og '
                        'valgte højdemodeller — og nulstiller felterne.')
        knap.setStyleSheet(
            'QPushButton { color: #B71C1C; background-color: #FDECEA; '
            'border: 1px solid #B71C1C; border-radius: 4px; '
            'padding: 4px 10px; font-weight: bold; } '
            'QPushButton:hover { background-color: #F9D7D2; }')
        knap.clicked.connect(self._nulstil_indstillinger)

        # Læg knappen i en højrejusteret række øverst i mainLayout.
        række = QHBoxLayout()
        række.addStretch(1)
        række.addWidget(knap)
        layout = self.layout()           # mainLayout (QVBoxLayout)
        if layout is not None:
            layout.insertLayout(0, række)

    def _nulstil_indstillinger(self):
        """Rydder alle pluginnets gemte indstillinger og nulstiller dialogen."""
        svar = QMessageBox.question(
            self, 'Nulstil indstillinger',
            'Vil du nulstille alle indstillinger?\n\n'
            'Det rydder valgt mappe/placering, projektnavn, token, '
            'projektområder og valgte højdemodeller.\n\n'
            'Dine filer på disken (resultater, output) slettes IKKE.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if svar != QMessageBox.Yes:
            return

        from qgis.core import QgsSettings
        s = QgsSettings()
        # Fjern hele plugin-gruppen — fanger også valgt_hoejdemodel/<område>-undernøgler.
        s.beginGroup('vaadomraade_modeller')
        s.remove('')
        s.endGroup()

        # Ryd felter i boks 1 (uden at trigge gemme-signaler undervejs).
        for felt in (self.lineEditMappe, self.lineEditProjektnavn,
                     self.lineEditToken, self.lineEditProjektomraade):
            felt.blockSignals(True)
            felt.clear()
            felt.blockSignals(False)

        # Ryd ekstra projektområde-rækker.
        for _lbl, _check, felt, _kv, _kr in getattr(self, '_ekstra_raekker', []):
            felt.blockSignals(True)
            felt.clear()
            felt.blockSignals(False)

        # Skjul alle status-flueben.
        for navn in dir(self):
            if navn.startswith('lblStatus'):
                lbl = getattr(self, navn, None)
                if lbl is not None and hasattr(lbl, 'setVisible'):
                    lbl.setText('')
                    lbl.setVisible(False)

        # Gendan standard-tilstand (forudfylder mappe=C:\temp og standard-token,
        # og opdaterer låse/status via _restaurer_session).
        self._restaurer_session()

        self._vis_status_besked('Nulstillet',
            'Indstillingerne er nulstillet. Filer på disken er ikke rørt.')

    # ------------------------------------------------------------------
    # Kør-eller-vælg-lag: hvert trin kan enten køre modellen eller få et
    # eksisterende lag kopieret ind på trinnets forventede output-sti.
    # ------------------------------------------------------------------

    def _trin_outputs(self):
        """Mapping: knap-attributnavn -> liste af (vis_navn, output_filnavn).

        output_filnavn er det navn modellen ellers ville skrive i
        Outputfiler_<omraade>/. Når brugeren vælger et lag, kopieres det
        hertil, så alle efterfølgende trin virker uændret.

        BEMÆRK: Højdemodel håndteres særskilt (se _trin_er_hojdemodel) — der
        erstattes KUN DHM-downloaden, og resten af modellen (fill sinks,
        channel networks) køres oven på den valgte højdemodel. Derfor er den
        ikke med her."""
        return {
            'btnKorOplande': [
                ('Vandoplandet',      'Vandoplandet.gpkg'),
                ('Direkte opland',    'Direkte_Opland.gpkg'),
            ],
            'btnKorKlipGrunddata': [
                ('Jordbund klippet',  'Jordbund_Klippet.gpkg'),
                ('Marker klippet',    'Marker_klippet.gpkg'),
                ('Befæstet areal',    'Befaestet_Areal.gpkg'),
                ('Natur',             'Natur.gpkg'),
                ('Omdriftsarealer',   'Omdriftsarealer.gpkg'),
            ],
            'btnKorDirekteOpland': [
                ('Direkte omdriftsarealer', 'Direkte_Omdriftsarealer.gpkg'),
            ],
            'btnKorVandopland': [
                ('Vandoplandet',      'Vandoplandet.gpkg'),
            ],
        }

    def _trin_knaptekster(self):
        """Mapping: knap-attributnavn -> knappens tekst.

        Navnet beskriver hvad trinnet gør, ikke at det er en model der køres."""
        return {
            'btnKorOplande':       'Beregn oplande',
            'btnKorKlipGrunddata': 'Klip grunddata',
            'btnKorVandopland':    'Beregn vandopland',
            'btnKorDirekteOpland': 'Beregn direkte opland',
        }

    # Fælles knap-stil for trin-handlinger, så alle trin ser ens ud.
    _TRIN_KNAP_STIL = (
        'QPushButton { color: white; background-color: #2E7D32; '
        'border: none; border-radius: 4px; padding: 4px 12px; '
        'font-weight: bold; min-height: 24px; } '
        'QPushButton:hover { background-color: #1B5E20; } '
        'QPushButton:disabled { background-color: #BDBDBD; color: #EEEEEE; }'
    )

    # Faste bredder så knapperne lander i ordnede kolonner på tværs af alle trin.
    _KNAP_BREDDE  = 190   # bredde for hver handlingsknap (rummer de længste tekster)

    # Fast bredde på trin-labels så knap-kolonnerne starter samme sted i alle rækker.
    _LABEL_BREDDE = 200

    # Knapperne i boks 1 er smallere end trin-knapperne. Med 190 ville rækken
    # naa laengere ud end alt andet indhold i dialogen, og saa var det boks 1
    # der bestemte vinduets bredde. 150 raekker til teksterne dér.
    _BOKS1_KNAP_BREDDE = 150

    def _kompakt_boks1(self):
        """Slår hvert felts to rækker i boks 1 sammen til én.

        Boksen havde en række med feltets navn og ?-ikonet, og en række til under
        med selve feltet — otte rækker til fire felter, og en boks der fyldte
        halvdelen af dialogen. Her flyttes navnet og ikonet ind foran feltet.

        Labelen får samme faste bredde som trin-labelene i boks 2-4, så navnene
        står i én kolonne hele vejen ned gennem dialogen.
        """
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QLayout

        par = (
            ('mappeHeaderLayout', 'mappeLayout'),
            ('projektnavnHeaderLayout', 'projektnavnInputLayout'),
            ('projektomraadeLayout', 'filLayout'),
            ('tokenLayout', 'tokenInputLayout'),
        )
        box1 = self.findChild(QLayout, 'box1Layout')
        for header_navn, felt_navn in par:
            header = self.findChild(QLayout, header_navn)
            felt = self.findChild(QLayout, felt_navn)
            if header is None or felt is None:
                continue
            # Widgets flyttes i samme rækkefølge; header-rækkens spacer skal ikke
            # med — den skubbede navnet til venstre i en række der nu har et felt.
            flyt = []
            while header.count():
                sag = header.takeAt(0)
                w = sag.widget()
                if w is not None:
                    flyt.append(w)
            for i, w in enumerate(flyt):
                felt.insertWidget(i, w)
            felt.setSpacing(6)
            # Link-knappen ved token-feltet hører til EFTER feltet, ikke mellem
            # navnet og det — den er en henvisning, ikke en del af etiketten.
            link = getattr(self, 'btnDataforsyningenLink', None)
            if link is not None and felt.indexOf(link) >= 0:
                felt.removeWidget(link)
                felt.addWidget(link)
            # Stretch til sidst. Uden den strækker feltet sig ud i hele boksens
            # bredde, og så er det boks 1 der bestemmer hvor bred dialogen bliver —
            # bredere end alt andet indhold.
            felt.addStretch(1)
            if box1 is not None:
                box1.removeItem(header)
            header.setParent(None)

        # Projektområdet vælges blandt kortlagene — ikke i en fildialog.
        if getattr(self, 'btnVaelgFil', None) is not None:
            self.btnVaelgFil.setText('Vælg kortlag')
            self.btnVaelgFil.setToolTip(
                'Vælg projektområdet blandt polygonlagene i QGIS-projektet')

        for navn in ('labelMappe', 'labelProjektnavn', 'labelProjektomraade',
                     'labelToken'):
            lbl = getattr(self, navn, None)
            if lbl is not None:
                lbl.setFixedWidth(self._LABEL_BREDDE)
                lbl.setWordWrap(False)

        # Knapperne i boks 1 får ÉN fælles bredde, men ikke trin-knappernes 190:
        # med den bliver rækken bredere end resten af dialogen, og så er det boks 1
        # der bestemmer vinduets bredde. Bredden måles på den længste knaptekst i
        # brugerens egen skrifttype, så den passer uanset systemets skriftstørrelse.
        for navn in ('btnVaelgMappe', 'btnOpretProjekt', 'btnVaelgFil'):
            knap = getattr(self, navn, None)
            if knap is not None:
                knap.setFixedWidth(self._BOKS1_KNAP_BREDDE)

        # Statusmærket til sidst: fast bredde, så ✓/✗ står i lodret linje og ikke
        # flytter sig når teksten skifter.
        # Indtastningsfelterne får samme bredde som trin-knapperne, så felt- og
        # knapkolonnen flugter hele vejen ned gennem dialogen.
        for navn in ('lineEditMappe', 'lineEditProjektnavn',
                     'lineEditProjektomraade', 'lineEditToken'):
            felt_w = getattr(self, navn, None)
            if felt_w is not None:
                felt_w.setFixedWidth(self._KNAP_BREDDE)

        for navn in ('lblStatusMappe', 'lblStatusProjektnavn',
                     'lblStatusProjektomraade', 'lblStatusToken'):
            mrk = getattr(self, navn, None)
            if mrk is not None:
                mrk.setFixedWidth(24)
                mrk.setAlignment(Qt.AlignCenter)

    def _aktiver_kor_eller_vaelg(self):
        """Styler hvert trins knap ens og giver trin-labelen fast bredde, så
        knapperne flugter i en pæn kolonne på tværs af alle trin."""
        from qgis.PyQt.QtWidgets import QLabel
        from qgis.PyQt.QtCore import Qt

        # Ensartet størrelse OG stil på alle "?"-hjælpeikoner, så de ser ens ud
        # overalt (en pæn rund blå cirkel — ikke en lodret oval).
        _HJAELP_STIL = (
            'QLabel { color: white; background-color: #1565C0; '
            'border-radius: 9px; font-weight: bold; }'
        )
        for _navn in dir(self):
            if _navn.startswith('hjaelp'):
                _ikon = getattr(self, _navn, None)
                if isinstance(_ikon, QLabel) and _ikon.text() == '?':
                    _ikon.setFixedSize(18, 18)
                    _ikon.setAlignment(Qt.AlignCenter)
                    _ikon.setStyleSheet(_HJAELP_STIL)

        def _fastgoer_label(knap):
            """Giver trin-labelen (første QLabel) fast bredde og fjerner rækkens
            mellemliggende spacer, så knapperne og "eller" flugter præcist uden
            at blive klemt."""
            from qgis.PyQt.QtWidgets import QSpacerItem
            layout = self._find_knap_layout(knap)
            if layout is None:
                return
            # Ensartet afstand mellem elementer i ALLE trin-rækker, så ? og
            # knapper flugter i kolonner uanset rækkens oprindelige spacing.
            layout.setSpacing(6)
            # Fast bredde på trin-navnet, så det ikke ekspanderer.
            for i in range(layout.count()):
                w = layout.itemAt(i).widget()
                if isinstance(w, QLabel) and w.text() and w.text() != '?':
                    w.setFixedWidth(self._LABEL_BREDDE)
                    break
            # Fjern rækkens spacer (mellem label og knap), så fast-bredderne
            # styrer layoutet rent. Tilføj i stedet en stretch EFTER knapperne.
            for i in range(layout.count()):
                if isinstance(layout.itemAt(i), QSpacerItem):
                    layout.takeAt(i)
                    break

        def _stil_trin_knap(knap):
            """Styler trinnets knap og retter rækkens layout til.

            Hvert trin har ÉN knap. Tidligere stod der en "eller" og en knap mere
            ude til højre, hvor man kunne bruge sine egne lag i stedet for at køre
            trinnet.
            """
            knap.setStyleSheet(self._TRIN_KNAP_STIL)
            knap.setFixedWidth(self._KNAP_BREDDE)
            _fastgoer_label(knap)
            layout = self._find_knap_layout(knap)
            if layout is None:
                return
            # Stretch til sidst, så knappen ikke trækkes ud i bredden.
            layout.addStretch(1)

        # Højdemodel-trinnet hedder det, det gør: det beregner strømningsvejene.
        # Terrænet er midlet, ikke målet — og det skaffes selv, enten fra det
        # præberegnede grundlag eller fra Dataforsyningen.
        hojde_knap = getattr(self, 'btnKorHojdemodel', None)
        if hojde_knap is not None:
            hojde_knap.setText('Beregn strømningsveje')
            hojde_knap.setToolTip(
                'Beregn strømningsvejene. Terrænet hentes selv — fra det '
                'præberegnede grundlag hvis det dækker området, ellers fra '
                'Dataforsyningen.')
            _stil_trin_knap(hojde_knap)

        # Øvrige trin: kopiér valgt lag direkte ind på output-stien.
        outputs = self._trin_outputs()
        tekster = self._trin_knaptekster()
        for knap_navn, mål in outputs.items():
            knap = getattr(self, knap_navn, None)
            if knap is None:
                continue
            kor_tekst = tekster.get(knap_navn, 'Kør')
            # Omdøb selve kør-knappen retvisende for trinnet.
            knap.setText(kor_tekst)
            knap.setToolTip(f'{kor_tekst} ved at køre modellen for dette trin')
            _stil_trin_knap(knap)

        # Fjern det inaktive "(Oversvømmelse)"-trin i boks 3 ved at skjule det.
        for _navn in ('labelOversvommelse', 'hjaelpOversvommelse'):
            _w = getattr(self, _navn, None)
            if _w is not None:
                _w.setVisible(False)

        # Skjul afkrydsningsfeltet ved projektområdet — det gav kun mening da man
        # kunne vælge mellem flere områder. Nu er der altid kun ét.
        _chk = getattr(self, 'checkProjektomraade', None)
        if _chk is not None:
            _chk.setChecked(True)
            _chk.setVisible(False)

        # Enkelt-knap-trin (uden "brug egne"-alternativ): style grøn, fast bredde
        # og omdøb retvisende. Bruger PRÆCIS samme layout-flow som par-rækkerne
        # (_fastgoer_label), så knappen flugter automatisk — uanset font/DPI.
        enkelt_knapper = {
            'btnKorProjektomraade3': 'Indsæt i regneark',
            'btnKorOverrisling':     'Beregn overrisling',
            'btnKorEkstensivering':  'Indsæt i regneark',
        }
        for knap_navn, tekst in enkelt_knapper.items():
            knap = getattr(self, knap_navn, None)
            if knap is None:
                continue
            knap.setText(tekst)
            knap.setStyleSheet(self._TRIN_KNAP_STIL)
            knap.setFixedWidth(self._KNAP_BREDDE)
            knap.setToolTip(tekst)
            _fastgoer_label(knap)
            layout = self._find_knap_layout(knap)
            if layout is not None:
                layout.addStretch(1)

        # Ekstensivering-rækken har en spinbox FØR knappen. Giv den fast bredde
        # og træk dens plads fra label-bredden, så knappen flugter med de andre.
        _spin = getattr(self, 'spinEkstensivering', None)
        _ek_knap = getattr(self, 'btnKorEkstensivering', None)
        if _spin is not None and _ek_knap is not None:
            _spin.setFixedWidth(60)
            _ek_layout = self._find_knap_layout(_ek_knap)
            if _ek_layout is not None:
                from qgis.PyQt.QtWidgets import QLabel as _QLabel
                # Reducér label-bredden med spinboxens bredde + lidt luft, så
                # knappen lander i samme kolonne som de øvrige.
                for i in range(_ek_layout.count()):
                    w = _ek_layout.itemAt(i).widget()
                    if isinstance(w, _QLabel) and w.text() and w.text() != '?':
                        w.setFixedWidth(self._LABEL_BREDDE - 60 - 6)
                        break

        # Ryd parenteser fra (Ekstensivering)-labelen, da trinnet nu er aktivt.
        _ek_lbl = getattr(self, 'labelEkstensivering', None)
        if _ek_lbl is not None:
            _ek_lbl.setText('Ekstensivering')

        # "Vis resultater"-rækken: fast label-bredde + fjern spacer, så de
        # dynamisk genererede knapper flugter i samme kolonne som de andre.
        _vis_lbl = getattr(self, 'labelAabenResultater', None)
        if _vis_lbl is not None:
            # _find_knap_layout finder layoutet ud fra en hvilken som helst
            # widget i rækken (søger efter widget-identitet).
            _vis_layout = self._find_knap_layout(_vis_lbl)
            if _vis_layout is not None:
                from qgis.PyQt.QtWidgets import QSpacerItem as _QSpacer
                _vis_lbl.setFixedWidth(self._LABEL_BREDDE)
                for i in range(_vis_layout.count()):
                    if isinstance(_vis_layout.itemAt(i), _QSpacer):
                        _vis_layout.takeAt(i)
                        break
                # Nulstil containerens egne margener, så knapperne ikke skubbes
                # ind, og tilføj en stretch så de venstrejusteres i kolonnen.
                _cont_layout = getattr(self, 'aabenResultaterContainerLayout', None)
                if _cont_layout is not None:
                    _cont_layout.setContentsMargins(0, 0, 0, 0)
                _vis_layout.addStretch(1)

        # Skjul den separate "Retuner data"-række — dens funktion (Vis i GIS)
        # flytter ind i "Vis resultater"-rækken som en af de to knapper.
        for _navn in ('labelRetuner', 'hjaelpRetuner', 'btnRetuner', 'lblStatusRetuner',
                      'labelBoxRetunerTitle'):
            _w = getattr(self, _navn, None)
            if _w is not None:
                _w.setVisible(False)

    @staticmethod
    def _find_knap_layout(knap):
        """Finder det QLayout en widget er placeret i (søger i forælderens layout
        og dets under-layouts). Returnerer None hvis ikke fundet."""
        from qgis.PyQt.QtWidgets import QLayout
        forælder = knap.parent()
        if forælder is None:
            return None
        rod = forælder.layout()
        if rod is None:
            return None

        def søg(layout):
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item.widget() is knap:
                    return layout
                under = item.layout()
                if under is not None:
                    fund = søg(under)
                    if fund is not None:
                        return fund
            return None

        return søg(rod)

    def _kopier_lag_fil(self, kilde, maal):
        """Kopierer en lag-fil til målstien — inkl. raster-sidefiler (fx .sdat
        følges af .sgrd/.prj/.sdat.aux.xml). Konverterer ikke formater; målets
        endelse styres af det forventede output-filnavn.

        Returnerer True ved succes."""
        import shutil
        try:
            kilde_rod, kilde_ext = os.path.splitext(kilde)
            maal_rod,  maal_ext  = os.path.splitext(maal)

            # Hvis formaterne matcher: kopiér hovedfil + evt. sidefiler.
            if kilde_ext.lower() == maal_ext.lower():
                shutil.copy2(kilde, maal)
                # Kopiér kendte sidefiler med samme stamme
                for side_ext in ('.sgrd', '.prj', '.sdat.aux.xml', '.aux.xml',
                                  '.shx', '.dbf', '.cpg', '.qpj', '.prj'):
                    side_kilde = kilde_rod + side_ext
                    if os.path.isfile(side_kilde):
                        shutil.copy2(side_kilde, maal_rod + side_ext)
                return os.path.isfile(maal)

            # Forskellige formater: konvertér via QGIS' lag-loader.
            return self._konverter_lag_fil(kilde, maal)
        except Exception as e:
            QMessageBox.critical(self, 'Fejl ved kopiering',
                f'Kunne ikke kopiere laget:\n{e}')
            return False

    def _konverter_lag_fil(self, kilde, maal):
        """Konverterer en lag-fil til målformatet (udledt af målets endelse)."""
        from qgis.core import (
            QgsVectorLayer, QgsRasterLayer, QgsVectorFileWriter,
            QgsRasterFileWriter, QgsProject, QgsCoordinateTransformContext
        )
        maal_ext = os.path.splitext(maal)[1].lower()
        raster_ext = {'.tif', '.tiff', '.sdat', '.asc', '.img', '.vrt'}

        if maal_ext in raster_ext:
            lag = QgsRasterLayer(kilde, 'kilde')
            if not lag.isValid():
                return False
            writer = QgsRasterFileWriter(maal)
            pipe = lag.pipe()
            fejl = writer.writeRaster(pipe, lag.width(), lag.height(),
                                      lag.extent(), lag.crs())
            return fejl == 0 and os.path.isfile(maal)
        else:
            lag = QgsVectorLayer(kilde, 'kilde', 'ogr')
            if not lag.isValid():
                return False
            muligheder = QgsVectorFileWriter.SaveVectorOptions()
            muligheder.driverName = 'GPKG' if maal_ext == '.gpkg' else 'ESRI Shapefile'
            res = QgsVectorFileWriter.writeAsVectorFormatV3(
                lag, maal, QgsProject.instance().transformContext(), muligheder)
            kode = res[0] if isinstance(res, tuple) else res
            return kode == QgsVectorFileWriter.NoError and os.path.isfile(maal)

    # ------------------------------------------------------------------
    # Status-indikatorer
    # ------------------------------------------------------------------

    def _sæt_status(self, label_navn, succes=None, koerte=None, fejlede=None,
                    valgt_model=False):
        """Opdaterer en status-QLabel med ✓ (grøn) eller ✗ (rød).

        succes=True/False: simpel boolsk indikator (boks 1-handlinger).
        koerte/fejlede: lister af projektområde-navne (model-runs med multiple areas).
          - Alle OK  → ✓
          - Alle fejl → ✗
          - Blandet   → ✓ ✗(fejlede1, fejlede2)
        valgt_model: hvis True tilføjes en lille "(valgt model)"-markør ved ✓,
          så man kan se at trinnet brugte en valgt højdemodel i stedet for download.
        """
        lbl = getattr(self, label_navn, None)
        if lbl is None:
            return
        koerte  = koerte  or []
        fejlede = fejlede or []
        flueben = '<span style="color:#2E7D32;font-weight:bold;font-size:14px;">✓</span>'
        if valgt_model:
            flueben += (' <span style="color:#1565C0;font-weight:bold;'
                        'font-size:11px;">(valgt model)</span>')
        if succes is not None:
            if succes:
                html = flueben
            else:
                html = '<span style="color:#D32F2F;font-weight:bold;font-size:14px;">✗</span>'
        elif not fejlede:
            html = flueben
        elif not koerte:
            html = '<span style="color:#D32F2F;font-weight:bold;font-size:14px;">✗</span>'
        else:
            fejl_navne = ', '.join(n.split(' (')[0] for n in fejlede)
            html = (
                flueben
                + f' <span style="color:#D32F2F;font-weight:bold;font-size:14px;">✗({fejl_navne})</span>'
            )
        lbl.setText(html)
        lbl.setVisible(True)

    def _vis_status_besked(self, titel, besked, fejl=False):
        """Viser en diskret besked i QGIS' statuslinje (message bar) i stedet
        for en popup. Falder stille tilbage hvis iface ikke er tilgængelig."""
        try:
            from qgis.utils import iface
            from qgis.core import Qgis
            if iface is not None:
                niveau = Qgis.Warning if fejl else Qgis.Info
                iface.messageBar().pushMessage(titel, besked, level=niveau, duration=6)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Fil- og mappevælgere
    # ------------------------------------------------------------------

    def _vaelg_lag_fra_kanvas(self):
        """Lad brugeren vælge projektområdet blandt polygonlagene i QGIS-projektet.

        Returnerer en filsti. Ligger laget kun i hukommelsen (et tegnet lag,
        et resultat fra et værktøj), gemmes det til disk først — resten af
        modellen læser projektområdet som en fil.

        Kun polygonlag: et projektområde er en flade. Punkter og linjer i listen
        ville kun kunne vælges forkert.
        """
        from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes
        from qgis.PyQt.QtWidgets import QInputDialog

        vektorlag = [
            lag for lag in QgsProject.instance().mapLayers().values()
            if isinstance(lag, QgsVectorLayer) and lag.isValid()
            and QgsWkbTypes.geometryType(lag.wkbType()) == QgsWkbTypes.PolygonGeometry
        ]
        if not vektorlag:
            QMessageBox.warning(self, 'Ingen polygonlag',
                'Der er ingen polygonlag i QGIS-projektet.\n\n'
                'Indlæs projektområdet som et lag først — træk filen ind i QGIS, '
                'eller tegn området med et midlertidigt lag.')
            return None

        navne = [lag.name() for lag in vektorlag]
        navn, ok = QInputDialog.getItem(
            self, 'Vælg kortlag',
            'Vælg det lag der skal bruges som projektområde:',
            navne, 0, False
        )
        if not ok or not navn:
            return None
        lag = vektorlag[navne.index(navn)]

        # Find lagets filsti (uden evt. |layername=...-suffiks)
        kilde = lag.source().split('|', 1)[0]
        if kilde and os.path.isfile(kilde):
            return kilde

        # Memory/scratch-lag uden fil — gem det til disk i projektmappen.
        return self._gem_lag_til_disk(lag)

    def _gem_lag_til_disk(self, lag):
        """Gemmer et vektor-lag som GeoPackage i projektmappen (eller standardmappen)
        og returnerer filstien. Returnerer None ved fejl."""
        from qgis.core import (
            QgsVectorFileWriter, QgsProject, QgsCoordinateTransformContext
        )

        rod = self.lineEditMappe.text().strip() or _STANDARD_MAPPE
        projektnavn = _sanitize_naam(self.lineEditProjektnavn.text().strip())
        maalmappe = os.path.join(rod, projektnavn) if projektnavn else rod
        try:
            os.makedirs(maalmappe, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, 'Fejl',
                f'Kunne ikke oprette mappen til at gemme laget:\n{e}')
            return None

        sikkert_navn = _sanitize_naam(lag.name()) or 'Projektomraade'
        maal = os.path.join(maalmappe, f'{sikkert_navn}.gpkg')

        muligheder = QgsVectorFileWriter.SaveVectorOptions()
        muligheder.driverName = 'GPKG'
        muligheder.layerName = sikkert_navn
        try:
            resultat = QgsVectorFileWriter.writeAsVectorFormatV3(
                lag, maal, QgsProject.instance().transformContext(), muligheder
            )
            fejlkode = resultat[0] if isinstance(resultat, tuple) else resultat
        except Exception as e:
            QMessageBox.critical(self, 'Fejl',
                f'Kunne ikke gemme laget til disk:\n{e}')
            return None

        if fejlkode != QgsVectorFileWriter.NoError or not os.path.isfile(maal):
            QMessageBox.critical(self, 'Fejl',
                f'Laget "{lag.name()}" kunne ikke gemmes til:\n{maal}')
            return None

        return maal

    def _vaelg_projektomraade_fil(self):
        """Vælg det primære projektområde blandt kortlagene og kør efterbehandling."""
        sti = self._vaelg_lag_fra_kanvas()
        if sti:
            self._anvend_primaert_projektomraade(sti)

    def _anvend_primaert_projektomraade(self, fil):
        """Efterbehandling når det primære projektområde er valgt."""
        if not self._valider_projektomraade(fil):
            return  # afvist — fx flere separate polygoner
        fil = self._fix_geometri(fil)  # ret ugyldig geometri automatisk
        self.lineEditProjektomraade.setText(fil)
        self._opret_projektomraade_undermappe(fil)
        navn = os.path.splitext(os.path.basename(fil))[0]
        self._tilfoej_lag_til_projekt([(f'Projektomraade_{navn}', fil)])
        self._sæt_status('lblStatusProjektomraade', succes=True)

    def _valider_projektomraade(self, fil):
        """Tjekker at projektområde-filen er ÉT sammenhængende polygon.

        Et projektområde med flere SEPARATE polygoner (flere features) afvises,
        da de hydrologiske beregninger (channel networks, oplande) skal køres som
        uafhængige processer pr. område. To dele skal derfor køres som separate
        projekter. Et enkelt multipolygon (én feature med flere dele) accepteres.

        Returnerer True hvis filen er gyldig (eller ikke kan læses — så lader vi
        de senere trin om fejlen)."""
        try:
            if not fil or not os.path.isfile(fil):
                return True
            from qgis.core import QgsVectorLayer
            lag = QgsVectorLayer(fil, 'tjek', 'ogr')
            if not lag.isValid():
                return True  # lad senere trin håndtere ugyldig fil
            antal = lag.featureCount()
            if antal > 1:
                QMessageBox.warning(
                    self, 'Projektområdet har flere dele',
                    f'Den valgte fil indeholder {antal} separate polygoner.\n\n'
                    'Et projektområde skal være ÉT sammenhængende polygon, fordi '
                    'beregningerne (strømningsveje, oplande) laves pr. område.\n\n'
                    'Har du to adskilte delområder, så gem dem som hver sin fil og '
                    'kør dem som to separate projekter.')
                return False
            return True
        except Exception:
            return True  # ved tvivl: lad det passere, så senere trin kan fejle

    def _fix_geometri(self, fil):
        """Kører 'Fix geometries' på projektområde-filen og returnerer stien til
        en renset kopi. Hvis geometrien allerede er gyldig (eller fixet fejler),
        returneres den oprindelige sti uændret.

        Den rensede fil får SAMME basenavn som originalen (så områdenavnet, der
        udledes af filnavnet, er uændret) og gemmes ved siden af originalen med
        et '_fixet'-suffiks i mappenavnet, så originalen aldrig overskrives."""
        try:
            if not fil or not os.path.isfile(fil):
                return fil
            # Allerede en fixet kopi? — så er der intet at gøre (idempotent).
            if os.path.basename(os.path.dirname(fil)) == '_fixet_geometri':
                return fil
            from qgis.core import QgsVectorLayer
            lag = QgsVectorLayer(fil, 'tjek', 'ogr')
            if not lag.isValid():
                return fil

            # Tjek hurtigt om der overhovedet er ugyldige geometrier.
            har_ugyldig = False
            for f in lag.getFeatures():
                g = f.geometry()
                if g.isNull() or not g.isGeosValid():
                    har_ugyldig = True
                    break
            if not har_ugyldig:
                return fil  # alt er fint — ingen grund til at fixe

            # Gem den rensede version med samme basenavn i en _fixet-undermappe.
            basenavn = os.path.splitext(os.path.basename(fil))[0]
            maalmappe = os.path.join(os.path.dirname(fil), '_fixet_geometri')
            os.makedirs(maalmappe, exist_ok=True)
            maal = os.path.join(maalmappe, basenavn + '.gpkg')

            import processing
            processing.run('native:fixgeometries',
                           {'INPUT': fil, 'METHOD': 1, 'OUTPUT': maal})
            if os.path.isfile(maal):
                self._vis_status_besked(
                    'Geometri rettet',
                    f'Projektområdet havde ugyldig geometri — en rettet kopi bruges.')
                return maal
            return fil
        except Exception as e:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(
                f'Fix geometry fejlede ({e}) — bruger original.',
                'N-regneark (LIMF)', Qgis.Warning)
            return fil

    def _opret_projektomraade_undermappe(self, fil):
        """Opretter <rod>/<projektnavn>/<filnavn-uden-extension>/ når et projektområde er valgt."""
        rod = self.lineEditMappe.text().strip()
        projektnavn = self.lineEditProjektnavn.text().strip()

        if not rod or not projektnavn:
            return

        sikkert_projekt = _sanitize_naam(projektnavn)
        if not sikkert_projekt:
            return

        projekt_mappe = os.path.join(rod, sikkert_projekt)
        if not os.path.isdir(projekt_mappe):
            QMessageBox.warning(self, 'Projektmappe findes ikke',
                f'Projektmappen blev ikke fundet:\n{projekt_mappe}\n\n'
                f'Tryk først på det grønne flueben ved "Navngiv projekt".')
            return

        filnavn = os.path.splitext(os.path.basename(fil))[0]
        sikkert_filnavn = _sanitize_naam(filnavn)
        if not sikkert_filnavn:
            return

        undermappe        = os.path.join(projekt_mappe, sikkert_filnavn)
        outputfiler_mappe = os.path.join(undermappe, f'Outputfiler_{sikkert_filnavn}')
        resultater_mappe  = os.path.join(undermappe, 'Resultater')
        try:
            os.makedirs(outputfiler_mappe, exist_ok=True)
            os.makedirs(resultater_mappe, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, 'Fejl',
                f'Kunne ikke oprette undermapperne:\n{e}')
            return

        from qgis.core import QgsSettings
        QgsSettings().setValue('vaadomraade_modeller/projektomraade_navn', sikkert_filnavn)

    def _tilfoej_ekstra_projektomraade(self):
        """
        Tilføjer en ekstra projektområde-række (label + checkbox + lineEdit + 'Vælg fil' + ryd-knap)
        direkte i 'ekstraProjektomraaderContainer' inde i boks 1, lige under det primære
        projektområde-felt. Rækken starter aktiveret/deaktiveret afhængigt af om den
        foregående række har en fil — håndteres af _opdater_ekstra_laase.
        """
        try:
            if len(self._ekstra_raekker) >= self._MAX_EKSTRA:
                return

            from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QCheckBox

            par_nr = len(self._ekstra_raekker) + 2  # første ekstra-række er nr. 2
            container = self.ekstraProjektomraaderContainer
            layout = container.layout()

            lbl = QLabel(f'Projektområde {par_nr}', container)
            layout.addWidget(lbl)

            check = QCheckBox(container)
            check.setChecked(True)
            check.setToolTip('Markér for at inkludere dette projektområde i kørsel')

            felt = QLineEdit(container)
            felt.setReadOnly(True)
            felt.setPlaceholderText('Ingen fil valgt...')

            knap_vaelg = QPushButton('Vælg…', container)

            raekke = QHBoxLayout()
            raekke.addWidget(check)
            raekke.addWidget(felt)
            raekke.addWidget(knap_vaelg)
            layout.addLayout(raekke)

            self._ekstra_raekker.append((lbl, check, felt, knap_vaelg, None))
            self._ekstra_projektomraader.append(felt)

            knap_vaelg.clicked.connect(lambda _, f=felt, k=knap_vaelg: self._vaelg_ekstra_fil(f, k))
            felt.textChanged.connect(self._opdater_ekstra_laase)
            felt.textChanged.connect(self._opdater_aaben_resultater_knapper)

        except Exception as e:
            QMessageBox.critical(self, 'Fejl', f'_tilfoej_ekstra_projektomraade: {e}')

    def _opdater_ekstra_laase(self):
        """
        Aktiverer hver ekstra-række kun når den foregående har en fil. Når seneste
        række har fået en fil, tilføjes en ny låst (men aktiv) række — så processen
        kan gentages indefinitely op til _MAX_EKSTRA.
        """
        forrige_har_fil = bool(self.lineEditProjektomraade.text().strip())
        for lbl, check, felt, knap_vaelg, _knap_ryd in self._ekstra_raekker:
            lbl.setEnabled(forrige_har_fil)
            check.setEnabled(forrige_har_fil)
            felt.setEnabled(forrige_har_fil)
            knap_vaelg.setEnabled(forrige_har_fil)
            forrige_har_fil = bool(felt.text().strip())

        # Hvis seneste række har fil og vi ikke har nået max — tilføj endnu en
        if (self._ekstra_raekker
                and self._ekstra_raekker[-1][2].text().strip()
                and len(self._ekstra_raekker) < self._MAX_EKSTRA):
            self._tilfoej_ekstra_projektomraade()
            # Den nye række skal vises som låst-op (forrige har fil)
            ny_lbl, ny_check, ny_felt, ny_knap_v, _ny_knap_r = self._ekstra_raekker[-1]
            ny_lbl.setEnabled(True)
            ny_check.setEnabled(True)
            ny_felt.setEnabled(True)
            ny_knap_v.setEnabled(True)

    def _vaelg_ekstra_fil(self, felt, knap):
        """Vælg ekstra projektområde blandt kortlagene — opretter også undermappe-strukturen."""
        def anvend(fil):
            if not self._valider_projektomraade(fil):
                return  # afvist — fx flere separate polygoner
            fil = self._fix_geometri(fil)  # ret ugyldig geometri automatisk
            felt.setText(fil)
            self._opret_projektomraade_undermappe(fil)
            navn = os.path.splitext(os.path.basename(fil))[0]
            self._tilfoej_lag_til_projekt([(f'Projektomraade_{navn}', fil)])
        sti = self._vaelg_lag_fra_kanvas()
        if sti:
            anvend(sti)

    def get_ekstra_projektomraader(self):
        """Returnerer liste af stier for alle udfyldte ekstra projektområder."""
        return [
            p.text().strip()
            for p in self._ekstra_projektomraader
            if p.text().strip()
        ]

    def _vaelg_mappe(self):
        """Åbn mappedialog til valg af placering hvor data gemmes."""
        start = self.lineEditMappe.text().strip() or _STANDARD_MAPPE
        if not os.path.isdir(start):
            start = ''
        mappe = QFileDialog.getExistingDirectory(
            self,
            "Vælg placering hvor data skal gemmes",
            start
        )
        if mappe:
            self.lineEditMappe.setText(mappe)
            self._sæt_status('lblStatusMappe', succes=True)
            # SAGA kan ikke læse stier med æ/ø/å m.fl. Projekt-/områdenavne
            # translittereres automatisk til ASCII, men den valgte ROD-mappe kan
            # vi ikke omdøbe — så advar hvis den indeholder ikke-ASCII-tegn.
            if any(ord(c) > 127 for c in mappe):
                QMessageBox.warning(self, 'Mappesti med æ/ø/å',
                    'Den valgte placering indeholder specialtegn (fx æ, ø, å):\n'
                    f'{mappe}\n\n'
                    'Terræn-beregningen (SAGA) kan IKKE håndtere sådanne tegn i '
                    'stien og vil fejle. Vælg en placering uden æ/ø/å og andre '
                    'specialtegn — fx C:\\temp.')

    def _aaben_dataforsyningen_token(self):
        """Åbn Dataforsyningen token-siden i browserens standardprogram."""
        import webbrowser
        webbrowser.open('https://dataforsyningen.dk/user#token')

    def _opdater_token_status(self):
        """Viser ✓ ved token-feltet når token er indtastet."""
        lbl = getattr(self, 'lblStatusToken', None)
        if lbl is None:
            return
        if self.lineEditToken.text().strip():
            lbl.setText('✓')
        else:
            lbl.setText('')

    def _skjul_token_widgets(self):
        """Skjuler token-relaterede widgets i boks 1. Token bruges kun til
        WCS-download og spørges ved 'Beregn strømningsveje' hvis det mangler."""
        for navn in ('labelToken', 'hjaelpToken', 'btnDataforsyningenLink',
                     'lineEditToken', 'lblStatusToken'):
            w = getattr(self, navn, None)
            if w is not None:
                w.setVisible(False)

    def _sikr_token(self):
        """Sikrer at der er et token før WCS-download. Hvis ingen findes, beder
        en dialog om det (med mulighed for at åbne Dataforsyningens token-side).

        Returnerer True hvis et token er tilgængeligt, ellers False (afbryd)."""
        from qgis.PyQt.QtWidgets import QInputDialog, QLineEdit as _QLE

        token = self.get_token()
        if token:
            return True

        # Tilbyd at åbne token-siden, og bed derefter om token.
        svar = QMessageBox.question(
            self, 'Dataforsyningen-token kræves',
            'Download af højdemodel kræver et Dataforsyningen-token.\n\n'
            'Vil du åbne Dataforsyningens token-side i browseren?\n'
            '(Vælg "No" hvis du allerede har et token klar.)',
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes)
        if svar == QMessageBox.Cancel:
            return False
        if svar == QMessageBox.Yes:
            self._aaben_dataforsyningen_token()

        nyt, ok = QInputDialog.getText(
            self, 'Indtast token',
            'Indsæt dit Dataforsyningen-token:',
            _QLE.Normal, '')
        nyt = (nyt or '').strip()
        if not ok or not nyt:
            return False
        # Gem i (skjult) felt + QgsSettings, så det huskes til næste gang.
        self.lineEditToken.setText(nyt)
        return True

    # ------------------------------------------------------------------
    # Persistent session-state
    # ------------------------------------------------------------------

    def _gem_mappe(self, tekst):
        from qgis.core import QgsSettings
        QgsSettings().setValue('vaadomraade_modeller/mappe', tekst.strip())

    def _gem_projektnavn(self, tekst):
        from qgis.core import QgsSettings
        QgsSettings().setValue('vaadomraade_modeller/projektnavn', _sanitize_naam(tekst.strip()))

    def _gem_token(self, tekst):
        from qgis.core import QgsSettings
        QgsSettings().setValue('vaadomraade_modeller/token', tekst.strip())

    def _gem_projektomraade_fil(self, tekst):
        from qgis.core import QgsSettings
        QgsSettings().setValue('vaadomraade_modeller/projektomraade_fil', tekst.strip())

    def _restaurer_session(self):
        """Gendanner sidst brugte mappe, projektnavn, token og projektområde-fil fra QgsSettings."""
        from qgis.core import QgsSettings
        s = QgsSettings()

        mappe = s.value('vaadomraade_modeller/mappe', '')
        if not (mappe and os.path.isdir(mappe)):
            # Ingen gyldig tidligere placering — brug standardplaceringen og
            # opret den hvis den ikke findes, så feltet altid peger på en
            # eksisterende mappe ved første åbning.
            mappe = _STANDARD_MAPPE
            try:
                os.makedirs(mappe, exist_ok=True)
            except Exception:
                pass
            s.setValue('vaadomraade_modeller/mappe', mappe)
        if os.path.isdir(mappe):
            self.lineEditMappe.blockSignals(True)
            self.lineEditMappe.setText(mappe)
            self.lineEditMappe.blockSignals(False)

        projektnavn = s.value('vaadomraade_modeller/projektnavn', '')
        if projektnavn:
            self.lineEditProjektnavn.blockSignals(True)
            self.lineEditProjektnavn.setText(projektnavn)
            self.lineEditProjektnavn.blockSignals(False)

        token = s.value('vaadomraade_modeller/token', '')
        if not token:
            # TESTMILJØ: forudfyld med hardcodet token når intet er gemt endnu.
            token = _STANDARD_TOKEN
            s.setValue('vaadomraade_modeller/token', token)
        if token:
            self.lineEditToken.blockSignals(True)
            self.lineEditToken.setText(token)
            self.lineEditToken.blockSignals(False)

        projektomraade_fil = s.value('vaadomraade_modeller/projektomraade_fil', '')
        if projektomraade_fil and os.path.isfile(projektomraade_fil):
            self.lineEditProjektomraade.blockSignals(True)
            self.lineEditProjektomraade.setText(projektomraade_fil)
            self.lineEditProjektomraade.blockSignals(False)

        # Opdater UI-tilstand baseret på de gendannede værdier
        self._opdater_laase()
        self._opdater_projektomraade_laase()
        self._opdater_token_status()
        self._opdater_aaben_resultater_knapper()

    def _opret_projekt_mapper(self):
        """Opretter <valgt mappe>/<projektnavn>/ og en <projektnavn>.qgz projektfil."""
        rod = self.lineEditMappe.text().strip()
        projektnavn = self.lineEditProjektnavn.text().strip()

        if not rod:
            QMessageBox.warning(self, 'Manglende input',
                'Vælg først en mappe under "Datamappe".')
            return
        if not os.path.isdir(rod):
            QMessageBox.warning(self, 'Ugyldig mappe',
                f'Den valgte mappe findes ikke:\n{rod}')
            return
        if not projektnavn:
            QMessageBox.warning(self, 'Manglende input',
                'Skriv et projektnavn først.')
            return

        sikkert_navn = _sanitize_naam(projektnavn)
        if not sikkert_navn:
            QMessageBox.warning(self, 'Ugyldigt navn',
                'Projektnavnet indeholder kun ugyldige tegn.')
            return

        projekt_mappe     = os.path.join(rod, sikkert_navn)
        projekt_fil       = os.path.join(projekt_mappe, f'{sikkert_navn}.qgz')

        try:
            os.makedirs(projekt_mappe, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, 'Fejl',
                f'Kunne ikke oprette mappen:\n{e}')
            self._sæt_status('lblStatusProjektnavn', succes=False)
            return

        # Skriv QGIS-projektfil (kun hvis den ikke allerede findes, så vi ikke overskriver arbejde)
        if not os.path.isfile(projekt_fil):
            try:
                from qgis.core import QgsProject
                ok = QgsProject.instance().write(projekt_fil)
                if not ok:
                    QMessageBox.warning(self, 'Projektfil',
                        f'Mappen blev oprettet, men projektfilen kunne ikke skrives:\n{projekt_fil}')
            except Exception as e:
                QMessageBox.warning(self, 'Projektfil',
                    f'Mappen blev oprettet, men projektfilen fejlede:\n{e}')

        from qgis.core import QgsSettings
        QgsSettings().setValue('vaadomraade_modeller/projektnavn', sikkert_navn)

        self._sæt_status('lblStatusProjektnavn', succes=True)
        self._opdater_projektomraade_laase()


    # ------------------------------------------------------------------
    # Fælles hjælpemetode — bruges af ALLE modeller
    # ------------------------------------------------------------------

    def _byg_params(self, ekstra_params=None):
        """
        Returnerer et dict med de fælles parametre som ALLE modeller bruger:
          - projektomrde : den valgte shapefil fra boks 1
          - token        : det indtastede Dataforsyningen token
          - output_mappe : den valgte outputmappe

        Brug ekstra_params til at tilføje modelspecifikke output-stier o.l.
        Eksempel fra en Kør-metode:
            params = self._byg_params({'hoejdemodel': sti, ...})
        """
        params = {
            'projektomraade': self.get_projektomraade(),
            'token':          self.get_token(),
        }
        if ekstra_params:
            params.update(ekstra_params)
        return params

    def _kør_model(self, model_filnavn, ekstra_params, knap, omraade_navn='',
                   outputfiler_sti=None, lag_navn=None, lag_nokkel=None,
                   efter_succes=None, vis_succes_besked=True, algoritme_id=None):
        """
        Generel metode til at køre en .model3-fil fra Modeller/-mappen.
        Bruges af alle Kør-knapper — tilføj blot modelspecifikke params.

        omraade_navn:    bruges som suffiks på lag-navne i QGIS (f.eks. 'Projektomraade1').
        outputfiler_sti: mappe hvortil output-filer gemmes. Bruges til automatisk at tildele
                         filstier til model-outputs der ikke er angivet i ekstra_params.
                         Output-parametre detekteres automatisk fra .model3-filen — tilføj
                         eller fjern outputs i modellen, og interfacet følger med.
        efter_succes:    callable(result) der kaldes efter modellen er kørt færdig uden fejl.
                         Bruges til modeller uden model-niveau outputparametre (f.eks. scripts
                         der bestemmer outputstier internt via QgsSettings).
        vis_succes_besked: hvis False vises "Færdig"-popup ikke (brugbart ved iteration over
                           flere projektområder, hvor en samlet besked vises efter loopet).

        Returnerer True ved succes, False ved fejl/exception.
        """
        import processing
        from qgis.core import QgsProcessingModelAlgorithm, QgsSettings
        from qgis.PyQt.QtWidgets import QProgressDialog
        from qgis.PyQt.QtCore import Qt

        output_mappe = self.get_outputmappe()
        # Kør enten en .model3-fil ELLER en enkelt script-algoritme (algoritme_id).
        # Script-varianten bruges hvor en omsluttende model ikke er nødvendig
        # (fx 'Udpeg oplande (flere vandløb)' der selv slår DHM/channel op).
        model_sti = None
        # Navn til status-/fejlbeskeder (model-filnavn eller algoritme-id).
        koersel_navn = algoritme_id if algoritme_id is not None else model_filnavn
        if algoritme_id is None:
            # Modellen følger altid med pluginnet (Modeller/-mappen i plugin-roden).
            model_sti = os.path.normpath(os.path.join(_PLUGIN_MODELLER, model_filnavn))
            if not os.path.isfile(model_sti):
                QMessageBox.critical(self, "Fejl",
                    f"Model ikke fundet:\n{model_sti}\n\n"
                    f"Modellen skal ligge i pluginnets 'Modeller'-mappe."
                )
                return

        # Sikr at pluginnets scripts er registreret og indlæst i Processing,
        # før modellen køres. Modeller kalder script:-algoritmer, og hvis de
        # ikke er loadet, fejler kørslen med "algorithms are not available".
        self._registrer_scripts_automatisk()

        # Læs model-outputs fra .model3:
        #   alle_outputs → sti-tildeling (filer lander i Outputfiler uanset create_by_default)
        #   vis_outputs  → QGIS-visning (kun create_by_default=True)
        # Ved script-kørsel (algoritme_id) er der ingen .model3 at læse outputs fra —
        # scriptet bestemmer selv sine output-stier internt.
        alle_outputs = self._lae_model_outputs(model_sti, kun_standard=False) if model_sti else {}
        vis_outputs  = self._lae_model_outputs(model_sti, kun_standard=True) if model_sti else {}
        if alle_outputs and outputfiler_sti:
            os.makedirs(outputfiler_sti, exist_ok=True)
            ekstra_params = dict(ekstra_params)
            for p_navn, (display_navn, p_type) in alle_outputs.items():
                if p_navn not in ekstra_params:
                    ext      = '.tif' if p_type == 'rasterDestination' else '.gpkg'
                    ekstra_params[p_navn] = os.path.join(
                        outputfiler_sti, self._sanitize_filnavn(display_navn) + ext
                    )

        # Ved script-kørsel bruger vi KUN de eksplicit angivne params — de fælles
        # model-defaults (projektomraade/token, små bogstaver) gælder ikke for et
        # selvstændigt script og ville give "ukendt parameter"-fejl.
        params = dict(ekstra_params) if algoritme_id is not None \
            else self._byg_params(ekstra_params)

        # Gem valgt mappe i QgsSettings så alle scripts kan finde den
        QgsSettings().setValue('vaadomraade_modeller/mappe', output_mappe)

        from qgis.PyQt.QtWidgets import QApplication
        from qgis.PyQt.QtGui import QCursor
        from qgis.PyQt.QtCore import Qt as _Qt

        # Progress-dialog i animeret "arbejder"-tilstand (ubestemt bjælke) med
        # en roterende vent-besked. Beskeden roteres KUN fra feedback-callbacks
        # (sikre tidspunkter) — vi bruger IKKE en QTimer med processEvents(),
        # da det kan crashe når modellen internt kalder processing.run().
        progress_dialog = QProgressDialog(
            'Arbejder… — vent venligst', "Annuller", 0, 0, self
        )
        progress_dialog.setWindowTitle("Kører model")
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setAutoClose(False)
        progress_dialog.setAutoReset(False)
        progress_dialog.setMinimumWidth(380)  # plads til de længere beskeder
        progress_dialog.show()

        feedback = _InterfaceFeedback(progress_dialog)
        # Vis en tilfældig start-besked med det samme (fra feedbackens egen
        # seedede generator), så starten varierer fra gang til gang.
        feedback._roter_besked()

        # Husk knappens oprindelige tekst, så vi kan sætte den tilbage bagefter.
        oprindelig_tekst = knap.text()
        knap.setEnabled(False)
        knap.setText("Arbejder…")

        # Mal dialogen FÆRDIG før den blokerende kørsel starter. Det kræver
        # flere event-cyklusser (oprettelse + layout + paint), ellers vises
        # dialogen som en tom hvid boks. Dette er SIKKERT, da det sker FØR
        # processing.run() — der pumpes ikke events under selve kørslen.
        QApplication.setOverrideCursor(QCursor(_Qt.WaitCursor))
        progress_dialog.raise_()
        progress_dialog.activateWindow()
        for _ in range(5):
            QgsApplication.processEvents()
        progress_dialog.repaint()

        succes = False
        import time as _time
        _t_start = _time.perf_counter()
        try:
            if algoritme_id is not None:
                # Kør en enkelt script-algoritme direkte (ingen omsluttende model).
                result = processing.run(algoritme_id, params, feedback=feedback)
            else:
                alg = QgsProcessingModelAlgorithm()
                alg.fromFile(model_sti)
                result = processing.run(alg, params, feedback=feedback)

            # Total kørsels-tid (til fejlsøgning af hastighed) — vises i loggen.
            try:
                from qgis.core import QgsMessageLog, Qgis
                QgsMessageLog.logMessage(
                    f'[TID] Samlet kørsel ({koersel_navn}): '
                    f'{_time.perf_counter() - _t_start:.1f} s.',
                    'Vaadomraade', Qgis.Info)
            except Exception:
                pass

            # Vis fuld bjælke kort, så afslutningen er tydelig.
            progress_dialog.setRange(0, 100)
            progress_dialog.setValue(100)
            succes = True

            if efter_succes:
                try:
                    efter_succes(result)
                except Exception as e:
                    QMessageBox.warning(self, "Advarsel",
                        f"Modellen kørte færdig, men efter-kørsel-trin fejlede:\n{e}")

            # Tilføj kun outputs med create_by_default=True til QGIS
            if vis_outputs:
                self._auto_tilfoej_lag(result, vis_outputs, omraade_navn)

            # Forbehold fra beregningen skal frem til brugeren. De betyder ikke at
            # kørslen mislykkedes, men at tallene ikke kan bruges ukritisk.
            self._meld_resultat(feedback, koersel_navn, omraade_navn)

            if vis_succes_besked:
                self._vis_status_besked("Færdig", f"{koersel_navn} kørte færdig.")

        except Exception as e:
            QMessageBox.critical(self, f"Fejl ved kørsel af {koersel_navn}", str(e))
        finally:
            QApplication.restoreOverrideCursor()
            progress_dialog.close()
            knap.setEnabled(True)
            knap.setText(oprindelig_tekst)
            # Re-evaluér låsene — boks 3 og 4 kan være blevet klar efter denne kørsel
            try:
                self._opdater_laase()
            except Exception:
                pass
        return succes

    def _meld_resultat(self, feedback, koersel_navn, omraade_navn=''):
        """Bringer beregningens egne meldinger videre — uden at stå i vejen.

        Der er tre slags, og de skal ikke behandles ens:

          fejl        beregningen siger at tallene ikke kan bruges som de er —
                      et fejlet QA-tjek, et opland afskåret ved rasterkanten.
                      Det SKAL man se, så det bliver en dialogboks.
          advarsler   forbehold man skal kende, men som ikke ændrer hvad man gør
                      lige nu (et vandløb der løber igennem, manglende
                      tilpasninger). De hører i loggen, ikke i vejen.
          nøgletal    hvad der faktisk skete. Samme sted.

        En dialogboks efter hver kørsel bliver til noget man klikker væk uden
        at læse, og så er den værdiløs den ene gang den betyder noget.
        """
        fejl = list(getattr(feedback, 'fejl', []) or [])
        advarsler = list(getattr(feedback, 'advarsler', []) or [])
        noegletal = list(getattr(feedback, 'noegletal', []) or [])
        if not (fejl or advarsler or noegletal):
            return

        # Alt i QGIS' log, uanset hvor vigtigt det er.
        try:
            from qgis.core import QgsMessageLog, Qgis
            for linje, niveau in ([(t, Qgis.Info) for t in noegletal]
                                  + [(t, Qgis.Warning) for t in advarsler]
                                  + [(t, Qgis.Critical) for t in fejl]):
                QgsMessageLog.logMessage(
                    f'{koersel_navn} ({omraade_navn}): {linje}',
                    'N-regneark (LIMF)', niveau)
        except Exception:
            pass

        # Gemmes så trinnets flueben kan få dem som tooltip.
        if not hasattr(self, '_sidste_meldinger'):
            self._sidste_meldinger = []
        for t in noegletal + advarsler + fejl:
            self._sidste_meldinger.append(
                (f'{omraade_navn}: ' if omraade_navn else '') + t)

        if not fejl:
            return          # intet der kræver et klik

        titel = 'Kontrollér resultatet' + (
            (' — ' + str(omraade_navn)) if omraade_navn else '')
        QMessageBox.warning(
            self, titel,
            'Lagene er skrevet, men beregningen kan ikke stå inde for tallene:'
            + '\n\n' + '\n\n'.join('• ' + t for t in fejl)
            + '\n\nResten står i Log-panelet under "N-regneark (LIMF)".')

    # ------------------------------------------------------------------
    # Kør-handlinger — én metode per model
    # ------------------------------------------------------------------

    def _kor_hojdemodel(self):
        """Kører 'Beregn strømningsveje' (script:beregn_stroemningsveje) for hvert
        afkrydsede projektområde.

        Findes der et præberegnet grundlag der dækker området, bruges det, og
        terrænet hentes slet ikke. Ellers hentes det fra WCS.

        Trinnet henter terrænet, konditionerer det med oplandsmodellen (Scripts/
        oplande.py: tilpasningskoter fra DHM/Hydro, monoton vandløbsbrænding,
        breaching før fill) og udleder D8-strømningsveje. Det skriver DHM_raa.tif,
        Hoejdemodel.tif og Channel_Networks.gpkg til Outputfiler_<omraade>, og
        mellemresultaterne genbruges af 'Beregn oplande'."""
        # Hvis mindst ét afkrydsede område skal DOWNLOADE (ingen lokal valgt),
        # kræves et token — spørg om det hvis det mangler.
        skal_downloade = any(
            not self._har_valgt_hojdemodel(navn)
            for _fil, navn, _sti in self._aktive_projektomraader()
        )
        if skal_downloade and not self._sikr_token():
            return

        def byg(fil, navn, outputfiler_sti):
            # Scriptet slår selv outputstierne op i Outputfiler_<omraade>.
            # Lagene skal tilføjes HER: processing.run() indlæser ikke lag i
            # projektet (det gør kun runAndLoadResults), så scriptets egen
            # addLayerToLoadOnCompletion har ingen virkning ad den vej.
            fil = self._fix_geometri(fil)

            def efter_succes(_result):
                # Terrænet først, så strømningsvejene lander ovenpå det.
                self._tilfoej_lag_til_projekt([
                    (f'Hoejdemodel_{navn}',
                     os.path.join(outputfiler_sti, 'Hoejdemodel.tif')),
                    (f'Channel_Networks_{navn}',
                     os.path.join(outputfiler_sti, 'Channel_Networks.gpkg')),
                ])

            return self._kør_model(
                model_filnavn    = None,
                algoritme_id     = 'script:beregn_stroemningsveje',
                ekstra_params    = {
                    'PROJEKTOMRAADE': fil,
                    'TOKEN':          self.get_token(),
                    'HENT_IGEN':      bool(getattr(self, '_tving_dhm_download', False)),
                },
                knap             = self.btnKorHojdemodel,
                omraade_navn     = navn,
                outputfiler_sti  = outputfiler_sti,
                efter_succes     = efter_succes,
                vis_succes_besked = False,
            )
        self._kør_for_alle_aktive(
            'Beregn strømningsveje', byg, status_label='lblStatusHojdemodel',
            valgt_model_test=self._har_valgt_hojdemodel)

    def _har_valgt_hojdemodel(self, omraade_navn):
        """True hvis brugeren har valgt en eksisterende højdemodel for området."""
        from qgis.core import QgsSettings
        sti = QgsSettings().value(
            f'vaadomraade_modeller/valgt_hoejdemodel/{omraade_navn}', '')
        return bool(sti) and os.path.isfile(sti)

    def _kor_oplande(self):
        """Kører 'Udpeg oplande' (script:udpeg_oplande_n) for hvert afkrydsede
        projektområde og tilføjer Vandoplandet/Direkte_Opland som lag.

        Scriptet slår selv højdemodellen op i Outputfiler_<omraade> og
        vandløbsnetværk + hydrologiske tilpasninger op i pluginnets Grunddata, og
        skriver outputs til Outputfiler — derfor køres det direkte uden omsluttende
        model.

        Beregningen (Scripts/oplande.py, Whitebox) sporer totaloplandet opstrøms fra
        projektområdet, udpeger ét vandløbsopland pr. indløbspunkt hvor et kortlagt
        vandløb løber ind, og udleder det direkte opland som RESTAREAL — så
        direkte + Σ vandløbsoplande = totalopland altid holder. Tærsklen afgør hvor
        stort et tilløb skal være for at blive udpeget selvstændigt; mindre tilløb
        indgår i det direkte opland."""
        from qgis.core import QgsSettings
        taerskel = float(QgsSettings().value(
            'vaadomraade_modeller/vandloeb_taerskel_ha', 50.0))

        def byg(fil, navn, outputfiler_sti):
            # Sikkerhedsnet: ret ugyldig geometri lige før kørsel (også for
            # projektområder der blev valgt før auto-fixet blev tilføjet).
            fil = self._fix_geometri(fil)
            def efter_succes(_result):
                # Deloplandene foerst: det er dét lag man arbejder i. De to
                # samlede er leverancen, regnearket laeser — de vises stadig,
                # men deloplandene ligger oeverst.
                self._tilfoej_lag_til_projekt([
                    (f'Vandoplandet_{navn}',   os.path.join(outputfiler_sti, 'Vandoplandet.gpkg')),
                    (f'Direkte_Opland_{navn}', os.path.join(outputfiler_sti, 'Direkte_Opland.gpkg')),
                    (f'Deloplande_{navn}',     os.path.join(outputfiler_sti, 'Deloplande.gpkg')),
                ])
            return self._kør_model(
                model_filnavn    = None,
                algoritme_id     = 'script:udpeg_oplande_n',
                ekstra_params    = {
                    'PROJEKTOMRAADE': fil,
                    'TAERSKEL_HA':    taerskel,
                },
                knap             = self.btnKorOplande,
                omraade_navn     = navn,
                outputfiler_sti  = outputfiler_sti,
                efter_succes     = efter_succes,
                vis_succes_besked = False,
            )
        self._kør_for_alle_aktive('Udvælg oplande', byg, status_label='lblStatusOplande')

    @staticmethod
    def _stil_lag(lag):
        """Giver strømningsvejene en streg man kan se noget på.

        Et linjelag i tilfældig farve oven på et gråt terræn er svært at
        kontrollere. Blå streger med bredde efter Strahler-orden gør det til at
        se med det samme, om vandløbene ligger hvor de skal — som er hele
        grunden til at lagene bliver vist.
        """
        from qgis.core import (QgsLineSymbol, QgsProperty, QgsSymbolLayer,
                               QgsVectorLayer)

        if not isinstance(lag, QgsVectorLayer):
            return
        if 'ORDER' not in [f.name() for f in lag.fields()]:
            return
        try:
            symbol = QgsLineSymbol.createSimple(
                {'color': '31,120,180,255', 'width': '0.4'})
            symbol.symbolLayer(0).setDataDefinedProperty(
                QgsSymbolLayer.PropertyStrokeWidth,
                QgsProperty.fromExpression('0.2 + 0.25 * coalesce("ORDER", 1)'))
            lag.renderer().setSymbol(symbol)
            lag.triggerRepaint()
        except Exception:
            pass    # en stil der ikke kan sættes, må ikke vælte en kørsel

    def _tilfoej_lag_til_projekt(self, lag_liste):
        """
        Tilføjer en liste af (lagnavn, sti)-par som vector-lag i det aktuelle
        QGIS-projekt og gemmer projektet til <projektmappe>/<projektnavn>.qgz
        så lagene er bevaret næste gang projektet åbnes.
        """
        from qgis.core import QgsRasterLayer, QgsVectorLayer, QgsProject

        RASTER_EXT = {'.tif', '.tiff', '.sdat', '.asc', '.img', '.vrt'}

        projekt = QgsProject.instance()
        tilfoejet = []
        for navn, sti in lag_liste:
            if not os.path.isfile(sti):
                continue
            # Fjern eksisterende lag der peger på samme fil, så vi ikke får dubletter
            sti_norm = os.path.normpath(sti).lower()
            for lag in list(projekt.mapLayers().values()):
                kilde = lag.source().split('|', 1)[0]
                if os.path.normpath(kilde).lower() == sti_norm:
                    projekt.removeMapLayer(lag.id())
            if os.path.splitext(sti)[1].lower() in RASTER_EXT:
                lag = QgsRasterLayer(sti, navn)
            else:
                lag = QgsVectorLayer(sti, navn, 'ogr')
            if lag.isValid():
                projekt.addMapLayer(lag)
                tilfoejet.append(navn)
                self._stil_lag(lag)

        # Gem projektet hvis der er en projektfil knyttet
        projekt_fil = projekt.fileName()
        if projekt_fil and tilfoejet:
            projekt.write(projekt_fil)

    # ------------------------------------------------------------------
    # Automatisk lag-håndtering baseret på model-outputparametre
    # ------------------------------------------------------------------

    @staticmethod
    def _lae_model_outputs(model_sti, kun_standard=True):
        """
        Parser .model3-filen og returnerer:
          { param_navn: (display_navn, parameter_type) }
        for output-parametre af typen 'sink', 'vectorDestination', 'rasterDestination'.

        kun_standard=True  → kun outputs med create_by_default=True (bruges til QGIS-visning)
        kun_standard=False → ALLE outputs uanset create_by_default (bruges til sti-tildeling
                             så filer altid lander i Outputfiler selv om de ikke vises i QGIS)
        """
        import xml.etree.ElementTree as ET

        UDGANGSTYPER = {'sink', 'vectorDestination', 'rasterDestination'}
        outputs = {}
        try:
            tree = ET.parse(model_sti)
            root = tree.getroot()
        except Exception:
            return outputs

        for child in root:
            if child.get('name') == 'parameterDefinitions':
                for param in child:
                    p_navn = param.get('name')
                    if not p_navn:
                        continue
                    p_type        = None
                    display_navn  = p_navn
                    create_default = True
                    for item in param:
                        n = item.get('name')
                        if n == 'parameter_type':
                            p_type = item.get('value')
                        elif n == 'description':
                            display_navn = item.get('value') or p_navn
                        elif n == 'create_by_default':
                            create_default = item.get('value', 'true').lower() == 'true'
                    if p_type in UDGANGSTYPER and (not kun_standard or create_default):
                        outputs[p_navn] = (display_navn, p_type)
                break
        return outputs

    @staticmethod
    def _sanitize_filnavn(navn):
        """Erstatter tegn der er ugyldige i filnavne med underscore."""
        import re
        return re.sub(r'[\s<>:"/\\|?*()\[\]%&]+', '_', navn).strip('_') or 'output'

    def _auto_tilfoej_lag(self, result, output_meta, omraade_navn=''):
        """
        Tilføjer model-output-lag til QGIS efter en kørsel.
        Itererer over output_meta (fra _lae_model_outputs) og tjekker result-dict
        for tilsvarende filstier. Memory-lag og ikke-eksisterende filer springes over.
        Fjerner dubletter (samme kildefil) inden lag tilføjes.
        """
        from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

        RASTER_TYPER = {'rasterDestination'}
        RASTER_EXT   = {'.tif', '.tiff', '.sdat', '.asc', '.img', '.vrt'}

        projekt   = QgsProject.instance()
        tilfoejet = []

        for p_navn, (display_navn, p_type) in output_meta.items():
            sti = result.get(p_navn)
            if not isinstance(sti, str) or not os.path.isfile(sti):
                continue

            lag_navn = f'{display_navn}_{omraade_navn}' if omraade_navn else display_navn

            # Fjern eksisterende lag med samme kildefil
            sti_norm = os.path.normpath(sti).lower()
            for lag in list(projekt.mapLayers().values()):
                kilde = lag.source().split('|', 1)[0]
                if os.path.normpath(kilde).lower() == sti_norm:
                    projekt.removeMapLayer(lag.id())

            ext = os.path.splitext(sti)[1].lower()
            if p_type in RASTER_TYPER or ext in RASTER_EXT:
                lag = QgsRasterLayer(sti, lag_navn)
            else:
                lag = QgsVectorLayer(sti, lag_navn, 'ogr')

            if lag.isValid():
                projekt.addMapLayer(lag)
                tilfoejet.append(lag_navn)

        if tilfoejet:
            pf = QgsProject.instance().fileName()
            if pf:
                QgsProject.instance().write(pf)

    def _kor_klip_grunddata(self):
        """Kører Klip_Grunddata_Model.model3 for hvert afkrydsede projektområde."""
        def byg(fil, navn, outputfiler_sti):
            return self._kør_model(
                model_filnavn = 'Klip_Grunddata_Model.model3',
                ekstra_params = {
                    'projektomraade':    fil,
                    'jordbund_klippet':  os.path.join(outputfiler_sti, 'Jordbund_Klippet.gpkg'),
                    'marker_klippet':    os.path.join(outputfiler_sti, 'Marker_klippet.gpkg'),
                    'befaestet_areal':   os.path.join(outputfiler_sti, 'Befaestet_Areal.gpkg'),
                    'natur':             os.path.join(outputfiler_sti, 'Natur.gpkg'),
                    'omdriftsarealer':   os.path.join(outputfiler_sti, 'Omdriftsarealer.gpkg'),
                },
                knap             = self.btnKorKlipGrunddata,
                omraade_navn     = navn,
                outputfiler_sti  = outputfiler_sti,
                vis_succes_besked = False,
            )
        self._kør_for_alle_aktive('Klip grunddata', byg, status_label='lblStatusKlip')

    def _udled_oplande_fra_deloplande(self, outputfiler_sti, navn):
        """Skriver Vandoplandet.gpkg og Direkte_Opland.gpkg ud fra Deloplande.gpkg.

        Kaldes lige før de to regnearkstrin. Har brugeren flyttet et delopland ved
        at rette kolonnen `type`, slår det igennem her — uden et ekstra skridt der
        skal huskes. Findes deloplandslaget ikke (en kørsel fra før laget fandtes),
        røres de to filer ikke.
        """
        import importlib.util
        import sys

        sti = os.path.join(_PLUGIN_SCRIPTS, 'deloplande.py')
        if not os.path.isfile(sti):
            return
        modulnavn = '_deloplande_interface'
        modul = sys.modules.get(modulnavn)
        if modul is None:
            spec = importlib.util.spec_from_file_location(modulnavn, sti)
            modul = importlib.util.module_from_spec(spec)
            sys.modules[modulnavn] = modul
            spec.loader.exec_module(modul)
        try:
            modul.udled_leverance(
                outputfiler_sti,
                log=lambda b: self._vis_status_besked('Deloplande', b))
        except Exception as e:
            # Statuslinjen, ikke en popup: det her kaldes lige før et trin
            # køres, og en modal boks midt i kæden stopper alt og venter på
            # et klik — også når ingen sidder og ser på.
            self._vis_status_besked(
                'Deloplandene kunne ikke bruges',
                f'{navn}: {e} — de eksisterende oplandsfiler bruges i stedet.',
                fejl=True)

    def _kor_vandopland(self):
        """Kører model_grids_vandloeb_ny.model3 for hvert afkrydsede projektområde."""
        # Grunddata følger altid med pluginnet (Grunddata/-mappen i plugin-roden).
        grids_fil = os.path.join(_PLUGIN_GRUNDDATA, 'Samlet_Grid', 'GRIDS_samlet_258.shp')
        if not os.path.isfile(grids_fil):
            QMessageBox.warning(self, 'Fil mangler',
                f'GRIDS_samlet_258.shp blev ikke fundet:\n{grids_fil}\n\n'
                f'Filen skal ligge i pluginnets Grunddata/Samlet_Grid-mappe.')
            return

        def byg(fil, navn, outputfiler_sti):
            self._udled_oplande_fra_deloplande(outputfiler_sti, navn)
            vandopland_fil = os.path.join(outputfiler_sti, 'Vandoplandet.gpkg')
            if not os.path.isfile(vandopland_fil):
                QMessageBox.warning(self, 'Fil mangler',
                    f'Vandoplandet.gpkg blev ikke fundet for {navn}:\n{vandopland_fil}\n\n'
                    f'Kør først "Udvælg oplande model" for {navn}.')
                return False
            return self._kør_model(
                model_filnavn = 'model_grids_vandloeb_ny.model3',
                ekstra_params = {
                    'vandlbsopland': vandopland_fil,
                    'grids_samlet':  grids_fil,
                },
                knap             = self.btnKorVandopland,
                omraade_navn     = navn,
                outputfiler_sti  = outputfiler_sti,
                vis_succes_besked = False,
            )
        self._kør_for_alle_aktive('Vandopland', byg, status_label='lblStatusVandopland')

    def _kor_direkte_opland(self):
        """Kører Direkte_opland_model.model3 for hvert afkrydsede projektområde."""
        def byg(fil, navn, outputfiler_sti):
            self._udled_oplande_fra_deloplande(outputfiler_sti, navn)
            if not os.path.isdir(outputfiler_sti):
                QMessageBox.warning(self, 'Mappe mangler',
                    f'Outputfiler-mappen blev ikke fundet for {navn}:\n{outputfiler_sti}\n\n'
                    f'Kør først modellerne i Boks 2 for {navn}.')
                return False
            return self._kør_model(
                model_filnavn = 'Direkte_opland_model.model3',
                ekstra_params = {
                    'omdriftsarealer': os.path.join(outputfiler_sti, 'Direkte_Omdriftsarealer.gpkg'),
                },
                knap             = self.btnKorDirekteOpland,
                omraade_navn     = navn,
                outputfiler_sti  = outputfiler_sti,
                vis_succes_besked = False,
            )
        self._kør_for_alle_aktive('Direkte opland', byg, status_label='lblStatusDirekteOpland')

    def _grunddata_modul(self):
        """Indlæser Scripts/grunddata.py — modulet der henter de store datasæt."""
        import importlib.util
        import sys

        navn = '_grunddata_interface'
        modul = sys.modules.get(navn)
        if modul is not None:
            return modul
        sti = os.path.join(_PLUGIN_SCRIPTS, 'grunddata.py')
        if not os.path.isfile(sti):
            QMessageBox.critical(self, 'Fil mangler',
                                 'grunddata.py blev ikke fundet: ' + sti)
            return None
        spec = importlib.util.spec_from_file_location(navn, sti)
        modul = importlib.util.module_from_spec(spec)
        sys.modules[navn] = modul
        spec.loader.exec_module(modul)
        return modul

    def _kor_projektomraade(self):
        """Kører Projektomraade_excel_model.model3 for hvert afkrydsede projektområde."""
        # De tre datasæt er for store til plugin-pakken og hentes fra en release
        # første gang — brugeren bliver spurgt, og de gemmes i QGIS-profilen.
        gd = self._grunddata_modul()
        if gd is None or not gd.sikr(
                ['Marker_2024', 'Vandloeb', 'Befaestet_Areal'], forael=self):
            return
        marktema_fil    = gd.sti('Marker_2024')
        vandloeb_fil    = gd.sti('Vandloeb')
        befeastning_fil = gd.sti('Befaestet_Areal')

        manglende = [s for s in (marktema_fil, vandloeb_fil, befeastning_fil)
                     if not s or not os.path.isfile(s)]
        if manglende:
            QMessageBox.warning(self, 'Filer mangler',
                'Følgende kildefiler blev ikke fundet:\n  '
                + '\n  '.join(str(s) for s in manglende))
            return

        def byg(fil, navn, outputfiler_sti):
            return self._kør_model(
                model_filnavn = 'Projektomraade_excel_model.model3',
                ekstra_params = {
                    # vector-inputs
                    'projektomraade': fil,
                    'marktema':       marktema_fil,
                    'vandloeb2':      vandloeb_fil,
                    'befeastning':    befeastning_fil,
                    # sink-outputs (gemmes i Outputfiler-mappen)
                    'agerjord':       os.path.join(outputfiler_sti, 'Agerjord.gpkg'),
                    'brak':           os.path.join(outputfiler_sti, 'Brak.gpkg'),
                    'graes':          os.path.join(outputfiler_sti, 'Graes.gpkg'),
                    'natur_area':     os.path.join(outputfiler_sti, 'Natur_Area.gpkg'),
                    'befaestet':      os.path.join(outputfiler_sti, 'Befaestet.gpkg'),
                    'sammenlagt':     os.path.join(outputfiler_sti, 'Sammenlagt.gpkg'),
                    'vandloeb':       os.path.join(outputfiler_sti, 'Vandloeb.gpkg'),
                },
                knap             = self.btnKorProjektomraade3,
                omraade_navn     = navn,
                outputfiler_sti  = outputfiler_sti,
                vis_succes_besked = False,
            )
        self._kør_for_alle_aktive('Projektområde', byg, status_label='lblStatusProjektomraade3')

    def _outputfiler_sti(self):
        """Returnerer <rod>/<projektnavn>/<omraade>/Outputfiler_<omraade>/ for primært projektområde."""
        fil = self.get_projektomraade()
        return self._outputfiler_sti_for_fil(fil)

    def _outputfiler_sti_for_fil(self, fil):
        """Returnerer Outputfiler_<omraade>/ for et specifikt projektområde-fil."""
        rod = self.get_outputmappe()
        projektnavn = self.lineEditProjektnavn.text().strip()
        if not rod or not projektnavn or not fil:
            QMessageBox.warning(self, 'Manglende input',
                'Udfyld mappe, projektnavn og projektområde-fil først.')
            return None
        sikkert_projekt = _sanitize_naam(projektnavn)
        omraade = _sanitize_naam(os.path.splitext(os.path.basename(fil))[0])
        if not sikkert_projekt or not omraade:
            QMessageBox.warning(self, 'Ugyldigt navn',
                'Projektnavn eller filnavn indeholder kun ugyldige tegn.')
            return None
        return os.path.join(rod, sikkert_projekt, omraade, f'Outputfiler_{omraade}')

    def _aktive_projektomraader(self):
        """
        Returnerer liste af (fil_sti, omraade_navn, outputfiler_sti) for hvert
        afkrydsede projektområde — både det primære og evt. ekstra rækker.
        Brugeren kan fjerne flueben på områder de ikke vil køre i denne omgang.
        """
        result = []
        kandidater = []
        # Kun ét projektområde (ekstra-områder er fjernet). Tag altid det
        # primære, hvis feltet er udfyldt.
        fil = self.lineEditProjektomraade.text().strip()
        if fil:
            kandidater.append(fil)

        for fil in kandidater:
            navn = _sanitize_naam(os.path.splitext(os.path.basename(fil))[0])
            if not navn:
                continue
            sti = self._outputfiler_sti_for_fil(fil)
            if sti is None:
                continue
            result.append((fil, navn, sti))
        return result

    def _kør_for_alle_aktive(self, label, byg_kor_funktion, status_label=None,
                             valgt_model_test=None):
        """
        Itererer over alle afkrydsede projektområder, opdaterer QgsSettings og kalder
        byg_kor_funktion(fil, omraade_navn, outputfiler_sti) for hver — funktionen skal
        returnere True ved succes (eller None hvis den selv viste fejl).

        valgt_model_test: valgfri callable(omraade_navn) -> bool. Hvis den
          returnerer True for mindst ét succesfuldt område, vises status med
          "(valgt model)"-markøren (bruges til højdemodel-trinnet).

        Viser én samlet besked til sidst i stedet for én pr. iteration.
        status_label: navn på QLabel-widget der opdateres med ✓/✗ efter kørsel.
        """
        from qgis.core import QgsSettings

        aktive = self._aktive_projektomraader()
        if not aktive:
            QMessageBox.warning(self, 'Ingen projektområder valgt',
                'Markér mindst ét projektområde med flueben for at køre modellen.')
            return

        # Husk hvilket projektomraade der var "aktivt" så vi kan sætte det tilbage
        oprindeligt_aktiv = QgsSettings().value('vaadomraade_modeller/projektomraade_navn', '')
        # Meldingerne fra beregningen samles her og haenges paa trinnets flueben
        # som tooltip. Saa er de der naar man vil se dem, og i vejen naar man ikke vil.
        self._sidste_meldinger = []
        koerte = []
        fejlede = []
        brugte_valgt_model = False
        for fil, navn, outputfiler_sti in aktive:
            QgsSettings().setValue('vaadomraade_modeller/projektomraade_navn', navn)
            try:
                ok = byg_kor_funktion(fil, navn, outputfiler_sti)
                if ok:
                    koerte.append(navn)
                    if valgt_model_test is not None and valgt_model_test(navn):
                        brugte_valgt_model = True
                else:
                    fejlede.append(navn)
            except Exception as e:
                fejlede.append(f'{navn} ({e})')

        # Sæt det "aktive" projektomraade tilbage til seneste succesfulde, eller bevar oprindelige
        if koerte:
            QgsSettings().setValue('vaadomraade_modeller/projektomraade_navn', koerte[-1])
        elif oprindeligt_aktiv:
            QgsSettings().setValue('vaadomraade_modeller/projektomraade_navn', oprindeligt_aktiv)

        if status_label:
            self._sæt_status(status_label, koerte=koerte, fejlede=fejlede,
                             valgt_model=brugte_valgt_model)
            meldinger = getattr(self, '_sidste_meldinger', [])
            lbl = getattr(self, status_label, None)
            if lbl is not None:
                if meldinger:
                    lbl.setToolTip(
                        label + ':' + '\n\n'
                        + '\n'.join('• ' + m for m in meldinger)
                        + '\n\nDet hele står i Log-panelet under '
                        + '"N-regneark (LIMF)".')
                else:
                    lbl.setToolTip(label)

        if not getattr(self, '_skjul_samlet_besked', False):
            besked = f'{label} kørte på {len(koerte)} projektområde(r)'
            if fejlede:
                besked += f' — fejlede på {len(fejlede)}: ' + ', '.join(n.split(" (")[0] for n in fejlede)
            self._vis_status_besked('Færdig', besked, fejl=bool(fejlede))

    def _kor_ekstensivering(self):
        """Skriver Ekstensivering-værdien fra spinboxen til celle D56 i '2. Omsætning'
        i Resultat_<omraade>.xlsx for hvert afkrydsede projektområde."""
        vaerdi = self.spinEkstensivering.value()

        aktive = self._aktive_projektomraader()
        if not aktive:
            QMessageBox.warning(self, 'Ingen projektområder valgt',
                'Markér mindst ét projektområde med flueben.')
            return

        try:
            import openpyxl
        except ImportError:
            QMessageBox.critical(self, 'Manglende pakke',
                'Pakken "openpyxl" er ikke installeret.\n'
                'Åbn OSGeo4W Shell og kør: python -m pip install openpyxl')
            return

        koerte = []
        fejlede = []
        for fil, navn, _outputfiler_sti in aktive:
            excel_sti = self._resultat_xlsx_for_fil(fil)
            if not excel_sti:
                fejlede.append(f'{navn} (kunne ikke beregne Excel-sti)')
                continue
            if not os.path.isfile(excel_sti):
                fejlede.append(f'{navn} (Excel-fil ikke fundet: {excel_sti})')
                continue
            try:
                wb = openpyxl.load_workbook(excel_sti)
                ark_navn = '2. Omsætning'
                if ark_navn not in wb.sheetnames:
                    fejlede.append(f'{navn} (ark "{ark_navn}" ikke fundet)')
                    continue
                wb[ark_navn]['D56'] = vaerdi
                wb.save(excel_sti)
                koerte.append(navn)
            except Exception as e:
                fejlede.append(f'{navn} ({e})')

        self._sæt_status('lblStatusEkstensivering', koerte=koerte, fejlede=fejlede)
        if not getattr(self, '_skjul_samlet_besked', False):
            besked = f'Ekstensivering ({vaerdi}) skrevet til {len(koerte)} projektområde(r)'
            if fejlede:
                besked += f' — fejlede på {len(fejlede)}: ' + ', '.join(n.split(" (")[0] for n in fejlede)
            self._vis_status_besked('Færdig', besked, fejl=bool(fejlede))

    def _kor_overrisling(self):
        """Kører Nfjernelsesgrad_omsaet.model3 for hvert afkrydsede projektområde.

        Modellen indeholder en aggregate()-formel der refererer 'Direkte_Opland'
        ved lagnavn. _kor_oplande tilføjer laget med suffix
        (f.eks. 'Direkte_Opland_<navn>'), så vi modificerer modelfilen midlertidigt
        med det dynamiske navn før hver kørsel og restorer originalen bagefter
        — uanset om kørslen lykkes eller ej.
        """
        from qgis.core import QgsProject

        # Modellen følger altid med pluginnet (Modeller/-mappen i plugin-roden).
        model_sti = os.path.normpath(os.path.join(
            _PLUGIN_MODELLER, 'Nfjernelsesgrad_omsaet.model3'))
        if not os.path.isfile(model_sti):
            QMessageBox.critical(self, 'Fejl', f'Model ikke fundet:\n{model_sti}')
            return

        # Læs originalen én gang så vi kan restore den efter hver iteration
        try:
            with open(model_sti, 'r', encoding='utf-8') as f:
                original_indhold = f.read()
        except Exception as e:
            QMessageBox.critical(self, 'Fejl',
                f'Kunne ikke læse model-fil:\n{e}')
            return

        def byg(fil, navn, outputfiler_sti):
            forventet_lagnavn = f'Direkte_Opland_{navn}'
            if not any(l.name() == forventet_lagnavn
                       for l in QgsProject.instance().mapLayers().values()):
                raise RuntimeError(
                    f"Laget '{forventet_lagnavn}' findes ikke. "
                    f"Kør 'Udvælg oplande' først."
                )

            modificeret = original_indhold.replace(
                "layer:='Direkte_Opland'",
                f"layer:='{forventet_lagnavn}'",
            )
            if modificeret == original_indhold:
                raise RuntimeError(
                    "Kunne ikke finde 'Direkte_Opland' i modellen — intet at erstatte."
                )

            try:
                with open(model_sti, 'w', encoding='utf-8') as f:
                    f.write(modificeret)
            except Exception as e:
                raise RuntimeError(f'Kunne ikke skrive midlertidig model: {e}')

            try:
                return self._kør_model(
                    model_filnavn = 'Nfjernelsesgrad_omsaet.model3',
                    ekstra_params = {'projektomraade': fil},
                    knap             = self.btnKorOverrisling,
                    omraade_navn     = navn,
                    outputfiler_sti  = outputfiler_sti,
                    vis_succes_besked = False,
                )
            finally:
                # Restorer altid originalen — også hvis modellen fejler
                try:
                    with open(model_sti, 'w', encoding='utf-8') as f:
                        f.write(original_indhold)
                except Exception as e:
                    QMessageBox.critical(self, 'Kritisk fejl',
                        f'Kunne ikke restore model-fil!\n{model_sti}\n\n'
                        f'Genskab manuelt fra kildekoden.\n\nFejl: {e}')

        self._kør_for_alle_aktive('Overrisling', byg, status_label='lblStatusOverrisling')

    def _kor_retuner(self):
        """Kører Retuner_Data_Projektomraade.model3 for hvert afkrydsede projektområde
        og loader resultat-shapefilen i QGIS for hver.
        Modellen har ingen model-niveau outputparametre — output skrives af internt script
        til Resultater/<navn>.shp og tilføjes via efter_succes."""
        def byg(fil, navn, outputfiler_sti):
            resultater   = os.path.join(os.path.dirname(outputfiler_sti), 'Resultater')
            resultat_shp = os.path.join(resultater, f'Resultat_{navn}.shp')

            def efter_succes(_result):
                if os.path.isfile(resultat_shp):
                    self._tilfoej_lag_til_projekt([
                        (f'Resultat_{navn}', resultat_shp),
                    ])

            return self._kør_model(
                model_filnavn = 'Retuner_Data_Projektomraade.model3',
                ekstra_params = {
                    'projektomraade': fil,
                    'token':          self.get_token(),
                },
                knap             = self.btnRetuner,
                omraade_navn     = navn,
                outputfiler_sti  = outputfiler_sti,
                efter_succes     = efter_succes,
                vis_succes_besked = False,
            )

        self._kør_for_alle_aktive('Retuner data', byg, status_label='lblStatusRetuner')

    def _resultater_sti(self):
        """Returnerer <rod>/<projektnavn>/<omraade>/Resultater/ eller viser fejl."""
        sti = self._outputfiler_sti()
        if sti is None:
            return None
        return os.path.join(os.path.dirname(sti), 'Resultater')

    def _resultat_xlsx_for_fil(self, fil):
        """Returnerer stien til Resultat_<omraade>.xlsx for et givent projektomraade-fil,
        eller None hvis stien ikke kan beregnes."""
        rod = self.get_outputmappe()
        projektnavn = self.lineEditProjektnavn.text().strip()
        if not (rod and projektnavn and fil):
            return None
        sikkert_projekt = _sanitize_naam(projektnavn)
        omraade = _sanitize_naam(os.path.splitext(os.path.basename(fil))[0])
        if not (sikkert_projekt and omraade):
            return None
        return os.path.join(
            rod, sikkert_projekt, omraade, 'Resultater',
            f'Resultat_{omraade}.xlsx'
        )

    def _aaben_excel_fil(self, sti):
        """Åbner Excel-filen i den default applikation. Viser fejl hvis filen mangler."""
        if not os.path.isfile(sti):
            QMessageBox.warning(self, 'Fil mangler',
                f'Excel-resultatfilen findes ikke endnu:\n{sti}\n\n'
                f'Kør først modellerne der genererer den.')
            return
        try:
            os.startfile(sti)
        except Exception as e:
            QMessageBox.critical(self, 'Fejl ved åbning',
                f'Kunne ikke åbne filen:\n{sti}\n\n{e}')

    def _opdater_aaben_resultater_knapper(self):
        """Viser to knapper ved siden af hinanden for det primære projektområde:
        'Vis resultatet i GIS' (returnerer data til polygon-laget) og
        'Vis resultatet i regneark' (åbner Excel-resultatfilen)."""
        if not hasattr(self, 'aabenResultaterContainerLayout'):
            return  # UI'en har ikke containeren (gammel UI-fil)

        layout = self.aabenResultaterContainerLayout

        # Fjern eksisterende knapper
        while layout.count() > 0:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        # ── Knap 1: Vis resultatet i GIS (kører Retuner-modellen) ─────────────
        gis_knap = QtWidgets.QPushButton('Vis resultatet i GIS')
        gis_knap.setStyleSheet(self._TRIN_KNAP_STIL)
        gis_knap.setFixedWidth(self._KNAP_BREDDE)
        gis_knap.setToolTip('Returnerer de beregnede data til projektområde-'
                            'polygonet som et nyt lag i QGIS.')
        gis_knap.clicked.connect(lambda _checked=False: self._kor_retuner())
        layout.addWidget(gis_knap)

        # ── Knap 2: Vis resultatet i regneark (åbner Excel-filen) ─────────────
        excel_knap = QtWidgets.QPushButton('Vis resultatet i regneark')
        excel_knap.setStyleSheet(self._TRIN_KNAP_STIL)
        excel_knap.setFixedWidth(self._KNAP_BREDDE)
        primaer = self.lineEditProjektomraade.text().strip()
        excel_sti = self._resultat_xlsx_for_fil(primaer) if primaer else None
        if excel_sti is None:
            excel_knap.setEnabled(False)
            excel_knap.setToolTip('Udfyld mappe, projektnavn og projektområde først.')
        else:
            excel_knap.setToolTip(excel_sti)
            excel_knap.clicked.connect(
                lambda _checked=False, s=excel_sti: self._aaben_excel_fil(s))
        layout.addWidget(excel_knap)

    # ------------------------------------------------------------------
    # Låse-logik
    # ------------------------------------------------------------------

    def _opdater_laase(self):
        """
        Boks 2 låses op når alle tre felter i boks 1 er udfyldt.
        Boks 3 og 4 kræver desuden at Forberedende-data (boks 2) er kørt færdig
        for ALLE afkrydsede projektområder — dvs. de relevante output-filer
        eksisterer i hver Outputfiler_<omraade>-mappe.
        """
        # Token kræves IKKE længere for at låse op — det bruges kun til WCS-
        # download og spørges ved "Beregn strømningsveje" hvis det mangler.
        boks1_komplet = (
            bool(self.lineEditProjektomraade.text().strip()) and
            bool(self.lineEditMappe.text().strip())
        )

        self.groupBox2.setEnabled(boks1_komplet)

        forberedende_klar = boks1_komplet and self._forberedende_data_komplet()
        self.groupBox3.setEnabled(forberedende_klar)
        self.groupBox4.setEnabled(forberedende_klar)

    def _forberedende_data_komplet(self):
        """Stille tjek: returnerer True hvis alle afkrydsede projektområder har
        Forberedende-output-filerne (Hoejdemodel, Direkte_Opland, Marker_klippet).
        Tjekker filer direkte uden at kalde popups."""
        rod = self.lineEditMappe.text().strip()
        projektnavn = self.lineEditProjektnavn.text().strip()
        if not (rod and projektnavn):
            return False

        sikkert_projekt = _sanitize_naam(projektnavn)
        if not sikkert_projekt:
            return False

        # Saml alle afkrydsede projektomraade-filer
        filer = []
        try:
            if self.checkProjektomraade.isChecked():
                primaer = self.lineEditProjektomraade.text().strip()
                if primaer:
                    filer.append(primaer)
            for _lbl, check, felt, _kv, _kr in getattr(self, '_ekstra_raekker', []):
                if check.isChecked():
                    tekst = felt.text().strip()
                    if tekst:
                        filer.append(tekst)
        except Exception:
            return False

        if not filer:
            return False

        for fil in filer:
            omraade = _sanitize_naam(os.path.splitext(os.path.basename(fil))[0])
            if not omraade:
                return False
            outputfiler = os.path.join(
                rod, sikkert_projekt, omraade, f'Outputfiler_{omraade}'
            )
            hoejde_ok = (
                os.path.isfile(os.path.join(outputfiler, 'Hoejdemodel.sdat')) or
                os.path.isfile(os.path.join(outputfiler, 'Hoejdemodel.tif'))
            )
            oplande_ok = os.path.isfile(
                os.path.join(outputfiler, 'Direkte_Opland.gpkg')
            )
            klip_ok = os.path.isfile(
                os.path.join(outputfiler, 'Marker_klippet.gpkg')
            )
            if not (hoejde_ok and oplande_ok and klip_ok):
                return False

        return True

    def _opdater_projektomraade_laase(self):
        """Låser projektområde-feltet op når projektmappen findes på disken."""
        rod = self.lineEditMappe.text().strip()
        projektnavn = self.lineEditProjektnavn.text().strip()
        sikkert = _sanitize_naam(projektnavn)
        aaben = bool(rod) and bool(sikkert) and os.path.isdir(os.path.join(rod, sikkert))

        self.labelProjektomraade.setEnabled(aaben)
        self.hjaelpProjektomraade.setEnabled(aaben)
        self.lineEditProjektomraade.setEnabled(aaben)
        self.btnVaelgFil.setEnabled(aaben)

    # ------------------------------------------------------------------
    # Hjælpemetode: hent værdier fra boks 1
    # ------------------------------------------------------------------

    def get_projektomraade(self):
        return self.lineEditProjektomraade.text().strip()

    def get_token(self):
        return self.lineEditToken.text().strip()

    def get_outputmappe(self):
        return self.lineEditMappe.text().strip()


def run():
    """Start dialogen fra QGIS Python-konsol."""
    dialog = ModelMappeDialog()
    dialog.exec_()


# Kør direkte hvis scriptet eksekveres som __main__ (til test uden QGIS)
if __name__ == "__main__":
    import sys
    app = QtWidgets.QApplication(sys.argv)
    dialog = ModelMappeDialog()
    dialog.show()
    sys.exit(app.exec_())
