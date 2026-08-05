import os
from qgis.PyQt import uic, QtWidgets
from .lav_grid_dialog import LavGridDialog
from .lav_centerpunkter_dialog import LavCenterpunkterDialog
from .klargor_qfield_dialog import KlargorQFieldDialog
from .eksporter_rapport_dialog import EksporterRapportDialog

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'jordprover_dialog.ui'))


from . import faelles_ui


class JordproverDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        faelles_ui.anvend_stil(self)

        self.btnLavGrid.clicked.connect(self.lav_grid)
        self.btnLavCenterpunkter.clicked.connect(self.lav_centerpunkter)
        self.btnKlargorQField.clicked.connect(self.klargor_qfield)
        self.btnLavPDF.clicked.connect(self.eksporter_rapport)

    def lav_grid(self):
        dlg = LavGridDialog(self)
        dlg.exec_()

    def lav_centerpunkter(self):
        dlg = LavCenterpunkterDialog(self)
        dlg.exec_()

    def klargor_qfield(self):
        dlg = KlargorQFieldDialog(self)
        dlg.exec_()

    def eksporter_rapport(self):
        dlg = EksporterRapportDialog(self)
        dlg.exec_()
