# -*- coding: utf-8 -*-
"""Dialog til valg af lodsejerlag og felt-mapping.

Workflow:
  * Brugeren vælger et eksisterende vektorlag ELLER browser til en fil.
  * Pluginnet auto-matcher datafelter til skabelonens pladsholdere
    (navn, adresse, postnr, løbenr) ud fra feltnavnene.
  * Felter pluginnet ikke kan matche med sikkerhed markeres, så brugeren
    selv kan vælge dem (eller lade pluginnet generere dem).
"""

import os
from datetime import date

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QGroupBox,
    QDialogButtonBox,
    QMessageBox,
    QWidget,
    QListWidget,
    QListWidgetItem,
    QCheckBox,
    QDoubleSpinBox,
    QTabWidget,
)
from qgis.PyQt.QtCore import Qt
from qgis.core import (
    QgsProject,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.gui import QgsColorButton

from . import faelles_ui
from .atlas_builder import (MIN_AREA_M2, STANDARD_FYLD, STANDARD_KANT,
                            STANDARD_OMRAADE)
from .oversigtskort import (PAPIR_A3, PAPIR_A4, STANDARD_GRAENSE,
                            UNIKKE_SYMBOLER, OversigtsValg)
from .reference_data import STANDARD_PERIODE, perioder
from .template_spec import PLACEHOLDERS, postnr_par

# Værdi i felt-combo der betyder "intet felt – lad pluginnet generere".
GENERATE_SENTINEL = "__generate__"
NONE_SENTINEL = "__none__"
# Baggrundskort: brug projektets rasterlag automatisk.
AUTO_BACKGROUND_SENTINEL = "__auto_bg__"
# Hvor i projektfilen valgene huskes til næste gang.
PROJEKT_SCOPE = "Limf_Atlas"
PROJEKT_EKSTRA_LAG = "ekstra_lag"
PROJEKT_PERIODE = "reference_periode"
PROJEKT_AREALFILTER = "arealfilter_til"
PROJEKT_MIN_AREAL = "arealfilter_m2"
PROJEKT_OV_TITEL = "oversigt_titel"
PROJEKT_OV_PROJEKTNAVN = "oversigt_projektnavn"
PROJEKT_OV_SAGSNUMMER = "oversigt_sagsnummer"
PROJEKT_OV_PAPIR = "oversigt_papir"
PROJEKT_OV_LOBENR = "oversigt_lobenr"
# Det der følger personen frem for projektet, huskes i QGIS' indstillinger.
BRUGER_INITIALER = "Limf_Atlas/oversigt_initialer"
BRUGER_LOGO = "Limf_Atlas/oversigt_logo"
BRUGER_FANE = "Limf_Atlas/fane"

# Fanerne nederst i dialogen.
FANE_ATLAS = 0
FANE_OVERSIGT = 1

#: Hvad dialogen skal bygge.
BYG_ATLAS = "atlas"
BYG_OVERSIGT = "oversigt"


def _bruger_vaerdi(noegle, standard):
    try:
        return QgsSettings().value(noegle, standard)
    except Exception:
        return standard


def _gem_bruger_vaerdi(noegle, vaerdi):
    try:
        QgsSettings().setValue(noegle, vaerdi)
    except Exception:
        pass


class AtlasDialogResult:
    """Brugerens valg, leveret tilbage til hovedklassen."""

    def __init__(self):
        self.layer = None
        #: dict: placeholder.key -> feltnavn i laget (eller None)
        self.field_mapping = {}
        self.generate_lobenr = False
        self.generate_postnr = False
        self.layout_name = "Lodsejer Atlas"
        #: Feltet der grupperer matrikler til samme ejer (én atlas-side pr. ejer).
        self.owner_field = None
        #: Lag der definerer projektområdets udstrækning (oversigtskort), eller None.
        self.project_area_layer = None
        #: Baggrundskort: True = auto (rasterlag), None = intet, ellers et lag.
        self.background_auto = True
        self.background_layer = None
        #: Referencekortets periode til arealtabellen ("2021-2023"/"2017-2021").
        self.reference_periode = STANDARD_PERIODE
        #: Mindste areal (m²) i projektgrænsen for at en ejer får en side.
        #: 0 = intet arealfilter, alle lodsejere kommer med. Det er standarden.
        self.min_area_m2 = 0.0
        #: Projektets lag der skal med i kortene og signaturforklaringen,
        #: top-først som i lagpanelet (fx genslyngning og andre tiltag).
        self.extra_layers = []
        #: Farver på matrikler og projektgrænse (QColor).
        self.matrikel_kant = STANDARD_KANT
        self.matrikel_fyld = STANDARD_FYLD
        self.omraade_kant = STANDARD_OMRAADE
        #: BYG_ATLAS eller BYG_OVERSIGT — hvilken fane brugeren byggede fra.
        self.byg = BYG_ATLAS
        #: Valgene under fanen «Oversigtskort» (OversigtsValg).
        self.oversigt = OversigtsValg()


class AtlasDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._result = AtlasDialogResult()
        #: placeholder.key -> QComboBox
        self._field_combos = {}
        #: placeholder.key -> QLabel (statusmærkat)
        self._status_labels = {}
        self._current_layer = None
        #: Lag indlæst fra fil (skal tilføjes projektet ved accept).
        self._loaded_from_file = None

        self.setWindowTitle("Byg lodsejer-atlas")
        self.setMinimumWidth(520)
        self._build_ui()
        self._populate_project_layers()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Vælg lodsejerlaget og bekræft hvilke felter der skal vises som "
            "dynamisk tekst i atlasset. Felter foreslås automatisk – ret dem "
            "om nødvendigt."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- Datakilde -------------------------------------------------
        src_box = QGroupBox("1. Lodsejerlag")
        src_layout = QVBoxLayout(src_box)

        proj_row = QHBoxLayout()
        proj_row.addWidget(QLabel("Lag i projektet:"))
        self.layer_combo = QComboBox()
        self.layer_combo.currentIndexChanged.connect(self._on_project_layer_changed)
        proj_row.addWidget(self.layer_combo, 1)
        src_layout.addLayout(proj_row)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("…eller fra fil:"))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("Ingen fil valgt")
        file_row.addWidget(self.file_edit, 1)
        browse_btn = QPushButton("Gennemse…")
        browse_btn.clicked.connect(self._on_browse)
        file_row.addWidget(browse_btn)
        src_layout.addLayout(file_row)

        layout.addWidget(src_box)

        # --- Felt-mapping ---------------------------------------------
        map_box = QGroupBox("2. Felt-mapping (dynamisk tekst)")
        self.map_form = QFormLayout(map_box)
        for ph in PLACEHOLDERS:
            combo = QComboBox()
            status = QLabel("")
            self._field_combos[ph.key] = combo
            self._status_labels[ph.key] = status

            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.addWidget(combo, 1)
            row_l.addWidget(status)

            label = ph.label + (" *" if ph.required else "")
            self.map_form.addRow(label, row)
        layout.addWidget(map_box)

        # --- Gruppering & kortgrundlag (fælles for atlas og oversigt) -----
        grp_box = QGroupBox("3. Gruppering og kortgrundlag")
        grp_form = QFormLayout(grp_box)

        self.owner_combo = QComboBox()
        grp_form.addRow("Gruppér pr. ejer (felt) *", self.owner_combo)

        self.area_combo = QComboBox()
        grp_form.addRow("Projektområde-lag", self.area_combo)

        self.background_combo = QComboBox()
        grp_form.addRow("Baggrundskort-lag", self.background_combo)

        grp_hint = QLabel(
            "Alle ejerens matrikler samles: én side pr. ejer i atlasset, én "
            "farve og skravering pr. ejer på oversigtskortet. Kortene zoomer "
            "til projektområde-laget. Baggrundskort: «Auto» bruger projektets "
            "rasterlag, eller vælg et bestemt lag."
        )
        grp_hint.setWordWrap(True)
        grp_hint.setStyleSheet("color: gray;")
        grp_form.addRow(grp_hint)
        layout.addWidget(grp_box)

        # --- Ekstra lag ------------------------------------------------
        lag_boks = QGroupBox("4. Lag i kortet")
        lag_layout = QVBoxLayout(lag_boks)
        self.ekstra_liste = QListWidget()
        self.ekstra_liste.setMaximumHeight(130)
        lag_layout.addWidget(self.ekstra_liste)
        lag_hint = QLabel(
            "Sæt flueben ved de lag, der skal med i kortet — fx "
            "genslyngning eller andre tiltag. De tegnes med den stil, de "
            "har i projektet, og kommer med i signaturforklaringen sammen "
            "med projektgrænse og lodsejere. Baggrundskortet vises ikke i "
            "forklaringen."
        )
        lag_hint.setWordWrap(True)
        lag_hint.setStyleSheet("color: gray;")
        lag_layout.addWidget(lag_hint)
        layout.addWidget(lag_boks)

        # --- Hvad der skal bygges: atlas eller oversigtskort ------------
        self.faner = QTabWidget()
        self.faner.addTab(self._atlas_fane(), "Atlas – én side pr. lodsejer")
        self.faner.addTab(self._oversigt_fane(), "Oversigtskort")
        layout.addWidget(self.faner)

        hint = QLabel(
            "* Påkrævet. Mangler et felt i dataen, kan løbenummer og "
            "postnr./by genereres automatisk (vælg «Generér automatisk»)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

        # --- Knapper ---------------------------------------------------
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Byg atlas")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annullér")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        # Åbn på den fane, der blev brugt sidst, med den rigtige knaptekst.
        self.faner.currentChanged.connect(self._on_fane)
        fane = _bruger_vaerdi(BRUGER_FANE, 0)
        try:
            fane = int(fane)
        except (TypeError, ValueError):
            fane = 0
        self.faner.setCurrentIndex(fane if 0 <= fane < self.faner.count() else 0)
        self._on_fane(self.faner.currentIndex())

        faelles_ui.anvend_stil(self)

    def _atlas_fane(self):
        """Fanen med det, der kun gælder atlasset."""
        fane = QWidget()
        fane_layout = QVBoxLayout(fane)

        areal_boks = QGroupBox("Arealtabel")
        areal_form = QFormLayout(areal_boks)
        self.periode_combo = QComboBox()
        for p in perioder():
            self.periode_combo.addItem(p, p)
        self._vaelg_husket_periode()
        areal_form.addRow("Referencekort", self.periode_combo)

        # Arealfilter: til/fra + grænsen. Kræver et projektområde-lag.
        areal_raekke = QWidget()
        areal_layout = QHBoxLayout(areal_raekke)
        areal_layout.setContentsMargins(0, 0, 0, 0)
        self.arealfilter_box = QCheckBox("Udelad ejere under")
        self.areal_spin = QDoubleSpinBox()
        self.areal_spin.setRange(1.0, 1000000.0)
        self.areal_spin.setDecimals(0)
        self.areal_spin.setSingleStep(100.0)
        self.areal_spin.setSuffix(" m²")
        self.areal_spin.setToolTip(
            "Arealet måles pr. ejer – alle ejerens matrikler lagt sammen – og "
            "kun det der ligger inden for projektgrænsen.\n"
            "1.000 m² = 0,1 ha.")
        areal_layout.addWidget(self.arealfilter_box)
        areal_layout.addWidget(self.areal_spin)
        areal_layout.addStretch(1)
        self.arealfilter_box.toggled.connect(self.areal_spin.setEnabled)
        self._vaelg_husket_arealfilter()
        areal_form.addRow("Arealfilter", areal_raekke)

        areal_hint = QLabel(
            "Arealtabellen (omdrift/græs/andet) udfyldes fra "
            "Landbrugsstyrelsens referencekort for den valgte periode – "
            "årstallene står i tabellens overskrift. Hvert kort hentes én "
            "gang og gemmes lokalt. Arealfilteret er slået fra, så alle "
            "lodsejere får en side; slå det til for at udelade dem der kun "
            "rager en smule ind i projektet (kræver et projektområde-lag)."
        )
        areal_hint.setWordWrap(True)
        areal_hint.setStyleSheet("color: gray;")
        areal_form.addRow(areal_hint)
        fane_layout.addWidget(areal_boks)

        farve_boks = QGroupBox("Farver")
        farve_form = QFormLayout(farve_boks)

        self.kant_btn = QgsColorButton()
        self.kant_btn.setColor(STANDARD_KANT)
        self.kant_btn.setDefaultColor(STANDARD_KANT)
        self.kant_btn.setColorDialogTitle("Farve på matrikelkanten")
        farve_form.addRow("Matrikler – kant:", self.kant_btn)

        self.fyld_btn = QgsColorButton()
        self.fyld_btn.setAllowOpacity(True)
        self.fyld_btn.setColor(STANDARD_FYLD)
        self.fyld_btn.setDefaultColor(STANDARD_FYLD)
        self.fyld_btn.setColorDialogTitle("Fyldfarve i matriklerne")
        farve_form.addRow("Matrikler – fyld:", self.fyld_btn)

        self.omraade_btn = QgsColorButton()
        self.omraade_btn.setColor(STANDARD_OMRAADE)
        self.omraade_btn.setDefaultColor(STANDARD_OMRAADE)
        self.omraade_btn.setColorDialogTitle("Farve på projektgrænsen")
        farve_form.addRow("Projektgrænse:", self.omraade_btn)

        farve_hint = QLabel(
            "Fyldet er gennemsigtigt som standard, så baggrundskortet kan "
            "ses gennem matriklen. Vælg en fyldfarve — og skru op for "
            "uigennemsigtigheden — hvis matriklerne skal være farvelagte."
        )
        farve_hint.setWordWrap(True)
        farve_hint.setStyleSheet("color: gray;")
        farve_form.addRow(farve_hint)
        fane_layout.addWidget(farve_boks)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Navn på layout:"))
        self.name_edit = QLineEdit(self._result.layout_name)
        name_row.addWidget(self.name_edit, 1)
        fane_layout.addLayout(name_row)
        fane_layout.addStretch(1)
        return fane

    def _oversigt_fane(self):
        """Fanen med oversigtskortet: ét kort med alle lodsejere."""
        projekt = QgsProject.instance()
        fane = QWidget()
        fane_layout = QVBoxLayout(fane)

        intro = QLabel(
            "Ét kort over hele projektet, hvor hver lodsejer har sin egen "
            "farve og skravering. Til højre står titel, en "
            "signaturforklaring med lodsejernes navn og adresse, målestok, "
            "sagsoplysninger og logo. Op til {} lodsejere får hver sin "
            "kombination; ved flere gentages de, og løbenumre i fladerne "
            "anbefales.\n\n"
            "Symbolerne kan rettes bagefter: tryk «Ret symboler…» i Layout "
            "Designer, eller højreklik laget «Lodsejere» i lagpanelet → "
            "Egenskaber → Symbologi. Kortet og forklaringen følger med."
            .format(UNIKKE_SYMBOLER)
        )
        intro.setWordWrap(True)
        fane_layout.addWidget(intro)

        tekst_boks = QGroupBox("Tekst på kortet")
        tekst_form = QFormLayout(tekst_boks)

        def projekt_tekst(noegle, standard=""):
            vaerdi, _ok = projekt.readEntry(PROJEKT_SCOPE, noegle, standard)
            return vaerdi or standard

        self.ov_titel = QLineEdit(projekt_tekst(PROJEKT_OV_TITEL, "Lodsejerkort"))
        tekst_form.addRow("Titel:", self.ov_titel)
        self.ov_projektnavn = QLineEdit(projekt_tekst(PROJEKT_OV_PROJEKTNAVN))
        self.ov_projektnavn.setPlaceholderText("fx 100-1 Storarden VLP")
        tekst_form.addRow("Projektnavn:", self.ov_projektnavn)
        self.ov_sagsnummer = QLineEdit(projekt_tekst(PROJEKT_OV_SAGSNUMMER))
        self.ov_sagsnummer.setPlaceholderText("fx SBSYS-sagsnummer")
        tekst_form.addRow("Sagsnummer:", self.ov_sagsnummer)
        self.ov_initialer = QLineEdit(str(_bruger_vaerdi(BRUGER_INITIALER, "") or ""))
        tekst_form.addRow("Initialer:", self.ov_initialer)
        self.ov_dato = QLineEdit(date.today().strftime("%d.%m.%Y"))
        tekst_form.addRow("Dato:", self.ov_dato)
        fane_layout.addWidget(tekst_boks)

        udseende_boks = QGroupBox("Udseende")
        udseende_form = QFormLayout(udseende_boks)

        self.ov_papir = QComboBox()
        self.ov_papir.addItem("A3 liggende", PAPIR_A3)
        self.ov_papir.addItem("A4 liggende", PAPIR_A4)
        i = self.ov_papir.findData(projekt_tekst(PROJEKT_OV_PAPIR, PAPIR_A3))
        self.ov_papir.setCurrentIndex(max(i, 0))
        udseende_form.addRow("Papir:", self.ov_papir)

        self.ov_graense_btn = QgsColorButton()
        self.ov_graense_btn.setColor(STANDARD_GRAENSE)
        self.ov_graense_btn.setDefaultColor(STANDARD_GRAENSE)
        self.ov_graense_btn.setColorDialogTitle("Farve på projektgrænsen")
        udseende_form.addRow("Projektgrænse:", self.ov_graense_btn)

        lobenr, _ok = projekt.readBoolEntry(
            PROJEKT_SCOPE, PROJEKT_OV_LOBENR, False)
        self.ov_lobenr = QCheckBox("Løbenummer i fladerne og i forklaringen")
        self.ov_lobenr.setChecked(lobenr)
        self.ov_lobenr.setToolTip(
            "Samme numre som atlassets sider. Forklaringen sorteres så "
            "efter nummer i stedet for alfabetisk.")
        udseende_form.addRow("", self.ov_lobenr)

        logo_raekke = QWidget()
        logo_layout = QHBoxLayout(logo_raekke)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        self.ov_logo = QLineEdit(str(_bruger_vaerdi(BRUGER_LOGO, "") or ""))
        self.ov_logo.setPlaceholderText("Intet logo")
        logo_layout.addWidget(self.ov_logo, 1)
        logo_knap = QPushButton("Gennemse…")
        logo_knap.clicked.connect(self._on_vaelg_logo)
        logo_layout.addWidget(logo_knap)
        udseende_form.addRow("Logo:", logo_raekke)
        fane_layout.addWidget(udseende_boks)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Navn på layout:"))
        self.ov_navn_edit = QLineEdit("Lodsejerkort")
        name_row.addWidget(self.ov_navn_edit, 1)
        fane_layout.addLayout(name_row)
        fane_layout.addStretch(1)
        return fane

    def _on_vaelg_logo(self):
        start = self.ov_logo.text().strip()
        sti, _ = QFileDialog.getOpenFileName(
            self, "Vælg logo", os.path.dirname(start) if start else "",
            "Billeder (*.png *.jpg *.jpeg *.svg *.bmp *.gif);;Alle filer (*.*)")
        if sti:
            self.ov_logo.setText(sti)

    def _on_fane(self, indeks):
        oversigt = indeks == FANE_OVERSIGT
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Byg oversigtskort" if oversigt else "Byg atlas")

    # -------------------------------------------------------------- layers
    def _populate_project_layers(self):
        layers = list(QgsProject.instance().mapLayers().values())
        vectors = [l for l in layers
                   if isinstance(l, QgsVectorLayer) and l.isValid()]

        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        self.layer_combo.addItem("— vælg lag —", None)
        for lyr in vectors:
            self.layer_combo.addItem(lyr.name(), lyr.id())
        self.layer_combo.blockSignals(False)

        # Projektområde-laget (PG / projektgrænse).
        self.area_combo.clear()
        self.area_combo.addItem("(brug lodsejerlagets udstrækning)", None)
        for lyr in vectors:
            self.area_combo.addItem(lyr.name(), lyr.id())

        # Baggrundskort: Auto (rasterlag) eller et bestemt lag (raster/vektor).
        self.background_combo.clear()
        self.background_combo.addItem(
            "Auto (projektets rasterlag)", AUTO_BACKGROUND_SENTINEL)
        self.background_combo.addItem("(intet baggrundskort)", None)
        for lyr in layers:
            if lyr.isValid():
                self.background_combo.addItem(lyr.name(), lyr.id())

        self._udfyld_ekstra_lag()

        # Auto-gæt de rigtige lag til hver menu ud fra lagnavnet.
        self._guess_into(self.layer_combo, vectors,
                         ["lodsejer", "ejer", "matrik", "jordstykke"])
        area_guess = self._guess_into(self.area_combo, vectors,
                                      ["projektgr", "projektomr", "pg", "grænse",
                                       "graense", "afgr"])
        self._guess_into(self.background_combo, layers,
                         ["orto", "geodanmark", "geodk", "dtk", "skærmkort",
                          "skaermkort", "luftfoto", "wmts", "wms"])

        # Hvis vi gættede et lodsejerlag, så fyld felt-mapping ud fra det.
        guessed_layer_id = self.layer_combo.currentData()
        if guessed_layer_id:
            lyr = QgsProject.instance().mapLayer(guessed_layer_id)
            if lyr is not None:
                self._set_active_layer(lyr)

    def _vaelg_husket_periode(self):
        """Sæt periode-comboen til sidste valg i dette projekt.

        Huskes samme sted som de ekstra lag, så et nyt atlas over samme
        projekt bruger samme referencekort uden at skulle vælges igen.
        """
        husket, _ok = QgsProject.instance().readEntry(
            PROJEKT_SCOPE, PROJEKT_PERIODE, STANDARD_PERIODE)
        i = self.periode_combo.findData(husket)
        if i < 0:
            i = self.periode_combo.findData(STANDARD_PERIODE)
        if i >= 0:
            self.periode_combo.setCurrentIndex(i)

    def _vaelg_husket_arealfilter(self):
        """Sæt arealfilteret til sidste valg i dette projekt.

        Er der intet husket, er filteret slået FRA – alle lodsejere kommer
        med, og spinboksen står klar på MIN_AREA_M2, hvis man slår det til.

        Grænsen gemmes som hele m² – writeEntry har ingen float-overload i
        PyQGIS, og spinboksen har ingen decimaler, så der tabes intet.
        """
        projekt = QgsProject.instance()
        til, _ok = projekt.readBoolEntry(
            PROJEKT_SCOPE, PROJEKT_AREALFILTER, False)
        m2, _ok2 = projekt.readNumEntry(
            PROJEKT_SCOPE, PROJEKT_MIN_AREAL, int(MIN_AREA_M2))
        if m2 < self.areal_spin.minimum():
            m2 = MIN_AREA_M2
        self.areal_spin.setValue(m2)
        self.arealfilter_box.setChecked(til)
        self.areal_spin.setEnabled(til)

    @staticmethod
    def _gem_arealfilter(til, m2):
        """Husk arealfilteret i projektfilen. Grænsen gemmes som hele m²."""
        projekt = QgsProject.instance()
        projekt.writeEntry(PROJEKT_SCOPE, PROJEKT_AREALFILTER, bool(til))
        projekt.writeEntry(PROJEKT_SCOPE, PROJEKT_MIN_AREAL, int(round(m2)))

    def _udfyld_ekstra_lag(self):
        """Projektets vektorlag i lagpanelets rækkefølge, med afkrydsning.

        Sidste valg huskes i projektfilen, så et nyt atlas over samme
        projekt får de samme lag med.
        """
        projekt = QgsProject.instance()
        husket, _ok = projekt.readListEntry(PROJEKT_SCOPE, PROJEKT_EKSTRA_LAG)
        husket = set(husket or [])
        self.ekstra_liste.clear()
        for knude in projekt.layerTreeRoot().findLayers():
            lyr = knude.layer()
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            element = QListWidgetItem(lyr.name())
            element.setData(Qt.ItemDataRole.UserRole, lyr.id())
            element.setFlags(element.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            element.setCheckState(
                Qt.CheckState.Checked if lyr.id() in husket else Qt.CheckState.Unchecked)
            if not knude.isVisible():
                element.setToolTip("Laget er slukket i kortet lige nu, men "
                                   "kommer med i atlasset, hvis det er valgt.")
            self.ekstra_liste.addItem(element)

    def _valgte_ekstra_lag(self):
        """De afkrydsede lag, top-først, uden lodsejer- og projektområdelag."""
        projekt = QgsProject.instance()
        udelad = set()
        if self._current_layer is not None:
            udelad.add(self._current_layer.id())
        if self.area_combo.currentData():
            udelad.add(self.area_combo.currentData())
        lag = []
        for i in range(self.ekstra_liste.count()):
            element = self.ekstra_liste.item(i)
            if element.checkState() != Qt.CheckState.Checked:
                continue
            lyr = projekt.mapLayer(element.data(Qt.ItemDataRole.UserRole))
            if lyr is not None and lyr.id() not in udelad:
                lag.append(lyr)
        return lag

    @staticmethod
    def _guess_into(combo, candidate_layers, hints):
        """Vælg i combo det lag hvis navn bedst matcher hints. Returnér lagets id.

        Matcher case-insensitivt; eksakt/præfiks-substreng på et tidligt hint
        foretrækkes. Rører ikke combo hvis intet match.
        """
        best_id = None
        best_rank = None
        for lyr in candidate_layers:
            name = lyr.name().lower()
            for rank, hint in enumerate(hints):
                if hint in name:
                    if best_rank is None or rank < best_rank:
                        best_rank = rank
                        best_id = lyr.id()
                    break
        if best_id is not None:
            idx = combo.findData(best_id)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        return best_id

    def _on_project_layer_changed(self, _index):
        layer_id = self.layer_combo.currentData()
        if not layer_id:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer:
            self.file_edit.clear()
            self._loaded_from_file = None
            self._set_active_layer(layer)

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Vælg lodsejerlag",
            "",
            "Vektorlag (*.shp *.gpkg *.geojson *.json);;Alle filer (*.*)",
        )
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            QMessageBox.warning(
                self, "Atlas Mapbook", "Kunne ikke indlæse laget:\n{}".format(path)
            )
            return
        self.file_edit.setText(path)
        # Nulstil projekt-combo til "intet valgt".
        self.layer_combo.blockSignals(True)
        self.layer_combo.setCurrentIndex(0)
        self.layer_combo.blockSignals(False)
        self._loaded_from_file = layer
        self._set_active_layer(layer)

    def _set_active_layer(self, layer):
        """Opdatér felt-combos og kør auto-matching for det valgte lag."""
        self._current_layer = layer
        field_names = [f.name() for f in layer.fields()]

        for ph in PLACEHOLDERS:
            combo = self._field_combos[ph.key]
            combo.blockSignals(True)
            combo.clear()

            if ph.generated:
                combo.addItem("Generér automatisk", GENERATE_SENTINEL)
            if not ph.required:
                combo.addItem("(ingen)", NONE_SENTINEL)
            for name in field_names:
                combo.addItem(name, name)

            guess = self._auto_match(ph, field_names)
            if ph.key == "postnr" and postnr_par(field_names):
                # Postnummer og by ligger i hver sin kolonne. Vaelges den
                # bare postnummer-kolonne, falder byen ud af etiketten —
                # generatoren saetter de to sammen i stedet.
                guess = None
            self._apply_guess(ph, combo, guess)
            combo.blockSignals(False)

        # Ejer-grupperingsfelt: default = det felt der blev matchet til "navn".
        self.owner_combo.blockSignals(True)
        self.owner_combo.clear()
        for name in field_names:
            self.owner_combo.addItem(name, name)
        navn_combo = self._field_combos.get("navn")
        default_owner = navn_combo.currentData() if navn_combo else None
        if default_owner:
            idx = self.owner_combo.findData(default_owner)
            if idx >= 0:
                self.owner_combo.setCurrentIndex(idx)
        self.owner_combo.blockSignals(False)

    # ----------------------------------------------------------- matching
    @staticmethod
    def _auto_match(placeholder, field_names):
        """Returnér bedste gæt på feltnavn for en pladsholder, eller None.

        Matcher case-insensitivt mod placeholder.match_hints. Eksakt eller
        præfiks-match foretrækkes over delstrengs-match.

        Korte hints ("nr", "id", "by") skal passe eksakt. Som delstreng
        fandt "nr" feltet postnr, og Lodsejerudtrækkets lag fik postnumrene
        som løbenumre — siderne hed 9000, 9240 osv.
        """
        lowered = {name.lower(): name for name in field_names}
        lange = [h for h in placeholder.match_hints if len(h) > 2]

        # 1) eksakt match på et hint
        for hint in placeholder.match_hints:
            if hint in lowered:
                return lowered[hint]
        # 2) feltnavn starter med et hint (DBF afkorter til 10 tegn)
        for hint in lange:
            for low, original in lowered.items():
                if low.startswith(hint) or (len(low) > 2 and hint.startswith(low)):
                    return original
        # 3) hint optræder som delstreng
        for hint in lange:
            for low, original in lowered.items():
                if hint in low:
                    return original
        return None

    def _apply_guess(self, placeholder, combo, guess):
        status = self._status_labels[placeholder.key]
        if guess is not None:
            idx = combo.findData(guess)
            if idx >= 0:
                combo.setCurrentIndex(idx)
                self._mark_status(status, "auto", "✓ auto-matchet")
                return
        # Intet sikkert match.
        if placeholder.generated:
            idx = combo.findData(GENERATE_SENTINEL)
            combo.setCurrentIndex(max(idx, 0))
            self._mark_status(status, "generate", "↻ genereres")
        else:
            combo.setCurrentIndex(0)
            self._mark_status(status, "warn", "! vælg felt")

    @staticmethod
    def _mark_status(label, kind, text):
        colors = {"auto": "#2e7d32", "generate": "#1565c0", "warn": "#c62828"}
        label.setText(text)
        label.setStyleSheet("color: {}; font-weight: bold;".format(
            colors.get(kind, "#000000")
        ))

    # ------------------------------------------------------------- accept
    def _on_accept(self):
        if self._current_layer is None:
            QMessageBox.warning(
                self, "Atlas Mapbook", "Vælg venligst et lodsejerlag først."
            )
            return

        mapping = {}
        generate_lobenr = False
        generate_postnr = False

        for ph in PLACEHOLDERS:
            data = self._field_combos[ph.key].currentData()
            if data == GENERATE_SENTINEL:
                mapping[ph.key] = None
                if ph.key == "lobenr":
                    generate_lobenr = True
                elif ph.key == "postnr":
                    generate_postnr = True
            elif data == NONE_SENTINEL or data is None:
                if ph.required:
                    QMessageBox.warning(
                        self,
                        "Atlas Mapbook",
                        "Feltet «{}» er påkrævet – vælg et felt.".format(ph.label),
                    )
                    return
                mapping[ph.key] = None
            else:
                mapping[ph.key] = data

        owner_field = self.owner_combo.currentData()
        if not owner_field:
            QMessageBox.warning(
                self, "Atlas Mapbook", "Vælg et felt at gruppere ejere på."
            )
            return

        self._result.layer = self._current_layer
        self._result.field_mapping = mapping
        self._result.generate_lobenr = generate_lobenr
        self._result.generate_postnr = generate_postnr
        self._result.owner_field = owner_field
        self._result.layout_name = self.name_edit.text().strip() or "Lodsejer Atlas"
        self._result.matrikel_kant = self.kant_btn.color()
        self._result.matrikel_fyld = self.fyld_btn.color()
        self._result.omraade_kant = self.omraade_btn.color()

        # Tilføj fil-lag til projektet så atlasset kan referere det.
        if self._loaded_from_file is not None:
            QgsProject.instance().addMapLayer(self._loaded_from_file)

        area_id = self.area_combo.currentData()
        self._result.project_area_layer = (
            QgsProject.instance().mapLayer(area_id) if area_id else None
        )

        self._result.extra_layers = self._valgte_ekstra_lag()
        valgte_id = [
            self.ekstra_liste.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.ekstra_liste.count())
            if self.ekstra_liste.item(i).checkState() == Qt.CheckState.Checked]
        QgsProject.instance().writeEntry(
            PROJEKT_SCOPE, PROJEKT_EKSTRA_LAG, valgte_id)

        periode = self.periode_combo.currentData() or STANDARD_PERIODE
        self._result.reference_periode = periode
        QgsProject.instance().writeEntry(
            PROJEKT_SCOPE, PROJEKT_PERIODE, periode)

        til = self.arealfilter_box.isChecked()
        m2 = self.areal_spin.value()
        self._result.min_area_m2 = m2 if til else 0.0
        self._gem_arealfilter(til, m2)

        bg = self.background_combo.currentData()
        if bg == AUTO_BACKGROUND_SENTINEL:
            self._result.background_auto = True
            self._result.background_layer = None
        elif bg is None:
            self._result.background_auto = False
            self._result.background_layer = None
        else:
            self._result.background_auto = False
            self._result.background_layer = QgsProject.instance().mapLayer(bg)

        self._result.byg = (BYG_OVERSIGT
                            if self.faner.currentIndex() == FANE_OVERSIGT
                            else BYG_ATLAS)
        _gem_bruger_vaerdi(BRUGER_FANE, self.faner.currentIndex())
        self._result.oversigt = self._oversigt_valg()

        self.accept()

    def _oversigt_valg(self):
        """Fanen «Oversigtskort» som OversigtsValg — og husk valgene.

        Titel, projektnavn, sagsnummer og papir hører til projektet;
        initialer og logo til personen, så de står klar i næste projekt.
        """
        valg = OversigtsValg()
        valg.titel = self.ov_titel.text().strip() or "Lodsejerkort"
        valg.projektnavn = self.ov_projektnavn.text().strip()
        valg.sagsnummer = self.ov_sagsnummer.text().strip()
        valg.initialer = self.ov_initialer.text().strip()
        valg.dato = self.ov_dato.text().strip()
        valg.logo = self.ov_logo.text().strip()
        valg.papir = self.ov_papir.currentData() or PAPIR_A3
        valg.vis_lobenr = self.ov_lobenr.isChecked()
        valg.graense_farve = self.ov_graense_btn.color()
        valg.layout_navn = self.ov_navn_edit.text().strip() or "Lodsejerkort"

        projekt = QgsProject.instance()
        projekt.writeEntry(PROJEKT_SCOPE, PROJEKT_OV_TITEL, valg.titel)
        projekt.writeEntry(PROJEKT_SCOPE, PROJEKT_OV_PROJEKTNAVN,
                           valg.projektnavn)
        projekt.writeEntry(PROJEKT_SCOPE, PROJEKT_OV_SAGSNUMMER,
                           valg.sagsnummer)
        projekt.writeEntry(PROJEKT_SCOPE, PROJEKT_OV_PAPIR, valg.papir)
        projekt.writeEntry(PROJEKT_SCOPE, PROJEKT_OV_LOBENR, valg.vis_lobenr)
        _gem_bruger_vaerdi(BRUGER_INITIALER, valg.initialer)
        _gem_bruger_vaerdi(BRUGER_LOGO, valg.logo)
        return valg

    def result(self):
        return self._result
