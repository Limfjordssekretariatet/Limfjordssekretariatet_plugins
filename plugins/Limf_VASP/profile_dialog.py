"""GUI til valg af profil-datalag.

Bruges af to handlinger med samme profilliste, men forskellige valg:
  mode="terrain"  ("Terræn på profil"): interval + side + distance; terræn
                  fra DHM er altid slået til.
  mode="profile"  ("Importer længdeprofil til GIS"): kun profilvalg.
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QComboBox,
    QDoubleSpinBox,
    QDialogButtonBox,
)
from qgis.PyQt.QtCore import Qt

from .geo import offset
from . import config

MODE_TERRAIN = "terrain"
MODE_PROFILE = "profile"


from . import faelles_ui

class ProfileDialog(QDialog):
    """Dialog der lader brugeren vælge ét profil-datalag (+ evt. terrænvalg)."""

    MODE_TERRAIN = MODE_TERRAIN
    MODE_PROFILE = MODE_PROFILE

    def __init__(self, profiles, mode=MODE_TERRAIN, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._profiles = profiles

        if mode == MODE_TERRAIN:
            self.setWindowTitle("Terræn på profil — vælg længdeprofil")
            intro = "Vælg længdeprofil. Terrænet hentes fra DHM langs en linje "
            intro += "forskudt til siden:"
        else:
            self.setWindowTitle("Importer længdeprofil til GIS")
            intro = "Vælg længdeprofil der skal hentes ind i QGIS:"

        self.resize(540, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(intro))

        # Søgefelt til at filtrere listen.
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Søg:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Filtrér på vandløb, navn, projekt eller LGDID …")
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search)
        layout.addLayout(search_row)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self._list)
        self._populate(profiles)

        if mode == MODE_TERRAIN:
            self._build_terrain_controls(layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        faelles_ui.anvend_stil(self)

    def _build_terrain_controls(self, layout):
        """Interval, side og distance — kun i terræn-tilstand."""
        # Interval mellem stationeringspunkter langs profilen.
        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Punkter med interval:"))
        self._spin_interval = QDoubleSpinBox()
        self._spin_interval.setRange(0.1, 1000.0)
        self._spin_interval.setValue(1.0)
        self._spin_interval.setSingleStep(1.0)
        self._spin_interval.setSuffix(" m")
        interval_row.addWidget(self._spin_interval)
        interval_row.addStretch()
        layout.addLayout(interval_row)

        # Side + distance for terrænforskydningen.
        terrain_row = QHBoxLayout()
        terrain_row.addWidget(QLabel("Terræn fra DHM, forskudt"))
        self._spin_distance = QDoubleSpinBox()
        self._spin_distance.setRange(0.1, 1000.0)
        self._spin_distance.setValue(config.OFFSET_DISTANCE)
        self._spin_distance.setSingleStep(1.0)
        self._spin_distance.setSuffix(" m")
        terrain_row.addWidget(self._spin_distance)
        terrain_row.addWidget(QLabel("til"))
        self._side = QComboBox()
        self._side.addItem("venstre", offset.SIDE_LEFT)
        self._side.addItem("højre", offset.SIDE_RIGHT)
        terrain_row.addWidget(self._side)
        terrain_row.addStretch()
        layout.addLayout(terrain_row)

    def _populate(self, profiles):
        self._list.clear()
        for prof in profiles:
            # Vandløbet først som i de øvrige valglister. Projektnavnet er
            # ofte bare "VLBGIS", så det står bagest sammen med LGDID.
            vlb = prof.get("vlbnavn") or ""
            prj = prof.get("prjnavn") or prof["projektid"]
            if vlb:
                label = "%s  /  %s  —  %d punkter  (LGDID %s, %s)" % (
                    vlb, prof["navn"], prof["punkter"], prof["lgdid"], prj)
            else:
                label = "%s  —  %d punkter  (LGDID %s, projekt %s)" % (
                    prof["navn"], prof["punkter"], prof["lgdid"], prj)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, prof)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _apply_filter(self, text):
        text = text.strip().lower()
        if not text:
            filtered = self._profiles
        else:
            # Søg bredt: vandløb, profilnavn, projekt (navn og id) og LGDID,
            # så samme søgeord virker her som i de øvrige valglister.
            filtered = [
                p for p in self._profiles
                if text in (p.get("vlbnavn") or "").lower()
                or text in (p.get("navn") or "").lower()
                or text in (p.get("prjnavn") or "").lower()
                or text in str(p.get("projektid") or "").lower()
                or text in str(p.get("lgdid") or "").lower()
            ]
        self._populate(filtered)

    def selected_profile(self):
        """Returnér den valgte profil-dict, eller None hvis intet er valgt."""
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def selected_interval(self):
        """Interval (m) mellem stationeringspunkter. Kun i terræn-tilstand."""
        if self._mode == MODE_TERRAIN:
            return self._spin_interval.value()
        return None

    def selected_distance(self):
        """Forskydningsafstand (m) vinkelret ud til siden (terræn-tilstand)."""
        if self._mode == MODE_TERRAIN:
            return self._spin_distance.value()
        return None

    def terrain_side(self):
        """Valgt side (offset.SIDE_LEFT/RIGHT). Kun i terræn-tilstand."""
        if self._mode == MODE_TERRAIN:
            return self._side.currentData()
        return None
