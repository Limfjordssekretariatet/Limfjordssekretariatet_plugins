import math
import os
import processing
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsField, QgsPointXY, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCoordinateTransformContext, QgsFeatureRequest
)
from .Sliver import clean_layer

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'lav_grid_dialog.ui'))

MARKKORT_PATH = os.path.join(os.path.dirname(__file__), 'Data', 'Markkort', 'Markkort_V3_snap.shp')
MAX_ASPECT_RATIO = 3.0   # default max length/width ratio before subdivision kicks in


class LavGridDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self._populate_lag()
        self.btnKorGrid.clicked.connect(self.kor_grid)

    def _populate_lag(self):
        self.cboLag.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == layer.VectorLayer:
                if layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                    self.cboLag.addItem(layer.name(), layer.id())

    def kor_grid(self):
        layer_id = self.cboLag.currentData()
        if not layer_id:
            QMessageBox.warning(self, 'Fejl', 'Vælg et polygonlag.')
            return

        grense_layer = QgsProject.instance().mapLayer(layer_id)
        if not grense_layer:
            QMessageBox.warning(self, 'Fejl', 'Laget kunne ikke findes.')
            return

        avg_ha = self.spinAvgHa.value()
        max_ha = self.spinMaxHa.value()
        min_ha = self.spinMinHa.value()

        if min_ha > avg_ha:
            QMessageBox.warning(self, 'Fejl', 'Min størrelse må ikke være større end gennemsnitsstørrelse.')
            return
        if avg_ha > max_ha:
            QMessageBox.warning(self, 'Fejl', 'Gennemsnitsstørrelse må ikke være større end max størrelse.')
            return

        work_crs = QgsCoordinateReferenceSystem('EPSG:25832')
        src_crs = grense_layer.crs()
        to_work = QgsCoordinateTransform(src_crs, work_crs, QgsCoordinateTransformContext())
        need_transform = src_crs != work_crs

        union_geom = None
        for feat in grense_layer.getFeatures():
            geom = feat.geometry()
            if not geom or geom.isNull() or geom.isEmpty():
                continue
            if need_transform:
                geom.transform(to_work)
            union_geom = QgsGeometry(geom) if union_geom is None else union_geom.combine(geom)

        if not union_geom or union_geom.isNull() or union_geom.isEmpty():
            QMessageBox.warning(self, 'Fejl', 'Laget indeholder ingen geometri.')
            return

        # Fase 1: Byg grid-polygoner (markkort + vandløbssplit + opsplit + sammenflet)
        stream_geom, n_streams = self._load_streams(union_geom, work_crs)
        parcels = self._load_markkort_parcels(union_geom, work_crs)
        if stream_geom is not None:
            parcels = self._split_by_streams(parcels, stream_geom, work_crs)
        parcels = self._subdivide_large(parcels, avg_ha, max_ha, min_ha)
        parcels = self._merge_small(parcels, min_ha, stream_geom=stream_geom)

        if not parcels:
            QMessageBox.warning(self, 'Fejl', 'Ingen felter blev oprettet.')
            return

        # Fase 2: Rens geometriske hairline-artefakter (konservativ d=0.5m)
        temp_layer = self._build_layer(parcels)
        temp_layer = processing.run("native:fixgeometries",
                                    {"INPUT": temp_layer, "OUTPUT": "memory:"})["OUTPUT"]
        try:
            temp_layer = clean_layer(temp_layer, d=15.0, min_area=1,
                                     grid_size=0.001, max_iters=3, despike=0.1)
        except Exception:
            pass  # sliver-rensning fejlede – fortsæt med urenset grid

        # Fase 3: Re-normaliser størrelser efter rensning
        cleaned = [QgsGeometry(f.geometry()) for f in temp_layer.getFeatures()
                   if not f.geometry().isEmpty() and f.geometry().area() >= 1]
        cleaned = self._merge_small(cleaned, min_ha, stream_geom=stream_geom)
        cleaned = self._subdivide_large(cleaned, avg_ha, max_ha, min_ha)

        # Fase 3.5: Re-håndhæv vandløbsgrænser (sliver-rensning kan have smeltet på tværs)
        if stream_geom is not None:
            cleaned = self._split_by_streams(cleaned, stream_geom, work_crs)
            cleaned = self._merge_small(cleaned, min_ha, stream_geom=stream_geom)

        if not cleaned:
            QMessageBox.warning(self, 'Fejl', 'Ingen felter efter re-normalisering.')
            return

        # Fase 4: Byg endeligt lag
        grid_layer = self._build_layer(cleaned, name='Grid')
        QgsProject.instance().addMapLayer(grid_layer)

        total_ha = sum(f.geometry().area() / 10000 for f in grid_layer.getFeatures())
        avg_result = total_ha / grid_layer.featureCount()
        stream_info = (f'\nVandløb: {n_streams} features lastet og anvendt.'
                       if n_streams else '\nVandløb: ingen features fundet i området.')
        QMessageBox.information(
            self, 'Grid oprettet',
            f'Grid oprettet med {grid_layer.featureCount()} felter.\n'
            f'Gennemsnitsstørrelse: {avg_result:.2f} ha\n'
            f'Total areal: {total_ha:.1f} ha'
            + stream_info
        )
        self.accept()

    def _build_layer(self, parcels, name='Grid_temp'):
        """Byg memory-polygon-lag med id/areal_ha fra en liste af QgsGeometry."""
        layer = QgsVectorLayer('Polygon?crs=EPSG:25832', name, 'memory')
        provider = layer.dataProvider()
        provider.addAttributes([
            QgsField('id', QVariant.Int),
            QgsField('areal_ha', QVariant.Double),
        ])
        layer.updateFields()
        features = []
        fid = 1
        for geom in parcels:
            for part in self._single_parts(geom):
                if part.area() < 1:
                    continue
                feat = QgsFeature()
                feat.setGeometry(part)
                feat.setAttributes([fid, round(part.area() / 10000, 4)])
                features.append(feat)
                fid += 1
        provider.addFeatures(features)
        layer.updateExtents()
        return layer

    def _load_streams(self, union_geom, work_crs):
        """Load alle .shp-filer fra Data/Streams, transformer til work_crs.
        Returnerer (QgsGeometry|None, antal_features)."""
        from qgis.core import QgsMessageLog
        streams_dir = os.path.join(os.path.dirname(__file__), 'Data', 'Streams')
        if not os.path.isdir(streams_dir):
            QgsMessageLog.logMessage(f'SoilSurvey: streams-mappe ikke fundet: {streams_dir}', 'SoilSurvey')
            return None, 0
        bbox = union_geom.boundingBox()
        all_geoms = []
        for fname in sorted(os.listdir(streams_dir)):
            if not fname.lower().endswith('.shp'):
                continue
            path = os.path.join(streams_dir, fname)
            layer = QgsVectorLayer(path, 'stream', 'ogr')
            if not layer.isValid():
                QgsMessageLog.logMessage(f'SoilSurvey: kan ikke åbne {fname}', 'SoilSurvey')
                continue
            layer_crs = layer.crs()
            need_tr = layer_crs != work_crs
            if need_tr:
                tr_rev = QgsCoordinateTransform(work_crs, layer_crs, QgsCoordinateTransformContext())
                filter_rect = tr_rev.transformBoundingBox(bbox)
                tr_fwd = QgsCoordinateTransform(layer_crs, work_crs, QgsCoordinateTransformContext())
            else:
                filter_rect = bbox
                tr_fwd = None
            for feat in layer.getFeatures(QgsFeatureRequest().setFilterRect(filter_rect)):
                geom = feat.geometry()
                if not geom or geom.isNull() or geom.isEmpty():
                    continue
                if tr_fwd:
                    geom.transform(tr_fwd)
                all_geoms.append(QgsGeometry(geom))
        n = len(all_geoms)
        QgsMessageLog.logMessage(f'SoilSurvey: {n} vandløbsfeatures fundet i bbox', 'SoilSurvey')
        if not all_geoms:
            return None, 0
        result = all_geoms[0]
        for g in all_geoms[1:]:
            result = result.combine(g)
        return result, n

    def _split_by_streams(self, parcels, stream_geom, work_crs):
        """Split parceller langs vandløbslinjer med native:splitwithlines.
        Hver stream-feature tilføjes individuelt for at undgå MultiLineString-typemismatch."""
        from qgis.core import QgsMessageLog

        parcel_layer = self._build_layer(parcels)

        # Dekomponer stream_geom til individuelle LineString-features
        stream_layer = QgsVectorLayer(
            f'LineString?crs={work_crs.authid()}', 'streams', 'memory')
        dp = stream_layer.dataProvider()
        stream_feats = []
        for part in stream_geom.asGeometryCollection():
            if part and not part.isEmpty():
                f = QgsFeature()
                f.setGeometry(part)
                stream_feats.append(f)
        if not stream_feats:
            return parcels
        dp.addFeatures(stream_feats)
        stream_layer.updateExtents()

        result_layer = processing.run('native:splitwithlines', {
            'INPUT': parcel_layer,
            'LINES': stream_layer,
            'OUTPUT': 'memory:'
        })['OUTPUT']

        result = [QgsGeometry(f.geometry()) for f in result_layer.getFeatures()
                  if not f.geometry().isEmpty() and f.geometry().area() >= 1]
        QgsMessageLog.logMessage(
            f'SoilSurvey: vandløbssplit {len(parcels)} → {len(result)} parceller '
            f'(+{len(result) - len(parcels)} nye)',
            'SoilSurvey')
        return result if result else parcels

    def _load_markkort_parcels(self, union_geom, work_crs):
        """Clip Markkort2024 to union_geom, dissolve overlapping/duplicate features
        via unaryUnion (no buffer — avoids pill-shaped artefacts), and return
        a list of individual polygon QgsGeometry objects."""
        if not os.path.exists(MARKKORT_PATH):
            return [QgsGeometry(union_geom)]

        mk_layer = QgsVectorLayer(MARKKORT_PATH, 'mk', 'ogr')
        if not mk_layer.isValid():
            return [QgsGeometry(union_geom)]

        mk_crs = mk_layer.crs()
        to_work = QgsCoordinateTransform(mk_crs, work_crs, QgsCoordinateTransformContext())
        from_work = QgsCoordinateTransform(work_crs, mk_crs, QgsCoordinateTransformContext())
        need_transform = mk_crs != work_crs

        if need_transform:
            filter_geom = QgsGeometry.fromRect(union_geom.boundingBox())
            filter_geom.transform(from_work)
            filter_rect = filter_geom.boundingBox()
        else:
            filter_rect = union_geom.boundingBox()

        raw_geoms = []
        for feat in mk_layer.getFeatures(QgsFeatureRequest().setFilterRect(filter_rect)):
            geom = feat.geometry()
            if not geom or geom.isNull() or geom.isEmpty():
                continue
            if need_transform:
                geom.transform(to_work)
            if not geom.isGeosValid():
                geom = geom.makeValid()
            clipped = geom.intersection(union_geom)
            if not clipped or clipped.isNull() or clipped.isEmpty():
                continue
            clipped = self._extract_polygons(clipped)
            if clipped is None or clipped.area() < 1:
                continue
            raw_geoms.append(clipped)

        if not raw_geoms:
            return [QgsGeometry(union_geom)]

        # Dissolve overlapping / duplicate features cleanly (no buffer → no artefacts)
        dissolved = QgsGeometry.unaryUnion(raw_geoms)
        if not dissolved or dissolved.isNull() or dissolved.isEmpty():
            return [QgsGeometry(union_geom)]

        # Split dissolved result into individual single-polygon parcels,
        # clipped to union_geom.  We iterate over every polygon part so that
        # concave Markkort features never produce disconnected MultiPolygons.
        parcels = []
        mk_coverage = None
        for part in dissolved.asGeometryCollection():
            if QgsWkbTypes.geometryType(part.wkbType()) != QgsWkbTypes.PolygonGeometry:
                continue
            cb = part.intersection(union_geom)
            if not cb or cb.isNull() or cb.isEmpty():
                continue
            for sub in self._single_parts(cb):
                if sub.area() < 100:
                    continue
                parcels.append(sub)
                mk_coverage = (QgsGeometry(sub) if mk_coverage is None
                               else mk_coverage.combine(sub))

        # Include any area inside union_geom not covered by the dissolved Markkort
        if mk_coverage is not None:
            uncovered = union_geom.difference(mk_coverage)
            if uncovered and not uncovered.isNull() and not uncovered.isEmpty():
                for part in uncovered.asGeometryCollection():
                    if (QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.PolygonGeometry
                            and part.area() > 100):
                        parcels.append(part)

        return parcels if parcels else [QgsGeometry(union_geom)]

    def _single_parts(self, geom):
        """Yield each individual Polygon part from any geometry (drops non-polygon parts)."""
        if geom is None or geom.isNull() or geom.isEmpty():
            return
        for part in geom.asGeometryCollection():
            if QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.PolygonGeometry:
                yield part

    def _extract_polygons(self, geom):
        """Return polygon-only geometry (drops lines/points from mixed collections)."""
        if geom is None or geom.isNull() or geom.isEmpty():
            return None
        if QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.PolygonGeometry:
            return geom
        poly_parts = list(self._single_parts(geom))
        if not poly_parts:
            return None
        result = poly_parts[0]
        for p in poly_parts[1:]:
            result = result.combine(p)
        return result

    def _get_mbr_params(self, geom):
        """Compute minimum bounding rectangle (rotating calipers on convex hull).
        Returns (cx, cy, angle_rad, length, width) where length >= width and
        angle_rad is the direction of the long axis (east = 0, north = π/2)."""
        hull = geom.convexHull()
        poly = hull.asPolygon()
        if not poly or not poly[0] or len(poly[0]) < 3:
            return self._bbox_params(geom)

        pts = poly[0]
        n = len(pts) - 1  # last point == first
        best_area = float('inf')
        best = None

        for i in range(n):
            dx = pts[(i + 1) % n].x() - pts[i].x()
            dy = pts[(i + 1) % n].y() - pts[i].y()
            edge = math.hypot(dx, dy)
            if edge < 1e-10:
                continue
            ux, uy = dx / edge, dy / edge   # along edge (candidate long axis)
            vx, vy = -uy, ux               # perpendicular

            u_vals = [ux * p.x() + uy * p.y() for p in pts[:n]]
            v_vals = [vx * p.x() + vy * p.y() for p in pts[:n]]
            u0, u1 = min(u_vals), max(u_vals)
            v0, v1 = min(v_vals), max(v_vals)
            rw, rh = u1 - u0, v1 - v0
            area = rw * rh

            if area < best_area:
                best_area = area
                uc, vc = (u0 + u1) / 2, (v0 + v1) / 2
                cx = ux * uc + vx * vc
                cy = uy * uc + vy * vc
                if rw >= rh:
                    best = (cx, cy, math.atan2(uy, ux), rw, rh)
                else:
                    best = (cx, cy, math.atan2(vy, vx), rh, rw)

        return best if best else self._bbox_params(geom)

    def _bbox_params(self, geom):
        bb = geom.boundingBox()
        cx = (bb.xMinimum() + bb.xMaximum()) / 2
        cy = (bb.yMinimum() + bb.yMaximum()) / 2
        w, h = bb.width(), bb.height()
        if w >= h:
            return cx, cy, 0.0, w, h
        return cx, cy, math.pi / 2, h, w

    def _subdivide_large(self, parcels, avg_ha, max_ha, min_ha):
        result = []
        for geom in parcels:
            area_ha = geom.area() / 10000
            if area_ha > max_ha:
                result.extend(self._subdivide(geom, avg_ha, max_ha, min_ha))
            else:
                # Also subdivide if aspect ratio is bad AND pieces would stay >= min_ha.
                # If not possible without going below min_ha, accept the exception.
                _, _, _, length, width = self._get_mbr_params(geom)
                ratio = (length / width) if width > 1 else 1.0
                if ratio > MAX_ASPECT_RATIO and area_ha / 2 >= min_ha:
                    result.extend(self._subdivide(geom, avg_ha, max_ha, min_ha))
                else:
                    result.append(geom)
        return result

    def _subdivide(self, geom, avg_ha, max_ha, min_ha, depth=0):
        """Split polygon into a 2D grid of roughly square cells aligned to the
        polygon's minimum bounding rectangle.  n_cols × n_rows is chosen so
        cells target avg_ha and are approximately square."""
        if depth > 10:
            return [geom]

        area_ha = geom.area() / 10000
        cx, cy, angle, length, width = self._get_mbr_params(geom)

        # Total cells needed to reach the average target
        n_total = max(2, round(area_ha / avg_ha))

        # Distribute into n_cols (along length) × n_rows (along width) for square cells.
        # Square cells require length/n_cols = width/n_rows → n_cols/n_rows = length/width.
        aspect = length / max(width, 0.01)
        n_cols = max(1, round(math.sqrt(n_total * aspect)))
        n_rows = max(1, round(n_total / n_cols))
        if n_cols * n_rows < 2:
            n_cols = 2

        # Cap so individual cells don't fall below min_ha
        if min_ha > 0:
            max_n = max(2, int(area_ha / min_ha))
            n_cols = min(n_cols, max_n)
            n_rows = min(n_rows, max_n)

        cos_a, sin_a = math.cos(angle), math.sin(angle)
        col_w = length / n_cols
        row_h = width / n_rows
        eps = 0.001  # lille tolerance, så celler ikke overlapper i praksis

        def make_pt(u, v):
            return QgsPointXY(cx + u * cos_a - v * sin_a,
                              cy + u * sin_a + v * cos_a)

        pieces = []
        for col in range(n_cols):
            u0 = -length / 2 + col * col_w - eps
            u1 = -length / 2 + (col + 1) * col_w + eps
            for row in range(n_rows):
                v0 = -width / 2 + row * row_h - eps
                v1 = -width / 2 + (row + 1) * row_h + eps
                cell = QgsGeometry.fromPolygonXY([[
                    make_pt(u0, v0), make_pt(u1, v0),
                    make_pt(u1, v1), make_pt(u0, v1),
                    make_pt(u0, v0),
                ]])
                clipped = geom.intersection(cell)
                if not clipped or clipped.isNull() or clipped.isEmpty():
                    continue
                for part in self._single_parts(clipped):
                    if part.area() > 1:
                        pieces.append(part)

        if not pieces:
            return [geom]

        # Always recurse on pieces still exceeding max_ha (no convexity guard —
        # that was suppressing the max limit for concave parcels)
        result = []
        for piece in pieces:
            if piece.area() / 10000 > max_ha:
                result.extend(self._subdivide(piece, avg_ha, max_ha, min_ha, depth + 1))
            else:
                result.append(piece)
        return result if result else [geom]

    def _merge_small(self, parcels, min_ha, stream_geom=None):
        """Iteratively merge parcels below min_ha with the neighbour sharing
        the longest common boundary.  A merge is only accepted when the result
        is a single connected polygon — this prevents disconnected MultiPolygons.
        Hvis stream_geom er angivet, prioriteres sammenfletning med naboen på
        SAMME side af vandløbet (krydsende sammenflettninger nedscore kraftigt)."""
        min_area = min_ha * 10000
        parcels = list(parcels)

        changed = True
        while changed:
            changed = False
            for i in range(len(parcels)):
                if parcels[i].area() >= min_area:
                    continue

                # Build scored candidate list (require a real shared edge, not just a point)
                candidates = []
                for j in range(len(parcels)):
                    if i == j:
                        continue
                    try:
                        if not parcels[i].intersects(parcels[j]):
                            continue
                        shared = parcels[i].intersection(parcels[j])
                        if shared is None or shared.isNull() or shared.isEmpty():
                            continue
                        score = shared.length() + shared.area()
                        if score <= 0.1:   # discard corner-only touches (score ≈ 0)
                            continue
                        # Nedscore kraftigt hvis fællesgrænsen løber langs et vandløb
                        if stream_geom is not None:
                            try:
                                overlap = shared.intersection(stream_geom)
                                if overlap and not overlap.isEmpty():
                                    frac = overlap.length() / max(shared.length(), 0.001)
                                    if frac > 0.3:
                                        score *= 0.001
                            except Exception:
                                pass
                        candidates.append((score, j))
                    except Exception:
                        continue

                # Try candidates best-first; accept only if merge stays connected
                for _, j in sorted(candidates, reverse=True):
                    merged = parcels[i].combine(parcels[j])
                    poly_parts = list(self._single_parts(merged))
                    if len(poly_parts) == 1:
                        parcels = [merged if k == j else p
                                   for k, p in enumerate(parcels) if k != i]
                        changed = True
                        break

                if changed:
                    break   # restart outer loop from beginning

        return parcels
