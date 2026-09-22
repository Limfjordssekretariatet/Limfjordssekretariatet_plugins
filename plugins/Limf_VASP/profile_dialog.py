"""GUI til valg af profil-datalag.

Bruges af to handlinger med samme profilliste, men forskellige valg:
  mode="terrain"  ("Terræn på profil"): interval + side + distance; terræn
                  fra DHM er altid slået til. Ét profil ad gangen — koterne
                  skrives tilbage til VASP, og det skal man tage stilling til
                  for ét profil ad gangen.
  mode="profile"  ("Importer længdeprofiler til GIS"): flere profiler kan
                  vælges med flueben, så et helt vandløb kan hentes i én
                  omgang i stedet for ét klik pr. profil.
  mode="vaelg"    Et enkelt valg uden videre: bruges hvor profilen kun skal
                  udpege et vandløb — fx stationeringen når oplande skrives
                  til VASP. Titel og indledning kan sættes af den, der
                  åbner dialogen.
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
    QPushButton,
)
from qgis.PyQt.QtCore import Qt

from .geo import offset
from . import config

MODE_TERRAIN = "terrain"
MODE_PROFILE = "profile"
MODE_VAELG = "vaelg"


from . import faelles_ui

class ProfileDialog(QDialog):
    """Dialog der lader brugeren vælge ét profil-datalag (+ evt. terrænvalg)."""

    MODE_TERRAIN = MODE_TERRAIN
    MODE_PROFILE = MODE_PROFILE
    MODE_VAELG = MODE_VAELG

    def __init__(self, profiles, mode=MODE_TERRAIN, parent=None,
                 titel=None, intro=None):
        super().__init__(parent)
        self._mode = mode
        self._profiles = profiles
        #: Flere ad gangen? Kun ved import.
        self._flere = (mode == MODE_PROFILE)
        #: Afkrydsede profiler, lgdid -> profil. Holdes uden for listen, så
        #: et valg ikke går tabt, når søgefeltet filtrerer listen om.
        self._valgte = {}
        #: Sandt mens listen fyldes — så afkrydsningerne ikke "ændres".
        self._fylder = False

        if mode == MODE_TERRAIN:
            self.setWindowTitle(titel or "Terræn på profil — vælg længdeprofil")
            intro = intro or (
                "Vælg længdeprofil. Terrænet hentes fra DHM langs en linje "
                "forskudt til siden:")
        elif mode == MODE_VAELG:
            self.setWindowTitle(titel or "Vælg længdeprofil")
            intro = intro or "Vælg det længdeprofil, der skal bruges:"
        else:
            self.setWindowTitle(titel or "Importer længdeprofiler til GIS")
            intro = intro or (
                "Sæt flueben ved de længdeprofiler, der skal hentes ind i "
                "QGIS. Søg først, og tag så hele holdet med "
                "«Vælg alle viste». Hvert profil bliver sit eget lag.")

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
        if self._flere:
            self._list.itemChanged.connect(self._afkrydsning_aendret)
        layout.addWidget(self._list)
        self._populate(profiles)

        if self._flere:
            self._build_flervalg(layout)
        if mode == MODE_TERRAIN:
            self._build_terrain_controls(layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        faelles_ui.anvend_stil(self)

    def _build_flervalg(self, layout):
        """Knapper til at tage eller fjerne hele den viste liste på én gang."""
        raekke = QHBoxLayout()
        alle = QPushButton("Vælg alle viste")
        alle.setToolTip("Sætter flueben ved alle profiler, listen viser lige nu "
                        "— altså dem søgningen har fundet.")
        alle.clicked.connect(lambda: self._saet_viste(True))
        raekke.addWidget(alle)
        ryd = QPushButton("Ryd valg")
        ryd.clicked.connect(self._ryd_valg)
        raekke.addWidget(ryd)
        raekke.addStretch(1)
        self._antal_label = QLabel()
        raekke.addWidget(self._antal_label)
        layout.addLayout(raekke)
        self._opdater_antal()

    def _afkrydsning_aendret(self, item):
        """Husk afkrydsningen, så den overlever en ny søgning."""
        if self._fylder:
            return
        prof = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self._valgte[prof["lgdid"]] = prof
        else:
            self._valgte.pop(prof["lgdid"], None)
        self._opdater_antal()

    def _saet_viste(self, valgt):
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(
                Qt.Checked if valgt else Qt.Unchecked)

    def _ryd_valg(self):
        """Ryd alle valg — også dem, søgningen har skjult."""
        self._valgte.clear()
        self._fylder = True
        self._saet_viste(False)
        self._fylder = False
        self._opdater_antal()

    def _opdater_antal(self):
        antal = len(self._valgte)
        if antal == 0:
            tekst = "ingen valgt — den markerede hentes"
        elif antal == 1:
            tekst = "1 profil valgt"
        else:
            tekst = "%d profiler valgt" % antal
        self._antal_label.setText(tekst)

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
        self._fylder = True
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
            if self._flere:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.Checked if prof["lgdid"] in self._valgte
                    else Qt.Unchecked)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._fylder = False

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

    def selected_profiles(self):
        """De valgte profiler, i listens rækkefølge.

        Er der ingen flueben, er det den markerede linje — så virker et
        dobbeltklik og et enkelt valg som før, uden at sætte flueben først.
        """
        if not self._flere:
            prof = self.selected_profile()
            return [prof] if prof else []
        if self._valgte:
            return [p for p in self._profiles if p["lgdid"] in self._valgte]
        prof = self.selected_profile()
        return [prof] if prof else []

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
