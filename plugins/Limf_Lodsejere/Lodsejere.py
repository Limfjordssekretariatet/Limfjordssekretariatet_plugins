import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDialog, QMessageBox

from . import faelles_gui


class Lodsejere:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        icon_sti = os.path.join(os.path.dirname(__file__), 'icon.png')
        icon = QIcon(icon_sti) if os.path.exists(icon_sti) else QIcon()
        self.action = QAction(icon, 'Lodsejerudtræk …', self.iface.mainWindow())
        self.action.setStatusTip(
            'Hent matrikler og ejeroplysninger for et valgt polygon')
        self.action.triggered.connect(self.run)
        faelles_gui.tilfoej(self.iface, self.action)

    def unload(self):
        if self.action is not None:
            faelles_gui.fjern(self.iface, self.action)
            self.action = None

    def run(self):
        from .select_polygon_dialog import SelectPolygonDialog

        sel_dlg = SelectPolygonDialog(self.iface)
        if not sel_dlg._layers:
            QMessageBox.warning(
                None, 'Lodsejere',
                'Projektet indeholder ingen polygon-lag.'
            )
            return

        if sel_dlg.exec_() != QDialog.Accepted:
            return

        geometry = sel_dlg.selected_geometry()
        layer = sel_dlg.selected_layer()

        if geometry is None or geometry.isEmpty():
            QMessageBox.warning(None, 'Lodsejere', 'Den valgte geometri er tom.')
            return

        from .dialog import LodsejerDialog
        dlg = LodsejerDialog(self.iface, geometry, layer.crs())
        dlg.exec_()
