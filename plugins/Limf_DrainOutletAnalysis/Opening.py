import numpy as np
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QComboBox, QPushButton, QLineEdit, QMessageBox,
    QTabWidget, QWidget, QApplication,
)
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsProject, QgsPointXY, QgsRasterLayer, QgsCoordinateTransform,
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsWkbTypes,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsLineSymbol,
    QgsFeatureRequest, QgsRectangle,
)
from qgis.gui import QgsMapToolEmitPoint, QgsVertexMarker


def _anvend_faelles_stil(dialog):
    """Hent husets dialogstil ved siden af denne fil.

    Filen her køres med exec() fra plugin.py og har derfor ingen pakke at
    importere fra — hverken 'from . import' eller almindelig import virker.
    Modulet indlæses derfor direkte fra sin sti. Kan det ikke findes, får
    dialogen bare Qt's standardudseende; det er ikke værd at fejle på.
    """
    import importlib.util
    import os

    sti = os.path.join(PLUGIN_DIR, "faelles_ui.py")
    try:
        spec = importlib.util.spec_from_file_location("faelles_ui", sti)
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
        modul.anvend_stil(dialog)
    except Exception:
        pass


# Sættes af plugin.py inden scriptet køres.
PLUGIN_DIR = globals().get("PLUGIN_DIR", "")


class DrainDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.start_point = None
        self.map_tool = None
        self.marker_start = None
        self.marker_result = None
        self.setWindowTitle("Drænudløbspunkter – Find udløbspunkt")
        self.setMinimumWidth(420)
        self._build_ui()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        main = QVBoxLayout(self)

        # ── Tabs ──────────────────────────────────────────────────────────
        self.tabs = QTabWidget()

        # Tab 0: Enkeltpunkt
        tab_single = QWidget()
        lay_single = QVBoxLayout(tab_single)
        grp_pt = QGroupBox("Startpunkt")
        frm = QFormLayout(grp_pt)
        self.btn_pick = QPushButton("Klik startpunkt på kortet…")
        self.btn_pick.clicked.connect(self._pick_point)
        frm.addRow(self.btn_pick)
        self.dhm_edit = QLineEdit()
        self.dhm_edit.setReadOnly(True)
        self.dhm_edit.setStyleSheet("background-color: #d0d0d0; color: #444;")
        self.dhm_edit.setPlaceholderText("— klik på kort —")
        frm.addRow("DHM værdi:", self.dhm_edit)
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(-9999, 9999)
        self.height_spin.setDecimals(3)
        self.height_spin.setSuffix(" m.o.h.")
        frm.addRow("Starthøjde (drænbund):", self.height_spin)
        lay_single.addWidget(grp_pt)
        lay_single.addStretch()
        self.tabs.addTab(tab_single, "Enkeltpunkt")

        # Tab 1: Punktlag (batch)
        tab_batch = QWidget()
        lay_batch = QVBoxLayout(tab_batch)
        grp_batch = QGroupBox("Punktlag")
        frm_batch = QFormLayout(grp_batch)
        self.point_layer_combo = QComboBox()
        self.point_layer_combo.currentIndexChanged.connect(self._on_point_layer_changed)
        frm_batch.addRow("Punktlag:", self.point_layer_combo)
        self.start_kote_field_combo = QComboBox()
        frm_batch.addRow("Startkote-felt:", self.start_kote_field_combo)
        btn_refresh = QPushButton("Opdater lagliste")
        btn_refresh.clicked.connect(self._populate_point_layers)
        frm_batch.addRow(btn_refresh)
        lbl_info = QLabel(
            "Vælg et talfeltet med drænbundskoten, eller lad stå tomt\n"
            "for automatisk DHM − 1 m per punkt."
        )
        lbl_info.setWordWrap(True)
        frm_batch.addRow(lbl_info)
        lay_batch.addWidget(grp_batch)
        lay_batch.addStretch()
        self.tabs.addTab(tab_batch, "Punktlag (batch)")

        self.tabs.currentChanged.connect(self._on_tab_changed)
        main.addWidget(self.tabs)

        # ── Parametre (fælles) ─────────────────────────────────────────────
        grp_par = QGroupBox("Parametre")
        frm2 = QFormLayout(grp_par)
        self.dem_combo = QComboBox()
        self._fill_dem_layers()
        frm2.addRow("DEM-lag:", self.dem_combo)
        self.slope_spin = QDoubleSpinBox()
        self.slope_spin.setRange(0.1, 500)
        self.slope_spin.setValue(2.0)
        self.slope_spin.setDecimals(2)
        self.slope_spin.setSuffix(" ‰")
        frm2.addRow("Fald:", self.slope_spin)
        self.radius_spin = QDoubleSpinBox()
        self.radius_spin.setRange(10, 50000)
        self.radius_spin.setValue(2000)
        self.radius_spin.setDecimals(0)
        self.radius_spin.setSuffix(" m")
        frm2.addRow("Max søgeradius:", self.radius_spin)
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-500, 500)
        self.offset_spin.setValue(10.0)
        self.offset_spin.setDecimals(1)
        self.offset_spin.setSuffix(" cm")
        frm2.addRow("Offset over/under terræn:", self.offset_spin)
        self.polygon_combo = QComboBox()
        self._fill_polygon_layers()
        frm2.addRow("Søgepolygon (valgfrit):", self.polygon_combo)
        main.addWidget(grp_par)

        # ── Knapper ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Find udløbspunkt")
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self._run)
        btn_row.addWidget(self.btn_run)
        btn_close = QPushButton("Luk")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        main.addLayout(btn_row)

        # ── Resultat ──────────────────────────────────────────────────────
        self.lbl_result = QLabel()
        self.lbl_result.setWordWrap(True)
        main.addWidget(self.lbl_result)

        _anvend_faelles_stil(self)
        self._populate_point_layers()

    def _fill_dem_layers(self):
        self.dem_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsRasterLayer) and layer.isValid():
                self.dem_combo.addItem(layer.name(), layer.id())

    def _populate_point_layers(self):
        self.point_layer_combo.blockSignals(True)
        self.point_layer_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if (isinstance(layer, QgsVectorLayer)
                    and layer.geometryType() == QgsWkbTypes.PointGeometry
                    and layer.isValid()):
                self.point_layer_combo.addItem(layer.name(), layer.id())
        self.point_layer_combo.blockSignals(False)
        self._on_point_layer_changed()
        self._on_tab_changed(self.tabs.currentIndex())

    def _on_point_layer_changed(self):
        self.start_kote_field_combo.clear()
        self.start_kote_field_combo.addItem("— DHM − 1 m (automatisk) —", None)
        lid = self.point_layer_combo.currentData()
        layer = QgsProject.instance().mapLayer(lid) if lid else None
        if layer is None:
            return
        numeric = (QVariant.Int, QVariant.LongLong, QVariant.Double)
        for field in layer.fields():
            if field.type() in numeric:
                self.start_kote_field_combo.addItem(field.name(), field.name())

    def _fill_polygon_layers(self):
        self.polygon_combo.clear()
        self.polygon_combo.addItem("— Ingen (brug radius) —", None)
        for layer in QgsProject.instance().mapLayers().values():
            if (isinstance(layer, QgsVectorLayer)
                    and layer.geometryType() == QgsWkbTypes.PolygonGeometry
                    and layer.isValid()):
                self.polygon_combo.addItem(layer.name(), layer.id())

    def _on_tab_changed(self, index):
        if index == 0:
            self.btn_run.setEnabled(self.start_point is not None)
            self.btn_run.setText("Find udløbspunkt")
        else:
            self.btn_run.setEnabled(self.point_layer_combo.count() > 0)
            self.btn_run.setText("Kør batch-analyse")

    # --------------------------------------------------------- Map picking --

    def _pick_point(self):
        self.hide()
        self.map_tool = QgsMapToolEmitPoint(self.canvas)
        self.map_tool.canvasClicked.connect(self._on_canvas_click)
        self.canvas.setMapTool(self.map_tool)

    def _on_canvas_click(self, point, button):
        self.start_point = point
        dem = self._get_dem_layer()
        if dem:
            pt_dem = self._to_dem_crs(point, dem)
            val, ok = dem.dataProvider().sample(pt_dem, 1)
            if ok and val == val:
                self.dhm_edit.setText(f"{val:.3f} m.o.h.")
                self.height_spin.setValue(val - 1.0)
        self._place_marker("start", point, QColor("blue"), QgsVertexMarker.ICON_CROSS)
        self.canvas.unsetMapTool(self.map_tool)
        self.btn_run.setEnabled(True)
        self.show()
        self.raise_()

    # ----------------------------------------------------------- Processing --

    def _run(self):
        if self.tabs.currentIndex() == 0:
            self._run_single()
        else:
            self._run_batch()

    def _run_single(self):
        dem = self._get_dem_layer()
        if dem is None:
            QMessageBox.warning(self, "Fejl", "Vælg et DEM-lag.")
            return
        if self.start_point is None:
            QMessageBox.warning(self, "Fejl", "Klik et startpunkt på kortet.")
            return

        start_h    = self.height_spin.value()
        slope      = self.slope_spin.value() / 1000.0
        max_radius = self.radius_spin.value()
        offset_m   = self.offset_spin.value() / 100.0
        clip_geom  = self._find_clip_geom(self.start_point, dem)

        res = self._analyse_point(self.start_point, start_h, dem,
                                  slope, max_radius, offset_m,
                                  clip_geom=clip_geom, show_errors=True)
        if res is None:
            return

        result_pt = res['result_pt']
        self._place_marker("result", result_pt, QColor("red"), QgsVertexMarker.ICON_BOX)
        self._add_drain_line(self.start_point, result_pt,
                             start_h, res['r_drain'],
                             res['start_status'], res['break_pt'], res['break_kote'])

        self.canvas.setCenter(result_pt)
        self.canvas.refresh()

        note = res['note']
        self.lbl_result.setText(
            f"<b>Udløbspunkt{note}:</b><br>"
            f"Afstand fra start: <b>{res['r_dist']:.1f} m</b><br>"
            f"Terrænkote (DEM): {res['r_dem']:.3f} m<br>"
            f"Drænbundskote ved udløb: {res['r_drain']:.3f} m<br>"
            f"Frispejl over terræn: {res['r_drain'] - res['r_dem']:.3f} m"
        )

    def _run_batch(self):
        dem = self._get_dem_layer()
        if dem is None:
            QMessageBox.warning(self, "Fejl", "Vælg et DEM-lag.")
            return

        lid = self.point_layer_combo.currentData()
        pt_layer = QgsProject.instance().mapLayer(lid) if lid else None
        if pt_layer is None:
            QMessageBox.warning(self, "Fejl", "Vælg et punktlag.")
            return

        slope      = self.slope_spin.value() / 1000.0
        max_radius = self.radius_spin.value()
        offset_m   = self.offset_spin.value() / 100.0

        kote_field = self.start_kote_field_combo.currentData()  # None = auto

        map_crs   = self.canvas.mapSettings().destinationCrs()
        layer_crs = pt_layer.crs()
        tr = (QgsCoordinateTransform(layer_crs, map_crs, QgsProject.instance())
              if map_crs != layer_crs else None)

        features = list(pt_layer.getFeatures())
        n_total = len(features)
        n_ok = n_err = 0

        self.btn_run.setEnabled(False)
        self.lbl_result.setText(f"Behandler 0 / {n_total}…")
        QApplication.processEvents()

        for i, feat in enumerate(features):
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                n_err += 1
                continue

            pt = geom.asPoint()
            if tr is not None:
                pt = tr.transform(pt)

            # Sample DHM (bruges altid til DHM-værdien; bruges som fallback til start_h)
            pt_dem = self._to_dem_crs(pt, dem)
            val, ok = dem.dataProvider().sample(pt_dem, 1)
            dhm_ok = ok and val == val  # not NaN

            # Bestem start_h: fra felt eller DHM − 1 m
            if kote_field is not None:
                field_val = feat[kote_field]
                if field_val is None or field_val != field_val:  # NULL / NaN
                    if not dhm_ok:
                        n_err += 1
                        continue
                    start_h = val - 1.0
                else:
                    start_h = float(field_val)
            else:
                if not dhm_ok:
                    n_err += 1
                    continue
                start_h = val - 1.0

            clip_geom = self._find_clip_geom(pt, dem)
            res = self._analyse_point(pt, start_h, dem,
                                      slope, max_radius, offset_m,
                                      clip_geom=clip_geom, show_errors=False)
            if res is None:
                n_err += 1
                continue

            self._add_drain_line(pt, res['result_pt'],
                                 start_h, res['r_drain'],
                                 res['start_status'], res['break_pt'], res['break_kote'])
            n_ok += 1

            if i % 5 == 0:
                self.lbl_result.setText(f"Behandler {i + 1} / {n_total}…")
                QApplication.processEvents()

        self.canvas.refresh()
        self.btn_run.setEnabled(True)
        err_str = f" ({n_err} fejlede.)" if n_err else ""
        self.lbl_result.setText(
            f"<b>Batch færdig:</b> {n_ok} af {n_total} punkter behandlet.{err_str}"
        )

    def _analyse_point(self, start_pt_map, start_h, dem,
                       slope, max_radius, offset_m,
                       clip_geom=None, show_errors=True):
        """Beregn drænudløb for ét punkt. Returnerer dict eller None ved fejl."""
        pt_dem = self._to_dem_crs(start_pt_map, dem)
        sx, sy = pt_dem.x(), pt_dem.y()

        try:
            dem_arr, px_w, px_h, x_min, y_max = self._read_dem_subset(
                dem, sx, sy, max_radius, clip_rect=clip_geom.boundingBox() if clip_geom else None)
        except Exception as e:
            if show_errors:
                QMessageBox.critical(self, "Fejl ved læsning af DEM", str(e))
            return None

        rows, cols = dem_arr.shape
        xs = x_min + (np.arange(cols) + 0.5) * px_w
        ys = y_max - (np.arange(rows) + 0.5) * px_h
        X, Y = np.meshgrid(xs, ys)

        dist       = np.sqrt((X - sx) ** 2 + (Y - sy) ** 2)
        drain_elev = start_h - slope * dist
        diff       = drain_elev - (dem_arr + offset_m)

        min_d = max(px_w, px_h)
        valid = (~np.isnan(dem_arr)) & (dist > min_d)

        # Begræns søgning til polygon via GDAL-rasterisering
        if clip_geom is not None:
            from osgeo import gdal, ogr
            mem_ds = gdal.GetDriverByName('MEM').Create('', cols, rows, 1, gdal.GDT_Byte)
            mem_ds.SetGeoTransform([x_min, px_w, 0, y_max, 0, -px_h])
            mem_ds.GetRasterBand(1).Fill(0)
            mem_vec = ogr.GetDriverByName('Memory').CreateDataSource('')
            mem_lyr = mem_vec.CreateLayer('')
            feat_ogr = ogr.Feature(mem_lyr.GetLayerDefn())
            feat_ogr.SetGeometry(ogr.CreateGeometryFromWkt(clip_geom.asWkt()))
            mem_lyr.CreateFeature(feat_ogr)
            gdal.RasterizeLayer(mem_ds, [1], mem_lyr, burn_values=[1])
            poly_mask = (np.frombuffer(mem_ds.GetRasterBand(1).ReadRaster(),
                                       dtype=np.uint8)
                           .reshape(rows, cols)
                           .astype(bool))
            valid = valid & poly_mask

        note = ""
        above = valid & (diff >= 0)
        if np.any(above):
            idx = np.unravel_index(
                np.argmin(np.where(above, dist, np.inf)), dist.shape)
        else:
            if not np.any(valid):
                if show_errors:
                    QMessageBox.warning(self, "Ingen resultat",
                                        "Ingen gyldige DEM-celler fundet.")
                return None
            idx = np.unravel_index(
                np.argmin(np.where(valid, np.abs(diff), np.inf)), diff.shape)
            offset_cm_str = f"{offset_m * 100:.1f} cm"
            note = (f" (approx. – drænet når ikke {offset_cm_str}"
                    f" over terræn inden for DEM)")

        r_dist  = float(dist[idx])
        r_dem   = float(dem_arr[idx])
        r_drain = float(drain_elev[idx])
        rx_dem  = float(X[idx])
        ry_dem  = float(Y[idx])

        rx_map, ry_map = self._from_dem_crs(QgsPointXY(rx_dem, ry_dem), dem)
        result_pt = QgsPointXY(rx_map, ry_map)

        # Find knækpunkt ved 50 cm dybde
        DEPTH_THRESHOLD = 0.50
        n_samples = max(20, int(r_dist / max(px_w, px_h)) + 1)
        ts = np.linspace(0, 1, n_samples)
        sample_x = sx + ts * (rx_dem - sx)
        sample_y = sy + ts * (ry_dem - sy)
        col_idx = np.clip(((sample_x - x_min) / px_w - 0.5).astype(int), 0, cols - 1)
        row_idx = np.clip(((y_max - sample_y) / px_h - 0.5).astype(int), 0, rows - 1)
        dem_along   = dem_arr[row_idx, col_idx]
        drain_along = start_h - slope * (ts * r_dist)
        depth_along = dem_along - drain_along

        start_status = "Lukket" if depth_along[0] >= DEPTH_THRESHOLD else "Åben"
        break_pt = break_kote = None
        for i in range(len(depth_along) - 1):
            d0, d1 = depth_along[i], depth_along[i + 1]
            if (not (np.isnan(d0) or np.isnan(d1))
                    and (d0 >= DEPTH_THRESHOLD) != (d1 >= DEPTH_THRESHOLD)):
                frac   = (DEPTH_THRESHOLD - d0) / (d1 - d0)
                t_b    = ts[i] + frac * (ts[i + 1] - ts[i])
                bx_dem = sx + t_b * (rx_dem - sx)
                by_dem = sy + t_b * (ry_dem - sy)
                bx_map, by_map = self._from_dem_crs(QgsPointXY(bx_dem, by_dem), dem)
                break_pt   = QgsPointXY(bx_map, by_map)
                break_kote = start_h - slope * (t_b * r_dist)
                break

        return {
            'result_pt':    result_pt,
            'r_dist':       r_dist,
            'r_dem':        r_dem,
            'r_drain':      r_drain,
            'start_status': start_status,
            'break_pt':     break_pt,
            'break_kote':   break_kote,
            'note':         note,
        }

    # --------------------------------------------------- Line memory layer --

    def _add_drain_line(self, start_pt, end_pt, start_kote, slut_kote,
                        start_status, break_pt=None, break_kote=None):
        crs_str = self.canvas.mapSettings().destinationCrs().authid()

        layer = next(
            (lyr for lyr in QgsProject.instance().mapLayers().values()
             if lyr.name() == "Drænlinjer"
             and lyr.geometryType() == QgsWkbTypes.LineGeometry),
            None
        )
        if layer is None:
            layer = QgsVectorLayer(f'LineString?crs={crs_str}', 'Drænlinjer', 'memory')
            layer.dataProvider().addAttributes([
                QgsField('start_kote', QVariant.Double),
                QgsField('slut_kote',  QVariant.Double),
                QgsField('status',     QVariant.String),
            ])
            layer.updateFields()
            self._apply_drain_renderer(layer)
            QgsProject.instance().addMapLayer(layer)
        else:
            if layer.fields().indexOf('status') < 0:
                layer.dataProvider().addAttributes([QgsField('status', QVariant.String)])
                layer.updateFields()
                self._apply_drain_renderer(layer)

        def make_feat(p1, p2, k1, k2, status):
            f = QgsFeature(layer.fields())
            f.setGeometry(QgsGeometry.fromPolylineXY([p1, p2]))
            f.setAttribute('start_kote', round(k1, 3))
            f.setAttribute('slut_kote',  round(k2, 3))
            f.setAttribute('status',     status)
            return f

        end_status = "Åben" if start_status == "Lukket" else "Lukket"
        if break_pt is None:
            feats = [make_feat(start_pt, end_pt, start_kote, slut_kote, start_status)]
        else:
            feats = [
                make_feat(start_pt, break_pt, start_kote, break_kote, start_status),
                make_feat(break_pt, end_pt,   break_kote, slut_kote,  end_status),
            ]

        layer.dataProvider().addFeatures(feats)
        layer.updateExtents()
        layer.triggerRepaint()

    def _apply_drain_renderer(self, layer):
        sym_aaben  = QgsLineSymbol.createSimple({'color': '#27AE60', 'line_width': '0.8'})
        sym_lukket = QgsLineSymbol.createSimple({'color': '#E74C3C', 'line_width': '0.8'})
        categories = [
            QgsRendererCategory('Åben',   sym_aaben,  'Åben'),
            QgsRendererCategory('Lukket', sym_lukket, 'Lukket'),
        ]
        layer.setRenderer(QgsCategorizedSymbolRenderer('status', categories))

    # ------------------------------------------------------------ Helpers --

    def _get_dem_layer(self):
        lid = self.dem_combo.currentData()
        return QgsProject.instance().mapLayer(lid) if lid else None

    def _to_dem_crs(self, point, dem):
        map_crs = self.canvas.mapSettings().destinationCrs()
        dem_crs = dem.crs()
        if map_crs == dem_crs:
            return point
        tr = QgsCoordinateTransform(map_crs, dem_crs, QgsProject.instance())
        return tr.transform(point)

    def _from_dem_crs(self, point, dem):
        map_crs = self.canvas.mapSettings().destinationCrs()
        dem_crs = dem.crs()
        if map_crs == dem_crs:
            return point.x(), point.y()
        tr = QgsCoordinateTransform(dem_crs, map_crs, QgsProject.instance())
        p = tr.transform(point)
        return p.x(), p.y()

    def _read_dem_subset(self, dem, center_x, center_y, max_radius, clip_rect=None):
        """Læs DEM-udsnit. clip_rect (QgsRectangle i DEM-CRS) tilsidesætter radius."""
        provider    = dem.dataProvider()
        full_extent = dem.extent()
        w_full      = dem.width()
        h_full      = dem.height()
        px_w = full_extent.width()  / w_full
        px_h = full_extent.height() / h_full

        if clip_rect is not None:
            search = clip_rect
        else:
            search = QgsRectangle(
                center_x - max_radius, center_y - max_radius,
                center_x + max_radius, center_y + max_radius,
            )
        clip = full_extent.intersect(search)
        if clip.isEmpty():
            raise ValueError("Startpunktet ligger uden for DEM-laget.")

        w = max(1, int(round(clip.width()  / px_w)))
        h = max(1, int(round(clip.height() / px_h)))

        block = provider.block(1, clip, w, h)

        dtype_map = {
            1: np.uint8,  2: np.uint16, 3: np.int16,
            4: np.uint32, 5: np.int32,  6: np.float32, 7: np.float64,
        }
        np_dtype = dtype_map.get(int(provider.dataType(1)), np.float32)

        arr = (np.frombuffer(bytes(block.data()), dtype=np_dtype)
                 .reshape(h, w)
                 .astype(np.float64))

        if provider.sourceHasNoDataValue(1):
            arr[arr == provider.sourceNoDataValue(1)] = np.nan

        return arr, px_w, px_h, clip.xMinimum(), clip.yMaximum()

    def _find_clip_geom(self, pt_map, dem):
        """Find polygon i det valgte polygon-lag der indeholder pt_map.
        Returnerer geometrien transformeret til DEM-CRS, eller None."""
        lid = self.polygon_combo.currentData()
        if lid is None:
            return None
        poly_layer = QgsProject.instance().mapLayer(lid)
        if poly_layer is None:
            return None

        map_crs  = self.canvas.mapSettings().destinationCrs()
        poly_crs = poly_layer.crs()
        dem_crs  = dem.crs()

        # Punkt i polygon-lagets CRS
        if map_crs != poly_crs:
            tr = QgsCoordinateTransform(map_crs, poly_crs, QgsProject.instance())
            pt_in_poly = tr.transform(pt_map)
        else:
            pt_in_poly = pt_map

        pt_geom = QgsGeometry.fromPointXY(pt_in_poly)
        for feat in poly_layer.getFeatures(
                QgsFeatureRequest().setFilterRect(pt_geom.boundingBox())):
            geom = feat.geometry()
            if geom and geom.contains(pt_geom):
                # Transformer til DEM-CRS inden rasterisering
                if poly_crs != dem_crs:
                    geom.transform(
                        QgsCoordinateTransform(poly_crs, dem_crs, QgsProject.instance()))
                return geom
        return None

    def _place_marker(self, name, point, color, icon):
        attr = f"marker_{name}"
        old = getattr(self, attr, None)
        if old:
            self.canvas.scene().removeItem(old)
        m = QgsVertexMarker(self.canvas)
        m.setCenter(point)
        m.setColor(color)
        m.setIconSize(14)
        m.setIconType(icon)
        m.setPenWidth(3)
        setattr(self, attr, m)

    def closeEvent(self, event):
        if self.map_tool:
            self.canvas.unsetMapTool(self.map_tool)
        for attr in ("marker_start", "marker_result"):
            m = getattr(self, attr, None)
            if m:
                self.canvas.scene().removeItem(m)
        super().closeEvent(event)


# ── Åbn dialogen (bring eksisterende i fokus hvis allerede åben) ──────────────
for _w in iface.mainWindow().findChildren(QDialog):
    if _w.windowTitle().startswith("Drænudløbspunkter") and _w.isVisible():
        _w.raise_()
        _w.activateWindow()
        break
else:
    _dlg = DrainDialog(iface)
    _dlg.show()
