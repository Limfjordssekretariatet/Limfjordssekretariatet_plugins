import os
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog
from qgis.core import (
    QgsProject, QgsWkbTypes, QgsGeometry,
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
)

FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), 'select_polygon.ui')
)


from . import faelles_ui


class SelectPolygonDialog(QDialog, FORM_CLASS):
    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self._layers = []    # QgsVectorLayer i samme rækkefølge som combo_lag
        self._features = []  # QgsFeature i samme rækkefølge som combo_objekt
        self.setupUi(self)
        faelles_ui.anvend_stil(self)
        self._populate_layers()
        self.combo_lag.currentIndexChanged.connect(self._on_layer_changed)

    # ------------------------------------------------------------------

    def _populate_layers(self):
        self.combo_lag.clear()
        self._layers = []
        active = self.iface.activeLayer()
        active_idx = -1

        for layer in QgsProject.instance().mapLayers().values():
            if (hasattr(layer, 'geometryType') and
                    layer.geometryType() == QgsWkbTypes.PolygonGeometry):
                if layer is active:
                    active_idx = len(self._layers)
                self._layers.append(layer)
                self.combo_lag.addItem(layer.name())

        if active_idx >= 0:
            self.combo_lag.setCurrentIndex(active_idx)

        self._on_layer_changed()

    def _on_layer_changed(self):
        self.combo_objekt.clear()
        self._features = []
        idx = self.combo_lag.currentIndex()

        if idx < 0 or idx >= len(self._layers):
            self.label_antal.setText('')
            return

        layer = self._layers[idx]
        expr_ctx = QgsExpressionContext()
        expr_ctx.appendScopes(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer)
        )
        display_expr = QgsExpression(layer.displayExpression())
        selected_ids = {f.id() for f in layer.selectedFeatures()}

        for feat in layer.getFeatures():
            expr_ctx.setFeature(feat)
            label = display_expr.evaluate(expr_ctx)
            # NULL eller None falder tilbage til feature-ID
            if label is None or str(label) in ('NULL', ''):
                label = str(feat.id())
            else:
                label = str(label)
            if feat.id() in selected_ids:
                label = f'[Markeret] {label}'
            self._features.append(feat)
            self.combo_objekt.addItem(label)

        # Forvalgt: første markerede objekt, ellers første objekt
        preselect = next(
            (i for i, f in enumerate(self._features) if f.id() in selected_ids),
            0,
        )
        self.combo_objekt.setCurrentIndex(preselect)

        count = layer.featureCount()
        self.label_antal.setText(f'{count} objekt(er) i laget')

    # ------------------------------------------------------------------

    def selected_geometry(self):
        """Returnerer QgsGeometry for det valgte objekt, eller None."""
        idx = self.combo_objekt.currentIndex()
        if idx < 0 or idx >= len(self._features):
            return None
        geom = self._features[idx].geometry()
        return QgsGeometry(geom) if geom and not geom.isEmpty() else None

    def selected_layer(self):
        """Returnerer det valgte QgsVectorLayer, eller None."""
        idx = self.combo_lag.currentIndex()
        if idx < 0 or idx >= len(self._layers):
            return None
        return self._layers[idx]
