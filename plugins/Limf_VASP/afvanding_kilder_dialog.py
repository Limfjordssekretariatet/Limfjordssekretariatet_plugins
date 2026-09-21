# -*- coding: utf-8 -*-
"""GUI til at samle de vandspejl der skal flettes før en afvandingsanalyse.

Brugeren bygger en liste af vandspejlskilder:

* Beregnede vandspejl fra VASP — én ad gangen, med scenarievalg hvis det er
  en multiberegning (via samme dialoger som import-værktøjet).
* Punktlag fra projektet med opmålte vandspejl, hvor kotefeltet vælges pr.
  lag.

Kilderne hentes bagefter via ``valgte_kilder()`` og flettes af pluginnet til
ét punktlag, som afvandingsanalysen køres på.
"""

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes

from . import faelles_ui
from .vsp_dialog import VspDialog


class AfvandingKilderDialog(QDialog):
    """Dialog der lader brugeren samle vandspejl til afvandingsanalysen."""

    def __init__(self, calcs, hent_vsp_punkter, parent=None):
        """calcs: liste fra dbaccess.list_vsp_calcs() (kan være tom).
        hent_vsp_punkter: callback(calc) -> (punkter, scenarienavn); (None, "")
        hvis .ber-filen ikke kunne læses eller scenarievalget blev fortrudt.
        """
        super().__init__(parent)
        self.setWindowTitle(
            "Afvandingsanalyse — vælg vandspejl der skal flettes")
        self.resize(640, 460)
        self._calcs = calcs or []
        self._hent_vsp_punkter = hent_vsp_punkter
        self._kilder = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Vælg de vandspejl afvandingen skal måles i forhold til. De "
            "flettes til ét vandspejl, før analysen kører.\n\n"
            "Du kan blande beregnede vandspejl fra VASP med punktlag i "
            "projektet, der har opmålte vandspejlskoter. Har punktlaget et "
            "filter eller markerede punkter, bruges kun de punkter."))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self._list, 1)

        knapper = QHBoxLayout()
        self._btn_vasp = QPushButton("Tilføj VASP-vandspejl …")
        self._btn_vasp.clicked.connect(self._tilfoej_vasp)
        if not self._calcs:
            self._btn_vasp.setEnabled(False)
            self._btn_vasp.setToolTip(
                "Databasen har ingen vandspejlsberegninger.")
        knapper.addWidget(self._btn_vasp)

        self._btn_lag = QPushButton("Tilføj punktlag fra projektet …")
        self._btn_lag.clicked.connect(self._tilfoej_lag)
        knapper.addWidget(self._btn_lag)

        knapper.addStretch(1)
        self._btn_fjern = QPushButton("Fjern valgte")
        self._btn_fjern.clicked.connect(self._fjern_valgte)
        knapper.addWidget(self._btn_fjern)
        layout.addLayout(knapper)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._opdater_tilstand()
        faelles_ui.anvend_stil(self)

    # --- tilføj kilder --------------------------------------------------

    def _tilfoej_vasp(self):
        """Vælg én eller flere VASP-beregninger (+ scenarie) og læg dem på
        listen. Er en af de valgte en multiberegning, vises scenarievalget
        for netop den; fortryder brugeren dét, springes kun den beregning
        over — resten af de valgte tilføjes stadig."""
        dlg = VspDialog(
            self._calcs, self,
            titel="Tilføj VASP-vandspejl",
            intro="Vælg de vandspejlsberegninger der skal med i "
                  "afvandingen:",
            multi=True)
        if dlg.exec_() != VspDialog.Accepted:
            return
        for calc in dlg.selected_calcs():
            punkter, scenarie = self._hent_vsp_punkter(calc)
            if punkter is None:
                continue

            navn = calc.get("vlbnavn") or calc.get("navn") or "VASP-vandspejl"
            if scenarie:
                navn = "%s (%s)" % (navn, scenarie)
            self._tilfoej_kilde(
                {"type": "vasp", "calc": calc, "punkter": punkter,
                 "scenarie": scenarie, "navn": navn},
                "VASP: %s  —  %d punkter" % (navn, len(punkter)))

    def _tilfoej_lag(self):
        """Vælg et punktlag i projektet + kotefelt og læg det på listen."""
        dlg = _PunktlagDialog(self)
        if not dlg.har_punktlag():
            QMessageBox.information(
                self, "Afvandingsanalyse",
                "Der er ingen punktlag i projektet. Indlæs punktfilen med de "
                "opmålte vandspejl først.")
            return
        if dlg.exec_() != QDialog.Accepted:
            return
        lag, felt = dlg.valg()
        if lag is None or not felt:
            return
        self._tilfoej_kilde(
            {"type": "lag", "lag": lag, "felt": felt, "navn": lag.name()},
            "Punktlag: %s  —  felt '%s'" % (lag.name(), felt))

    def _tilfoej_kilde(self, kilde, tekst):
        self._kilder.append(kilde)
        item = QListWidgetItem(tekst)
        self._list.addItem(item)
        self._list.setCurrentItem(item)
        self._opdater_tilstand()

    def _fjern_valgte(self):
        # Fjern nedefra, så de tilbageværende rækker beholder deres indeks.
        for row in sorted(
                (self._list.row(i) for i in self._list.selectedItems()),
                reverse=True):
            self._list.takeItem(row)
            del self._kilder[row]
        self._opdater_tilstand()

    def _opdater_tilstand(self):
        self._btn_fjern.setEnabled(self._list.count() > 0)
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(
            bool(self._kilder))

    def valgte_kilder(self):
        """Returnér listen af kilde-dicts i den rækkefølge de blev tilføjet."""
        return list(self._kilder)


class _PunktlagDialog(QDialog):
    """Vælg et punktlag i projektet og feltet der har vandspejlskoten."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tilføj punktlag")
        self.resize(440, 190)
        self._punktlag = self._find_punktlag()

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Punktlag:"))
        self._lag_combo = QComboBox()
        # Lag-id'et (en tekststreng) gemmes som data, ikke selve laget:
        # QgsVectorLayer-objekter overlever ikke altid en tur gennem
        # QComboBox' item-data, og feltlisten stod derfor tom.
        for lag in self._punktlag:
            self._lag_combo.addItem(lag.name(), lag.id())
        layout.addWidget(self._lag_combo)

        layout.addWidget(QLabel("Felt med vandspejlskoten:"))
        self._felt_combo = QComboBox()
        layout.addWidget(self._felt_combo)
        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #666;")
        layout.addWidget(self._hint)

        # Feltnavne der er tal-typer (resten er tekstfelter — dem kan vi også,
        # værdierne parses som tal ved indlæsningen).
        self._talfelter = set()

        self._lag_combo.currentIndexChanged.connect(self._opdater_felter)
        self._felt_combo.currentIndexChanged.connect(self._opdater_hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._ok = buttons.button(QDialogButtonBox.Ok)

        self._opdater_felter()
        faelles_ui.anvend_stil(self)

    @staticmethod
    def _find_punktlag():
        """Punkt-vektorlag i projektet, sorteret efter navn."""
        ud = []
        for lag in QgsProject.instance().mapLayers().values():
            if (isinstance(lag, QgsVectorLayer) and lag.isValid()
                    and lag.geometryType() == QgsWkbTypes.PointGeometry):
                ud.append(lag)
        ud.sort(key=lambda l: l.name().lower())
        return ud

    def har_punktlag(self):
        return bool(self._punktlag)

    def _aktivt_lag(self):
        lag_id = self._lag_combo.currentData()
        return QgsProject.instance().mapLayer(lag_id) if lag_id else None

    def _opdater_felter(self):
        self._felt_combo.blockSignals(True)
        self._felt_combo.clear()
        self._talfelter = set()
        lag = self._aktivt_lag()
        tal, tekst = [], []
        if lag is not None:
            for felt in lag.fields():
                if felt.isNumeric():
                    tal.append(felt.name())
                    self._talfelter.add(felt.name())
                else:
                    tekst.append(felt.name())
        # Alle felter vises — et opmålt vandspejl kan ligge i et tekstfelt i
        # en håndlavet shapefil — men tal-felterne står først, så det rigtige
        # som regel er forvalgt.
        for navn in tal + tekst:
            self._felt_combo.addItem(navn)
        self._felt_combo.setCurrentIndex(0 if self._felt_combo.count() else -1)
        self._felt_combo.blockSignals(False)
        self._ok.setEnabled(self._felt_combo.count() > 0)
        self._opdater_hint()

    def _opdater_hint(self):
        felt = self._felt_combo.currentText()
        if felt and felt not in self._talfelter:
            self._hint.setText(
                "Tekstfelt — værdierne læses som tal (komma og punktum som "
                "decimaltegn er begge OK). Værdier der ikke er tal, "
                "springes over.")
        else:
            self._hint.setText("")

    def valg(self):
        """Returnér (lag, feltnavn) — (None, "") hvis intet kunne vælges."""
        return self._aktivt_lag(), self._felt_combo.currentText()
