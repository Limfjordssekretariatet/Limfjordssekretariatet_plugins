# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Limfjordssekretariatet_toolsDialog
                                 A QGIS plugin dialog
 Limfjordssekretariatet forskellige tools
 ***************************************************************************/
"""

import os
import sys

from qgis.PyQt import uic
from qgis.PyQt import QtWidgets

# Processing
from qgis import processing

# Gør plugin-resourcer tilgængelige som 'resources_rc' for .ui-filen
from . import resources as resources_rc
sys.modules['resources_rc'] = resources_rc

# Load the UI file designed in Qt Designer
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'Limf_tools_dialog_base.ui'))

from . import faelles_ui


class Limfjordssekretariatet_toolsDialog(QtWidgets.QDialog, FORM_CLASS):

    def __init__(self, parent=None, iface=None):
        """Constructor."""
        super(Limfjordssekretariatet_toolsDialog, self).__init__(parent)
        self.iface = iface

        # Build UI
        self.setupUi(self)
        faelles_ui.anvend_stil(self)
        self.InterpolerBtn.clicked.connect(self.koer_interpoler_terraen)

        # Connect UI buttons to functions
        self.JordbalanceBtn.clicked.connect(self.jordberegning)
        self.GridTilLERBtn.clicked.connect(self.grid_til_ler)
        self.StoettepunkterBtn.clicked.connect(self.stoettepunkter)
        self.UdpegOplandBtn.clicked.connect(self.udpeg_opland)
        self._udpeg_dialog = None

    # Afvandingsanalyse, "Brænd vandløb i terræn" og "Terræn til VASP" er
    # flyttet til VASP-pluginnet, hvor profiler, tværprofiler og vandspejl
    # hentes direkte fra databasen i stedet for at skulle eksporteres først.

    def koer_interpoler_terraen(self):
        from .InterpolateTerrain import InterpolerTerrn
        alg = InterpolerTerrn()
        processing.execAlgorithmDialog(alg)
        
    def grid_til_ler(self):
        from .GridTilLER import GridTilLER
        alg = GridTilLER()
        processing.execAlgorithmDialog(alg)

    def stoettepunkter(self):
        """Åbn støttepunkt-panelet.

        Denne dialog lukkes først. Panelets trin forudsætter, at man kan
        arbejde på kortet imellem — indlæse højdemodellen, digitalisere
        punkter — og det kan man ikke bag to modale dialoger.
        """
        from .stoettepunkter.panel import StoettepunktPanel

        iface = self.iface
        if iface is None:
            from qgis.utils import iface
        self.accept()
        StoettepunktPanel(iface).aabn()

    def udpeg_opland(self):
        """Åbn Udpeg opland, som når den åbnes fra Værktøjskassen.

        Denne dialog lukkes først, og værktøjet åbnes ikke-modalt: et punkt
        skal kunne prikkes på kortet, og det kan man ikke bag en modal
        dialog. Den åbnes først, når denne dialogs egen løkke er slut.
        """
        from qgis.PyQt.QtCore import QTimer
        from .udpeg_opland.provider import ALGORITME_ID

        self.accept()

        def aabn():
            try:
                dialog = processing.createAlgorithmDialog(ALGORITME_ID)
            except Exception as e:
                dialog = None
                fejl = str(e)
            else:
                fejl = ''
            if dialog is None:
                QtWidgets.QMessageBox.warning(
                    self.parent() or None, "Udpeg opland",
                    "Udpeg opland findes ikke i Værktøjskassen. Er det gamle "
                    "Udpeg opland-plugin stadig installeret? Så skal det "
                    "afinstalleres, og QGIS genstartes.\n\n" + fejl)
                return
            dialog.show()
            # Holdes i live — ellers rydder Python den væk med det samme.
            self._udpeg_dialog = dialog

        QTimer.singleShot(0, aabn)

    def jordberegning(self):
        """Åbner QGIS' standard parameterdialog for jordbalance-modellen."""
        try:
            from .jordberegning import DHMVolumen

            alg = DHMVolumen()
            processing.execAlgorithmDialog(alg)

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Fejl i jordberegning",
                f"Der opstod en fejl under kørsel af jordberegning:\n{e}"
            )
