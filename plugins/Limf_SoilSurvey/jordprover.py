import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from . import faelles_gui
from .jordprover_dialog import JordproverDialog


class Jordprover:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), 'icon.png'))
        self.action = QAction(icon, 'Jordbund …', self.iface.mainWindow())
        self.action.setStatusTip(
            'Jordprøver: grid, centerpunkter, QField-klargøring og rapporter')
        self.action.triggered.connect(self.run)
        faelles_gui.tilfoej(self.iface, self.action)

    def unload(self):
        if self.action is not None:
            faelles_gui.fjern(self.iface, self.action)
            self.action = None

    def run(self):
        if self.dialog is None:
            self.dialog = JordproverDialog(self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
