import os
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature,
    QgsField, QgsWkbTypes
)

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'lav_centerpunkter_dialog.ui'))


from . import faelles_ui


class LavCenterpunkterDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        faelles_ui.anvend_stil(self)
        self._populate_lag()
        self.btnKor.clicked.connect(self.kor)

    def _populate_lag(self):
        self.cboLag.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == layer.VectorLayer:
                if layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                    self.cboLag.addItem(layer.name(), layer.id())

    def kor(self):
        layer_id = self.cboLag.currentData()
        if not layer_id:
            QMessageBox.warning(self, 'Fejl', 'Vælg et polygonlag.')
            return

        poly_layer = QgsProject.instance().mapLayer(layer_id)
        if not poly_layer:
            QMessageBox.warning(self, 'Fejl', 'Laget kunne ikke findes.')
            return

        crs_id = poly_layer.crs().authid()
        point_layer = QgsVectorLayer(f'Point?crs={crs_id}', 'Centerpunkter', 'memory')
        provider = point_layer.dataProvider()

        # Kopiér felter fra inputlaget
        provider.addAttributes(poly_layer.fields().toList())
        point_layer.updateFields()

        features = []
        for feat in poly_layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isNull() or geom.isEmpty():
                continue
            center = geom.pointOnSurface()
            new_feat = QgsFeature(point_layer.fields())
            new_feat.setGeometry(center)
            new_feat.setAttributes(feat.attributes())
            features.append(new_feat)

        if not features:
            QMessageBox.warning(self, 'Fejl', 'Ingen geometrier fundet i laget.')
            return

        provider.addFeatures(features)
        point_layer.updateExtents()
        QgsProject.instance().addMapLayer(point_layer)

        QMessageBox.information(
            self, 'Centerpunkter oprettet',
            f'{len(features)} centerpunkter oprettet fra "{poly_layer.name()}".'
        )
        self.accept()
