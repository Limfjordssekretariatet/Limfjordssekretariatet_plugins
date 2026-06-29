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
)
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
)

from .template_spec import PLACEHOLDERS

# Værdi i felt-combo der betyder "intet felt – lad pluginnet generere".
GENERATE_SENTINEL = "__generate__"
NONE_SENTINEL = "__none__"
# Baggrundskort: brug projektets rasterlag automatisk.
AUTO_BACKGROUND_SENTINEL = "__auto_bg__"


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

        # --- Gruppering & projektområde -------------------------------
        grp_box = QGroupBox("3. Gruppering og oversigtskort")
        grp_form = QFormLayout(grp_box)

        self.owner_combo = QComboBox()
        grp_form.addRow("Gruppér pr. ejer (felt) *", self.owner_combo)

        self.area_combo = QComboBox()
        grp_form.addRow("Projektområde-lag (oversigt)", self.area_combo)

        self.background_combo = QComboBox()
        grp_form.addRow("Baggrundskort-lag", self.background_combo)

        grp_hint = QLabel(
            "Atlasset får én side pr. ejer; alle ejerens matrikler vises "
            "samlet. Oversigtskortet zoomer til projektområde-laget. "
            "Baggrundskort: «Auto» bruger projektets rasterlag, eller vælg "
            "et bestemt lag. Arealtabellen udfyldes automatisk fra pluginnets "
            "medfølgende referencekort."
        )
        grp_hint.setWordWrap(True)
        grp_hint.setStyleSheet("color: gray;")
        grp_form.addRow(grp_hint)
        layout.addWidget(grp_box)

        # --- Layoutnavn -----------------------------------------------
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Navn på layout:"))
        self.name_edit = QLineEdit(self._result.layout_name)
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        hint = QLabel(
            "* Påkrævet. Mangler et felt i dataen, kan løbenummer og "
            "postnr./by genereres automatisk (vælg «Generér automatisk»)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

        # --- Knapper ---------------------------------------------------
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.buttons.button(QDialogButtonBox.Ok).setText("Byg atlas")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

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
        """
        lowered = {name.lower(): name for name in field_names}

        # 1) eksakt match på et hint
        for hint in placeholder.match_hints:
            if hint in lowered:
                return lowered[hint]
        # 2) feltnavn starter med et hint (DBF afkorter til 10 tegn)
        for hint in placeholder.match_hints:
            for low, original in lowered.items():
                if low.startswith(hint) or hint.startswith(low):
                    return original
        # 3) hint optræder som delstreng
        for hint in placeholder.match_hints:
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

        # Tilføj fil-lag til projektet så atlasset kan referere det.
        if self._loaded_from_file is not None:
            QgsProject.instance().addMapLayer(self._loaded_from_file)

        area_id = self.area_combo.currentData()
        self._result.project_area_layer = (
            QgsProject.instance().mapLayer(area_id) if area_id else None
        )

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

        self.accept()

    def result(self):
        return self._result
