# -*- coding: utf-8 -*-
"""Panelet der samler arbejdsgangen om støttepunkterne.

Panelet står åbent, mens man arbejder på kortet. Det er med vilje: trinene
hænger sammen med digitaliseringen, og rækkefølgen er

  1. vælg laget, eller opret et nyt           (én gang pr. projekt)
  2. vælg højdemodellen                       (én gang pr. projekt)
  3. sæt punkterne på kortet
  4. udfyld terrænkoterne                     (hver gang der er nye punkter)

Trin 4 må gerne trykkes så mange gange man vil — den regner blot forfra på
de punkter, der er.
"""

import os

from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from qgis.core import (
    QgsExpressionContextUtils,
    QgsMapLayerProxyModel,
    QgsProject,
    QgsRasterLayer,
)
from qgis.gui import QgsMapLayerComboBox

from .. import faelles_ui
from .algoritme import VAR_DTM
from .nyt_lag import er_stoettepunktlag, mangler_felter, opret_lag
# Omdoebt, saa den ikke forveksles med panelets egen knap-handling.
from .nyt_lag import tilfoej_opsaetning as saet_opsaetning_paa

ALG_ID = "afvanding:udfyld_afvanding"
TITEL = "Støttepunkter"
INDSTILLING_MAPPE = "afvanding/senesteMappe"

#: Det åbne panel. Holdes her, fordi dialogen ikke er modal — uden en
#: reference ville den blive ryddet op, så snart den var vist.
_aabent = None


class StoettepunktPanel:
    """Åbner panelet og holder sammen på trinene."""

    def __init__(self, iface):
        self.iface = iface
        self.dialog = None
        self.lag_kombo = None
        self.dtm_kombo = None
        self.status = None
        self.knap_opsaetning = None

    # ------------------------------------------------------------------
    # dialogen
    # ------------------------------------------------------------------

    def aabn(self):
        global _aabent
        if _aabent is not None and _aabent.dialog is not None:
            _aabent.dialog.show()
            _aabent.dialog.raise_()
            _aabent.dialog.activateWindow()
            return
        self.dialog = self._byg()
        _aabent = self
        # Ikke modal: punkterne skal kunne sættes på kortet, mens panelet
        # står åbent, og trin 4 trykkes så mange gange man vil.
        self.dialog.show()
        self.dialog.raise_()

    def _byg(self):
        dialog = QDialog(self.iface.mainWindow())
        dialog.setWindowTitle(TITEL)
        # Lukkes panelet, ryddes det væk — næste gang bygges et nyt, så
        # lagvalget og status altid passer med projektet, som det ser ud.
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.finished.connect(self._lukket)
        layout = QVBoxLayout(dialog)

        forklaring = QLabel(
            "Støttepunkter til afvandingsanalysen. Hvert punkt får en "
            "terrænkote fra højdemodellen og en afvandingsklasse nu og i "
            "fremtiden — af dem udledes afvandingsdybde og vandspejlskote.\n\n"
            "Panelet kan stå åbent, mens du arbejder på kortet.")
        forklaring.setWordWrap(True)
        layout.addWidget(forklaring)

        layout.addWidget(self._lagboks(dialog))
        layout.addWidget(self._dtmboks(dialog))
        layout.addWidget(self._digitaliserboks(dialog))
        layout.addWidget(self._udfyldboks(dialog))

        knapper = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        knapper.rejected.connect(dialog.close)
        layout.addWidget(knapper)

        faelles_ui.anvend_stil(dialog)
        self._opdater_status()
        return dialog

    def _lagboks(self, dialog):
        boks = QGroupBox("1. Støttepunktlaget — vælg eller opret", dialog)
        ind = QVBoxLayout(boks)
        tekst = QLabel(
            "Vælg det lag, du arbejder på. Har du ikke et, opretter "
            "«Opret nyt lag» en GeoPackage med dropdowns og afledte felter.")
        tekst.setWordWrap(True)
        ind.addWidget(tekst)

        raekke = QHBoxLayout()
        self.lag_kombo = QgsMapLayerComboBox(boks)
        self.lag_kombo.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.lag_kombo.setAllowEmptyLayer(True)
        self.lag_kombo.setCurrentIndex(0)
        raekke.addWidget(self.lag_kombo, 1)
        knap_nyt = QPushButton("Opret nyt lag …", boks)
        knap_nyt.clicked.connect(self.nyt_lag)
        raekke.addWidget(knap_nyt)
        ind.addLayout(raekke)

        self.status = QLabel()
        self.status.setWordWrap(True)
        ind.addWidget(self.status)

        self.knap_opsaetning = QPushButton(
            "Tilføj støttepunkt-opsætning til laget", boks)
        self.knap_opsaetning.setToolTip(
            "Lægger de manglende felter på laget og sætter dropdowns, "
            "standardkoten fra højdemodellen og de afledte felter op. "
            "Lagets udseende bliver ikke rørt.")
        self.knap_opsaetning.clicked.connect(self.tilfoej_opsaetning)
        ind.addWidget(self.knap_opsaetning)

        # Vælges laget om, skal status og knap følge med.
        self.lag_kombo.layerChanged.connect(lambda _l: self._opdater_status())
        return boks

    def _dtmboks(self, dialog):
        boks = QGroupBox("2. Højdemodellen (DTM) — vælg én gang", dialog)
        ind = QVBoxLayout(boks)
        tekst = QLabel(
            "Vælges den her, henter nye punkter selv deres kote, mens du "
            "digitaliserer, og den foreslås i trin 4. Indlæs den i "
            "projektet først.")
        tekst.setWordWrap(True)
        ind.addWidget(tekst)
        self.dtm_kombo = QgsMapLayerComboBox(boks)
        self.dtm_kombo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.dtm_kombo.setAllowEmptyLayer(True)
        aktuel = self._dtm_lag()
        if aktuel is not None:
            self.dtm_kombo.setLayer(aktuel)
        ind.addWidget(self.dtm_kombo)
        # Forbindes efter setLayer, så det foreslåede lag ikke melder sig selv.
        self.dtm_kombo.layerChanged.connect(self._dtm_valgt)
        return boks

    def _digitaliserboks(self, dialog):
        boks = QGroupBox("3. Sæt punkterne på kortet", dialog)
        ind = QVBoxLayout(boks)
        tekst = QLabel(
            "Knappen slår redigering til på laget og tager "
            "punktværktøjet frem. Udfyld afvandingsklassen nu og i "
            "fremtiden i formularen — koten kommer af sig selv, når "
            "højdemodellen er valgt. Husk at gemme laget, når du er færdig.")
        tekst.setWordWrap(True)
        ind.addWidget(tekst)
        knap = QPushButton("Start digitalisering", boks)
        knap.clicked.connect(self.digitaliser)
        ind.addWidget(knap)
        return boks

    def _udfyldboks(self, dialog):
        boks = QGroupBox("4. Udfyld terrænkoter — hver gang der er nye "
                         "punkter", dialog)
        ind = QVBoxLayout(boks)
        tekst = QLabel(
            "Henter koter fra højdemodellen og genberegner afvandingsdybde "
            "og vandspejlskote for punkterne i laget. Tryk igen, når du har "
            "sat flere punkter ind eller rettet en afvandingsklasse.")
        tekst.setWordWrap(True)
        ind.addWidget(tekst)
        knap = QPushButton("Udfyld terrænkoter …", boks)
        knap.clicked.connect(self.udfyld)
        ind.addWidget(knap)
        return boks

    def _lukket(self, _resultat=None):
        global _aabent
        if _aabent is self:
            _aabent = None
        self.dialog = None

    # ------------------------------------------------------------------
    # trinene
    # ------------------------------------------------------------------

    def _lag(self):
        """Det valgte støttepunktlag, eller None."""
        return self.lag_kombo.currentLayer() if self.lag_kombo else None

    def _opdater_status(self):
        """Sig om det valgte lag kan bruges — og om der mangler noget."""
        if self.status is None:
            return
        lag = self._lag()
        if lag is None:
            self.status.setText(
                "Intet lag valgt. Vælg et punktlag, eller opret et nyt.")
            self.status.setStyleSheet("color: gray;")
            self.knap_opsaetning.setEnabled(False)
            return
        manglende = mangler_felter(lag)
        if not manglende:
            self.status.setText("Laget er klar — alle felter er på plads.")
            self.status.setStyleSheet("color: #286e34;")
            self.knap_opsaetning.setEnabled(True)
            self.knap_opsaetning.setText("Sæt opsætningen på laget igen")
            return
        self.knap_opsaetning.setEnabled(True)
        self.knap_opsaetning.setText(
            "Tilføj støttepunkt-opsætning til laget")
        if er_stoettepunktlag(lag):
            self.status.setText(
                "Laget kan bruges, men mangler felterne: %s. Tryk nedenfor "
                "for at lægge dem på." % ", ".join(manglende))
            self.status.setStyleSheet("color: #8a6d00;")
        else:
            self.status.setText(
                "Laget er ikke et støttepunktlag — det mangler felterne: "
                "%s. Tryk nedenfor for at lægge dem på og sætte dropdowns "
                "op." % ", ".join(manglende))
            self.status.setStyleSheet("color: #a33;")

    def nyt_lag(self):
        indstillinger = QSettings()
        senest = indstillinger.value(INDSTILLING_MAPPE, "")
        sti, _ = QFileDialog.getSaveFileName(
            self.dialog or self.iface.mainWindow(),
            "Gem nyt støttepunktlag",
            os.path.join(senest, "stoettepunkter.gpkg"),
            "GeoPackage (*.gpkg)")
        if not sti:
            return
        if not sti.lower().endswith(".gpkg"):
            sti += ".gpkg"

        lagnavn = os.path.splitext(os.path.basename(sti))[0]
        lag, fejl = opret_lag(sti, lagnavn)
        if fejl:
            QMessageBox.critical(self.dialog, TITEL, fejl)
            return

        indstillinger.setValue(INDSTILLING_MAPPE, os.path.dirname(sti))
        QgsProject.instance().addMapLayer(lag)
        # Det nye lag er dét, man arbejder videre på.
        self.lag_kombo.setLayer(lag)
        self._opdater_status()
        self.iface.messageBar().pushSuccess(
            TITEL,
            "Laget '%s' er oprettet. Vælg højdemodellen i trin 2, før du "
            "digitaliserer — så får punkterne deres kote med det samme."
            % lagnavn)

    def tilfoej_opsaetning(self):
        lag = self._lag()
        if lag is None:
            QMessageBox.information(
                self.dialog, TITEL, "Vælg først et punktlag i trin 1.")
            return
        rapport, fejl = saet_opsaetning_paa(lag)
        self._opdater_status()
        if fejl:
            QMessageBox.warning(self.dialog, TITEL, fejl)
            return

        dele = []
        if rapport["tilfoejede"]:
            dele.append("Felter lagt på: %s."
                        % ", ".join(rapport["tilfoejede"]))
        else:
            dele.append("Alle felter var der i forvejen.")
        dele.append("Dropdowns, standardkote og afledte felter er sat op.")
        if not rapport["gemt"]:
            dele.append("Opsætningen kunne ikke gemmes i filen — gem "
                        "projektet, så den følger med derfra.")
        if rapport["forkerte_typer"]:
            dele.append("Bemærk at felterne %s har en anden felttype end "
                        "forventet; koter og dybder kan blive afrundet."
                        % ", ".join(rapport["forkerte_typer"]))
        QMessageBox.information(self.dialog, TITEL, " ".join(dele))

    def digitaliser(self):
        lag = self._lag()
        if lag is None:
            QMessageBox.information(
                self.dialog, TITEL, "Vælg først et punktlag i trin 1.")
            return
        if not er_stoettepunktlag(lag):
            QMessageBox.information(
                self.dialog, TITEL,
                "Laget mangler støttepunkt-felterne. Tryk «Tilføj "
                "støttepunkt-opsætning til laget» i trin 1 først.")
            return
        if self._dtm_lag() is None:
            self.iface.messageBar().pushInfo(
                TITEL,
                "Ingen højdemodel valgt i trin 2 — punkterne får først "
                "deres kote, når du trykker «Udfyld terrænkoter».")
        self.iface.setActiveLayer(lag)
        if not lag.isEditable():
            lag.startEditing()
        try:
            self.iface.actionAddFeature().trigger()
        except Exception:
            pass

    def _dtm_valgt(self, lag):
        if lag is None or not isinstance(lag, QgsRasterLayer):
            return
        QgsExpressionContextUtils.setProjectVariable(
            QgsProject.instance(), VAR_DTM, lag.name())
        self.iface.messageBar().pushSuccess(
            TITEL,
            "Højdemodellen '%s' er valgt. Nye punkter henter nu selv deres "
            "kote, og 'Udfyld terrænkoter' foreslår samme lag." % lag.name())

    def _dtm_lag(self):
        """Rasterlaget som projektvariablen dtm_lag peger på, hvis det findes."""
        if self.dtm_kombo is not None:
            valgt = self.dtm_kombo.currentLayer()
            if isinstance(valgt, QgsRasterLayer):
                return valgt
        navn = QgsExpressionContextUtils.projectScope(
            QgsProject.instance()).variable(VAR_DTM)
        if not navn:
            return None
        for lag in QgsProject.instance().mapLayers().values():
            if isinstance(lag, QgsRasterLayer) and lag.name() == navn:
                return lag
        return None

    def udfyld(self):
        from qgis import processing

        parametre = {}
        lag = self._lag()
        if lag is not None:
            parametre["PUNKTER"] = lag
        dtm = self._dtm_lag()
        if dtm is not None:
            parametre["DTM"] = dtm
        try:
            processing.execAlgorithmDialog(ALG_ID, parametre)
        except Exception as exc:
            QMessageBox.critical(
                self.dialog, TITEL,
                "Værktøjet kunne ikke åbnes: %s" % exc)
        self._opdater_status()
