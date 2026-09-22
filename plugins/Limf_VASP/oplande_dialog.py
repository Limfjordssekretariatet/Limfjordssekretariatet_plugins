"""GUI til at skrive udpegede oplande ind i et VASP-datasæt.

Oplandene kommer typisk fra "Udpeg oplande" i WetlandTools: ét polygon pr.
udløbspunkt med sit eget areal. VASP vil derimod have en tabel med station
og samlet opland. Dialogen binder de to ting sammen:

  * oplandslaget og det felt, arealet står i
  * længdeprofilet, hvis stationering rækkerne skal følge
  * det hydrauliske datasæt (.hds), serien skal skrives ind i

Tabellen regnes ud og vises, inden der skrives noget — det er den, der
lander i VASP. Selve skrivningen sker i vasp_plugin, så dialogen kun
samler valgene.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsWkbTypes,
)

from . import config, faelles_ui
from .geo import hds, oplandsserie

#: Hvor langt et udløbspunkt må ligge fra vandløbet, før det nævnes.
MAKS_AFSTAND_M = 100.0

#: Omregning til km², som VASP's oplandsfane regner i.
ENHEDER = (("km²", 1.0), ("ha", 0.01), ("m²", 1e-6))

#: Feltnavne fra "Udpeg oplande", der genkendes af sig selv.
AREALFELTER = {"areal_km2": "km²", "areal_ha": "ha", "areal_m2": "m²"}
UDLOEB_X, UDLOEB_Y = "udloeb_x", "udloeb_y"

#: Initialerne huskes mellem gange — de står på hver serie i VASP.
_INIT_NOEGLE = "VASP/initialer"


def _tal(vaerdi, decimaler=0):
    """Skriv et tal med komma som decimaltegn, som VASP selv viser det."""
    return ("%.*f" % (decimaler, vaerdi)).replace(".", ",")


class OplandeDialog(QDialog):
    """Dialog der samler valgene til en ny oplandsserie i VASP."""

    def __init__(self, profiler, hent_bundlinje, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Skriv oplande til VASP")
        self.resize(720, 700)

        self._profiler = profiler
        self._hent_bundlinje = hent_bundlinje
        self._profil = None
        self._bundlinje = []
        self._datasaet = []
        self._raekker = []
        self._noter = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Oplandene skrives som en ny serie under «Oplande» i et "
            "hydraulisk datasæt. Eksisterende serier bliver ikke rørt."))
        layout.addWidget(self._oplandsboks())
        layout.addWidget(self._vandloebsboks())
        layout.addWidget(self._serieboks())
        layout.addWidget(self._tabelboks(), 1)

        self._knapper = QDialogButtonBox(QDialogButtonBox.Cancel)
        self._skriv = self._knapper.addButton(
            "Skriv til VASP", QDialogButtonBox.AcceptRole)
        self._skriv.setEnabled(False)
        self._knapper.accepted.connect(self.accept)
        self._knapper.rejected.connect(self.reject)
        layout.addWidget(self._knapper)

        self._fyld_lag()
        self._opdater()
        faelles_ui.anvend_stil(self)

    # --- afsnit ----------------------------------------------------------

    def _oplandsboks(self):
        boks, ind = faelles_ui.afsnit("Oplande fra QGIS")

        raekke = QHBoxLayout()
        raekke.addWidget(QLabel("Lag:"))
        self._lag = QComboBox()
        self._lag.currentIndexChanged.connect(self._lag_skiftet)
        raekke.addWidget(self._lag, 1)
        ind.addLayout(raekke)

        raekke = QHBoxLayout()
        raekke.addWidget(QLabel("Areal i feltet:"))
        self._felt = QComboBox()
        self._felt.currentIndexChanged.connect(self._felt_skiftet)
        raekke.addWidget(self._felt, 1)
        self._enhed = QComboBox()
        for navn, _faktor in ENHEDER:
            self._enhed.addItem(navn)
        self._enhed.currentIndexChanged.connect(self._opdater)
        raekke.addWidget(self._enhed)
        ind.addLayout(raekke)

        self._kun_valgte = QCheckBox("Kun de markerede objekter")
        self._kun_valgte.stateChanged.connect(self._opdater)
        ind.addWidget(self._kun_valgte)

        self._deloplande = QComboBox()
        self._deloplande.addItem(
            "Deloplande — lægges sammen nedstrøms", True)
        self._deloplande.addItem(
            "Fulde oplande — arealerne skrives som de er", False)
        self._deloplande.currentIndexChanged.connect(self._opdater)
        ind.addWidget(self._deloplande)

        self._vend = QCheckBox("Vend retningen, de lægges sammen i")
        self._vend.setToolTip(
            "Retningen gættes ud fra bundkoten: vandet løber nedad. "
            "Sæt flueben, hvis tabellen vender forkert.")
        self._vend.stateChanged.connect(self._opdater)
        ind.addWidget(self._vend)

        self._retning_note = QLabel()
        self._retning_note.setWordWrap(True)
        ind.addWidget(self._retning_note)
        return boks

    def _vandloebsboks(self):
        boks, ind = faelles_ui.afsnit("Vandløb og datasæt i VASP")

        raekke = QHBoxLayout()
        raekke.addWidget(QLabel("Stationering fra:"))
        self._profil_label = QLabel("(intet profil valgt)")
        self._profil_label.setWordWrap(True)
        raekke.addWidget(self._profil_label, 1)
        raekke.addWidget(faelles_ui.knap(
            "Vælg profil …", self._vaelg_profil,
            tip="Stationerne hentes fra det længdeprofil, oplandene hører "
                "til — så tabellen passer med VASP's egen stationering."))
        ind.addLayout(raekke)

        raekke = QHBoxLayout()
        raekke.addWidget(QLabel("Datasæt:"))
        self._datasaet_valg = QComboBox()
        self._datasaet_valg.currentIndexChanged.connect(self._opdater)
        raekke.addWidget(self._datasaet_valg, 1)
        ind.addLayout(raekke)
        return boks

    def _serieboks(self):
        boks, ind = faelles_ui.afsnit("Den nye serie")

        raekke = QHBoxLayout()
        raekke.addWidget(QLabel("Navn:"))
        self._navn = QLineEdit()
        self._navn.setPlaceholderText("fx «Stubdrup 2.1»")
        self._navn.textChanged.connect(self._opdater_knap)
        raekke.addWidget(self._navn, 1)
        raekke.addWidget(QLabel("Initialer:"))
        self._initialer = QLineEdit()
        self._initialer.setMaxLength(10)
        self._initialer.setFixedWidth(70)
        raekke.addWidget(self._initialer)
        ind.addLayout(raekke)

        raekke = QHBoxLayout()
        raekke.addWidget(QLabel("Bemærkning:"))
        self._bemaerkning = QLineEdit()
        self._bemaerkning.setPlaceholderText(
            "Står på serien i VASP — fx hvor oplandene kommer fra")
        raekke.addWidget(self._bemaerkning, 1)
        ind.addLayout(raekke)

        from qgis.core import QgsSettings
        self._initialer.setText(QgsSettings().value(_INIT_NOEGLE, "") or "")
        return boks

    def _tabelboks(self):
        boks, ind = faelles_ui.afsnit("Sådan kommer tabellen til at se ud")
        self._tabel = QTableWidget(0, 3)
        self._tabel.setHorizontalHeaderLabels(
            ["Station [m]", "Opland [km²]", "Bemærkning"])
        self._tabel.verticalHeader().setVisible(False)
        self._tabel.setEditTriggers(QTableWidget.NoEditTriggers)
        hoved = self._tabel.horizontalHeader()
        hoved.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hoved.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hoved.setSectionResizeMode(2, QHeaderView.Stretch)
        ind.addWidget(self._tabel, 1)

        self._besked = QLabel()
        self._besked.setWordWrap(True)
        ind.addWidget(self._besked)
        return boks

    # --- lag og felter ---------------------------------------------------

    def _fyld_lag(self):
        """Find de lag i projektet, oplandene kan komme fra."""
        self._lag.blockSignals(True)
        self._lag.clear()
        for lag in QgsProject.instance().mapLayers().values():
            if not hasattr(lag, "geometryType"):
                continue
            if lag.geometryType() in (QgsWkbTypes.PolygonGeometry,
                                      QgsWkbTypes.PointGeometry):
                self._lag.addItem(lag.name(), lag.id())
        self._lag.blockSignals(False)
        # Et lag fra "Udpeg oplande" er det mest sandsynlige valg.
        for i in range(self._lag.count()):
            navn = self._lag.itemText(i).lower()
            if "opland" in navn:
                self._lag.setCurrentIndex(i)
                break
        self._lag_skiftet()

    def _aktivt_lag(self):
        lag_id = self._lag.currentData()
        return QgsProject.instance().mapLayer(lag_id) if lag_id else None

    def _lag_skiftet(self, *_):
        lag = self._aktivt_lag()
        self._felt.blockSignals(True)
        self._felt.clear()
        if lag is not None:
            for felt in lag.fields():
                if felt.isNumeric():
                    self._felt.addItem(felt.name())
        self._felt.blockSignals(False)
        # Felterne fra "Udpeg oplande" vælger sig selv — med rigtig enhed.
        for navn in AREALFELTER:
            i = self._felt.findText(navn)
            if i >= 0:
                self._felt.setCurrentIndex(i)
                break
        valgt = lag.selectedFeatureCount() if lag is not None else 0
        self._kun_valgte.setEnabled(bool(valgt))
        self._kun_valgte.setText(
            "Kun de %d markerede objekter" % valgt if valgt
            else "Kun de markerede objekter")
        if not valgt:
            self._kun_valgte.setChecked(False)
        if lag is not None and not self._navn.text().strip():
            self._navn.setText(lag.name())
        self._felt_skiftet()

    def _felt_skiftet(self, *_):
        """Sæt enheden efter feltnavnet, når det er et felt vi kender."""
        enhed = AREALFELTER.get(self._felt.currentText())
        if enhed:
            i = self._enhed.findText(enhed)
            if i >= 0:
                self._enhed.blockSignals(True)
                self._enhed.setCurrentIndex(i)
                self._enhed.blockSignals(False)
        self._opdater()

    # --- profil og datasæt ----------------------------------------------

    def _vaelg_profil(self):
        from .profile_dialog import ProfileDialog
        dialog = ProfileDialog(
            self._profiler, mode=ProfileDialog.MODE_VAELG, parent=self,
            titel="Vælg vandløb — stationeringen kommer herfra",
            intro="Vælg det længdeprofil, oplandene skal stationeres på. "
                  "Stationerne i tabellen bliver VASP's egne.")
        if dialog.exec_() != QDialog.Accepted:
            return
        prof = dialog.selected_profile()
        if not prof:
            return
        self._profil = prof
        self._bundlinje = self._hent_bundlinje(prof) or []
        vlb = prof.get("vlbnavn") or ""
        self._profil_label.setText(
            "%s%s  (LGDID %s, %d punkter på linjen)"
            % (vlb + " / " if vlb else "", prof.get("navn") or "",
               prof.get("lgdid"), len(self._bundlinje)))
        self._fyld_datasaet(prof.get("projektid"))
        self._opdater()

    def _fyld_datasaet(self, projektid):
        """Find de hydrauliske datasæt i profilens projekt."""
        self._datasaet_valg.blockSignals(True)
        self._datasaet_valg.clear()
        self._datasaet = []
        try:
            self._datasaet = hds.find_datasaet(config.prjdata_path(), projektid)
        except hds.HdsFejl as e:
            self._datasaet_valg.addItem(str(e), None)
        for d in self._datasaet:
            antal = d["antal"].get(hds.OPLANDE, 0)
            self._datasaet_valg.addItem(
                "%s  —  %d oplandsserie%s i forvejen  (HYD%d)"
                % (d["navn"], antal, "r" if antal != 1 else "", d["hydatid"]),
                d)
        if not self._datasaet:
            self._datasaet_valg.addItem(
                "Projektet har ingen hydrauliske datasæt — opret ét i VASP "
                "først.", None)
        self._datasaet_valg.blockSignals(False)

    def _valgt_datasaet(self):
        return self._datasaet_valg.currentData()

    # --- beregning og visning -------------------------------------------

    def _udloeb(self):
        """Træk (x, y, areal, bemærkning) ud af oplandslaget.

        Koordinaterne omregnes til profilets koordinatsystem, så et
        oplandslag i ETRS89 kan bruges på et profil i ED50.
        """
        lag = self._aktivt_lag()
        felt = self._felt.currentText()
        if lag is None or not felt:
            return [], []
        faktor = dict(ENHEDER)[self._enhed.currentText()]
        felter = lag.fields()
        har_udloeb = (felter.indexFromName(UDLOEB_X) >= 0
                      and felter.indexFromName(UDLOEB_Y) >= 0)
        id_felt = "punkt_id" if felter.indexFromName("punkt_id") >= 0 else None

        transform = self._transform(lag)
        objekter = (lag.selectedFeatures() if self._kun_valgte.isChecked()
                    else lag.getFeatures())
        udloeb, noter = [], []
        for f in objekter:
            vaerdi = f[felt]
            if vaerdi is None:
                noter.append("Et objekt har intet areal i «%s» og springes "
                             "over." % felt)
                continue
            try:
                areal = float(vaerdi) * faktor
            except (TypeError, ValueError):
                noter.append("«%s» kan ikke læses som et tal i alle objekter."
                             % felt)
                continue
            punkt = self._punkt(f, har_udloeb, transform)
            if punkt is None:
                noter.append("Et objekt har ingen geometri og springes over.")
                continue
            tekst = str(f[id_felt]) if id_felt else str(f.id())
            udloeb.append((punkt[0], punkt[1], areal, tekst))
        return udloeb, noter

    def _transform(self, lag):
        """Omregning fra lagets koordinatsystem til profilets, eller None."""
        epsg = config.KOORDSYS_TO_EPSG.get(
            self._profil.get("koordsysid") if self._profil else None)
        if not epsg:
            return None
        maal = QgsCoordinateReferenceSystem("EPSG:%d" % epsg)
        if not maal.isValid() or maal == lag.crs():
            return None
        return QgsCoordinateTransform(lag.crs(), maal,
                                      QgsProject.instance())

    @staticmethod
    def _punkt(f, har_udloeb, transform):
        """Udløbspunktet for ét opland: feltet hvis det er der, ellers geometrien."""
        if har_udloeb and f[UDLOEB_X] is not None and f[UDLOEB_Y] is not None:
            try:
                x, y = float(f[UDLOEB_X]), float(f[UDLOEB_Y])
            except (TypeError, ValueError):
                x = y = None
            if x is not None:
                return OplandeDialog._omregn(x, y, transform)
        geom = f.geometry()
        if geom is None or geom.isEmpty():
            return None
        punkt = geom.pointOnSurface().asPoint()
        return OplandeDialog._omregn(punkt.x(), punkt.y(), transform)

    @staticmethod
    def _omregn(x, y, transform):
        if transform is None:
            return x, y
        p = transform.transform(x, y)
        return p.x(), p.y()

    def _retning(self):
        """Hvilken vej rækkerne lægges sammen."""
        if not self._deloplande.currentData():
            return oplandsserie.INGEN_AKKUMULERING
        retning = oplandsserie.stroemretning(self._bundlinje)
        return -retning if self._vend.isChecked() else retning

    def _opdater(self, *_):
        """Regn tabellen ud igen og vis den."""
        udloeb, noter = self._udloeb()
        retning = self._retning()
        self._vis_retning(retning)
        if not udloeb or not self._bundlinje:
            self._raekker, self._noter = [], noter
        else:
            self._raekker, flere = oplandsserie.byg_serie(
                udloeb, self._bundlinje, retning=retning,
                maks_afstand=MAKS_AFSTAND_M)
            self._noter = noter + flere
        self._vis_tabel()
        self._opdater_knap()

    def _vis_retning(self, retning):
        if retning == oplandsserie.INGEN_AKKUMULERING:
            self._retning_note.setText(
                "Arealerne skrives som de står — ingenting lægges sammen.")
            self._vend.setEnabled(False)
            return
        self._vend.setEnabled(bool(self._bundlinje))
        if not self._bundlinje:
            self._retning_note.setText("Vælg et profil, så stationerne kan "
                                       "regnes ud.")
        elif retning == oplandsserie.MED_STROEMMEN:
            self._retning_note.setText(
                "Oplandet vokser med stationen — bundkoten falder den vej.")
        else:
            self._retning_note.setText(
                "Oplandet vokser mod station 0 — vandløbet er stationeret "
                "mod strømmen.")

    def _vis_tabel(self):
        self._tabel.setRowCount(len(self._raekker))
        for i, (station, opland, tekst) in enumerate(self._raekker):
            for j, vaerdi in enumerate((_tal(station), _tal(opland, 3),
                                        tekst)):
                celle = QTableWidgetItem(vaerdi)
                if j < 2:
                    celle.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._tabel.setItem(i, j, celle)
        beskeder = []
        if self._raekker:
            beskeder.append(
                "%d rækker, fra station %s til %s. Største opland %s km²."
                % (len(self._raekker), _tal(self._raekker[0][0]),
                   _tal(self._raekker[-1][0]),
                   _tal(max(r[1] for r in self._raekker), 3)))
        # Ens noter siger kun noget én gang.
        for note in dict.fromkeys(self._noter):
            beskeder.append("⚠ " + note)
        self._besked.setText("\n".join(beskeder))

    def _opdater_knap(self, *_):
        klar = bool(self._raekker and self._navn.text().strip()
                    and self._valgt_datasaet())
        self._skriv.setEnabled(klar)

    # --- resultat --------------------------------------------------------

    def valg(self):
        """Det, der skal skrives: datasæt, serienavn og rækker."""
        from qgis.core import QgsSettings
        initialer = self._initialer.text().strip()
        QgsSettings().setValue(_INIT_NOEGLE, initialer)
        return {
            "datasaet": self._valgt_datasaet(),
            "navn": self._navn.text().strip(),
            "initialer": initialer,
            "bemaerkning": self._bemaerkning.text().strip(),
            "raekker": self._raekker,
            "noter": self._noter,
            "profil": self._profil,
        }
