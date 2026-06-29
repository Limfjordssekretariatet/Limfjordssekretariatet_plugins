from qgis.PyQt.QtWidgets import QAction, QDialog, QMessageBox


class Lodsejere:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        self.action = QAction('Hent Lodsejere', self.iface.mainWindow())
        self.action.setToolTip('Hent matrikler og ejeroplysninger for valgt polygon')
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('Lodsejere', self.action)

    def unload(self):
        self.iface.removePluginMenu('Lodsejere', self.action)
        self.iface.removeToolBarIcon(self.action)

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
