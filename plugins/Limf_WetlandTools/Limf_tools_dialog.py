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

    def __init__(self, parent=None):
        """Constructor."""
        super(Limfjordssekretariatet_toolsDialog, self).__init__(parent)

        # Build UI
        self.setupUi(self)
        faelles_ui.anvend_stil(self)
        self.InterpolerBtn.clicked.connect(self.koer_interpoler_terraen)

        # Connect UI buttons to functions
        self.AfvandingsanalyseBtn.clicked.connect(self.koer_afvandingsanalyse)
        self.VASPBtn.clicked.connect(self.koer_vasp_excel)
        self.BurnBtn.clicked.connect(self.koer_fra_mike_til_dhm)   
        self.JordbalanceBtn.clicked.connect(self.jordberegning)
        self.GridTilLERBtn.clicked.connect(self.grid_til_ler)


    def koer_afvandingsanalyse(self):
        """Åbner QGIS' standard parameterdialog for afvandings-modellen"""

        # Importér modellen (instantieres ved kørsel)
        from .Afvandingsanalyse_v1_0 import Afvandingsmodelqgisoktober2025gdal

        # Lav en instans af modellen
        alg = Afvandingsmodelqgisoktober2025gdal()

        # Åbn QGIS standard dialog for algoritmen, så bruger selv vælger lag
        processing.execAlgorithmDialog(alg)


    def koer_vasp_excel(self):
        """Åbner QGIS' standard parameterdialog for VASPExcel-modellen"""
        from .VaspExcel import Vaspexcelbegge
        alg = Vaspexcelbegge()
        processing.execAlgorithmDialog(alg)

    def koer_interpoler_terraen(self):
        from .InterpolateTerrain import InterpolerTerrn
        alg = InterpolerTerrn()
        processing.execAlgorithmDialog(alg)
        
    def koer_fra_mike_til_dhm(self):
        """Åbner Processing-dialog til 'Fra MIKE til DHM'."""
        try:
            from .FraMikeTilDHM import FraMikeTilDHMAlgorithm
            alg = FraMikeTilDHMAlgorithm()
            processing.execAlgorithmDialog(alg)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Fejl i FraMikeTilDHM",
                f"Der opstod en fejl under kørsel af FraMikeTilDHM:\n{e}"
            )

    def grid_til_ler(self):
        from .GridTilLER import GridTilLER
        alg = GridTilLER()
        processing.execAlgorithmDialog(alg)

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
