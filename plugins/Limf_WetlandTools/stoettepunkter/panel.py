# -*- coding: utf-8 -*-
"""Panelet der samler støttepunkternes tre trin i én dialog."""

import os

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
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
from .nyt_lag import opret_lag

ALG_ID = "afvanding:udfyld_afvanding"
TITEL = "Støttepunkter"
INDSTILLING_MAPPE = "afvanding/senesteMappe"


class StoettepunktPanel:
    """Åbner panelet. Holder ingen tilstand ud over iface."""

    def __init__(self, iface):
        self.iface = iface

    # ------------------------------------------------------------------
    # dialogen
    # ------------------------------------------------------------------

    def aabn(self):
        dialog = QDialog(self.iface.mainWindow())
        dialog.setWindowTitle(TITEL)
        layout = QVBoxLayout(dialog)

        forklaring = QLabel(
            "Støttepunkter til afvandingsanalysen. Hvert punkt får en "
            "terrænkote fra højdemodellen og en afvandingsklasse nu og i "
            "fremtiden — af dem udledes afvandingsdybde og vandspejlskote.")
        forklaring.setWordWrap(True)
        layout.addWidget(forklaring)

        gruppe_lag = QGroupBox("1. Nyt støttepunktlag", dialog)
        lag_layout = QVBoxLayout(gruppe_lag)
        tekst = QLabel(
            "Opretter en GeoPackage med dropdowns og afledte felter. "
            "Digitalisér derefter punkterne i laget.")
        tekst.setWordWrap(True)
        lag_layout.addWidget(tekst)
        knap_nyt_lag = QPushButton("Opret nyt lag …", gruppe_lag)
        knap_nyt_lag.clicked.connect(self.nyt_lag)
        lag_layout.addWidget(knap_nyt_lag)
        layout.addWidget(gruppe_lag)

        gruppe_dtm = QGroupBox("2. Højdemodel (DTM)", dialog)
        dtm_layout = QVBoxLayout(gruppe_dtm)
        tekst = QLabel(
            "Vælges den her, henter nye punkter selv deres kote, og den "
            "foreslås i trin 3. Indlæs den i projektet først.")
        tekst.setWordWrap(True)
        dtm_layout.addWidget(tekst)
        kombo = QgsMapLayerComboBox(gruppe_dtm)
        kombo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        kombo.setAllowEmptyLayer(True)
        aktuel = self._dtm_lag()
        if aktuel is not None:
            kombo.setLayer(aktuel)
        dtm_layout.addWidget(kombo)
        layout.addWidget(gruppe_dtm)
        # Forbindes efter setLayer, så det foreslåede lag ikke melder sig selv.
        kombo.layerChanged.connect(self._dtm_valgt)

        gruppe_udfyld = QGroupBox("3. Terrænkoter", dialog)
        udfyld_layout = QVBoxLayout(gruppe_udfyld)
        tekst = QLabel(
            "Henter koter fra højdemodellen og genberegner afvandingsdybde og "
            "vandspejlskote.")
        tekst.setWordWrap(True)
        udfyld_layout.addWidget(tekst)
        knap_udfyld = QPushButton("Udfyld terrænkoter …", gruppe_udfyld)
        knap_udfyld.clicked.connect(self.udfyld)
        udfyld_layout.addWidget(knap_udfyld)
        layout.addWidget(gruppe_udfyld)

        knapper = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        knapper.rejected.connect(dialog.close)
        layout.addWidget(knapper)

        faelles_ui.anvend_stil(dialog)
        dialog.exec_()

    # ------------------------------------------------------------------
    # trinene
    # ------------------------------------------------------------------

    def nyt_lag(self):
        indstillinger = QSettings()
        senest = indstillinger.value(INDSTILLING_MAPPE, "")
        sti, _ = QFileDialog.getSaveFileName(
            self.iface.mainWindow(),
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
            QMessageBox.critical(self.iface.mainWindow(), TITEL, fejl)
            return

        indstillinger.setValue(INDSTILLING_MAPPE, os.path.dirname(sti))
        QgsProject.instance().addMapLayer(lag)
        self.iface.messageBar().pushSuccess(
            TITEL,
            "Laget '%s' er oprettet. Vælg højdemodellen i trin 2, før du "
            "digitaliserer — så får punkterne deres kote med det samme."
            % lagnavn)

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
        dtm = self._dtm_lag()
        if dtm is not None:
            parametre["DTM"] = dtm
        try:
            processing.execAlgorithmDialog(ALG_ID, parametre)
        except Exception as exc:
            QMessageBox.critical(
                self.iface.mainWindow(), TITEL,
                "Værktøjet kunne ikke åbnes: %s" % exc)
