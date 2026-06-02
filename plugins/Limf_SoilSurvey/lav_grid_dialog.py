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
    QgsCoordinateTransformContext, QgsFeatureRequest, QgsMessageLog
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

        # Fase 1: Byg grid-polygoner (markkort + opsplit + sammenflet)
        parcels = self._load_markkort_parcels(union_geom, work_crs)
        parcels = self._subdivide_large(parcels, avg_ha, max_ha, min_ha)
        parcels = self._merge_small(parcels, min_ha)

        if not parcels:
            QMessageBox.warning(self, 'Fejl', 'Ingen felter blev oprettet.')
            return

        # Fase 2: Rens geometriske hairline-artefakter
        temp_layer = self._build_layer(parcels)
        temp_layer = processing.run("native:fixgeometries",
                                    {"INPUT": temp_layer, "OUTPUT": "memory:"})["OUTPUT"]
        try:
            temp_layer = clean_layer(temp_layer, d=15.0, min_area=1,
                                     grid_size=0.001, max_iters=3, despike=0.1)
        except Exception:
            pass

        # Fase 3: Re-normaliser størrelser efter rensning
        cleaned = [QgsGeometry(f.geometry()) for f in temp_layer.getFeatures()
                   if not f.geometry().isEmpty() and f.geometry().area() >= 1]
        cleaned = self._merge_small(cleaned, min_ha)
        cleaned = self._subdivide_large(cleaned, avg_ha, max_ha, min_ha)

        if not cleaned:
            QMessageBox.warning(self, 'Fejl', 'Ingen felter efter re-normalisering.')
            return

        # Fase 4: Split langs vandløb (altid sidst, på det færdige grid)
        stream_layer, stream_geom, n_streams = self._load_streams(union_geom, work_crs)
        if stream_layer is not None:
            cleaned = self._split_by_streams(cleaned, stream_layer)

            # Fase 5: Snap tynde strimler (< 20 m brede) til vandløbet ved at
            # smelte dem med nabo på SAMME side. Derefter opdel for-store stykker.
            SNAP_M = 20.0
            cleaned = self._merge_small(cleaned, min_ha,
                                        stream_geom=stream_geom, min_width=SNAP_M)
            cleaned = self._subdivide_large(cleaned, avg_ha, max_ha, min_ha)

            # Fase 6: Vandløbet er absolut endelig grænse – re-split + afsluttende snap
            cleaned = self._split_by_streams(cleaned, stream_layer)
            cleaned = self._merge_small(cleaned, min_ha,
                                        stream_geom=stream_geom, min_width=SNAP_M)

            # Fase 7: Fjern slivers op til 10 m.
            # clean_layer identificerer slivers via opening (buffer) men smelter dem
            # via eliminateselectedpolygons – ingen afrundede kanter, ingen huller.
            tmp = self._build_layer(cleaned)
            tmp = processing.run("native:fixgeometries",
                                 {"INPUT": tmp, "OUTPUT": "memory:"})["OUTPUT"]
            try:
                tmp = clean_layer(tmp, d=10.0, min_area=1,
                                  grid_size=0.001, max_iters=3, despike=0.1)
            except Exception:
                pass
            cleaned = [QgsGeometry(f.geometry()) for f in tmp.getFeatures()
                       if not f.geometry().isEmpty() and f.geometry().area() >= 1]

            # Fase 8: Re-håndhæv vandløb efter sliver-rensning
            cleaned = self._split_by_streams(cleaned, stream_layer)
            cleaned = self._merge_small(cleaned, min_ha,
                                        stream_geom=stream_geom, min_width=SNAP_M)

        # Fase 9: Byg endeligt lag
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
        """Load alle .shp-filer fra Data/Streams som et klar-til-brug QgsVectorLayer
        (reprojectet + single-part LineStrings). Returnerer (layer, combined_geom, n)."""
        streams_dir = os.path.join(os.path.dirname(__file__), 'Data', 'Streams')
        if not os.path.isdir(streams_dir):
            QgsMessageLog.logMessage(f'SoilSurvey: streams-mappe ikke fundet: {streams_dir}', 'SoilSurvey')
            return None, None, 0

        layers_to_merge = []
        for fname in sorted(os.listdir(streams_dir)):
            if not fname.lower().endswith('.shp'):
                continue
            path = os.path.join(streams_dir, fname)
            lyr = QgsVectorLayer(path, 'stream', 'ogr')
            if not lyr.isValid():
                QgsMessageLog.logMessage(f'SoilSurvey: kan ikke åbne {fname}', 'SoilSurvey')
                continue
            # Reproject til work_crs hvis nødvendigt
            if lyr.crs() != work_crs:
                lyr = processing.run('native:reprojectlayer', {
                    'INPUT': lyr, 'TARGET_CRS': work_crs, 'OUTPUT': 'memory:'
                })['OUTPUT']
            layers_to_merge.append(lyr)

        if not layers_to_merge:
            QgsMessageLog.logMessage('SoilSurvey: ingen gyldige stream-lag fundet', 'SoilSurvey')
            return None, None, 0

        # Sammenflet til ét lag og klip til studieområdets bbox
        merged = processing.run('native:mergevectorlayers', {
            'LAYERS': layers_to_merge, 'CRS': work_crs, 'OUTPUT': 'memory:'
        })['OUTPUT']
        clipped = processing.run('native:extractbylocation', {
            'INPUT': merged,
            'PREDICATE': [0],  # intersects
            'INTERSECT': self._build_layer([union_geom]),
            'OUTPUT': 'memory:'
        })['OUTPUT']
        # Eksploder multipart → single LineStrings (undgår type-mismatch i splitwithlines)
        single = processing.run('native:multiparttosingleparts', {
            'INPUT': clipped, 'OUTPUT': 'memory:'
        })['OUTPUT']

        n = single.featureCount()
        QgsMessageLog.logMessage(f'SoilSurvey: {n} vandløbs-LineStrings klar til split', 'SoilSurvey')
        if n == 0:
            return None, None, 0

        # Kombineret geometri til _merge_small intersection-test
        combined = None
        for feat in single.getFeatures():
            g = feat.geometry()
            if g and not g.isEmpty():
                combined = QgsGeometry(g) if combined is None else combined.combine(g)

        return single, combined, n

    def _split_by_streams(self, parcels, stream_layer):
        """Split parceller langs vandløb med native:splitwithlines.
        stream_layer er et klar-til-brug QgsVectorLayer med single-part LineStrings."""
        parcel_layer = self._build_layer(parcels)
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

    def _despike(self, parcels, despike_m=1.0):
        """Fjern dangles/løse strimler via close+open buffer-sekvens.
        Svarer til Sliver.py's despike-trin: (+d, -d, -d, +d)."""
        layer = self._build_layer(parcels)
        for dist in (despike_m, -despike_m, -despike_m, despike_m):
            layer = processing.run('native:buffer', {
                'INPUT': layer, 'DISTANCE': dist,
                'JOIN_STYLE': 0, 'SEGMENTS': 5, 'OUTPUT': 'memory:'
            })['OUTPUT']
        layer = processing.run('native:fixgeometries',
                               {'INPUT': layer, 'OUTPUT': 'memory:'})['OUTPUT']
        result = [QgsGeometry(f.geometry()) for f in layer.getFeatures()
                  if not f.geometry().isEmpty() and f.geometry().area() >= 1]
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

    def _merge_small(self, parcels, min_ha, stream_geom=None, min_width=0.0):
        """Iteratively merge parcels that are too small OR too thin with the
        neighbour sharing the longest common boundary.
        min_width (m): merge polygoner hvis estimeret bredde (4·A/P) < 2·min_width.
        Hvis stream_geom er angivet, prioriteres sammenfletning med naboen på
        SAMME side af vandløbet (krydsende sammenflettninger nedscore kraftigt)."""
        min_area = min_ha * 10000
        width_threshold = 2.0 * min_width  # 4·A/P ≈ 2·min_dim for rektangel
        parcels = list(parcels)

        changed = True
        while changed:
            changed = False
            for i in range(len(parcels)):
                area = parcels[i].area()
                too_small = area < min_area
                too_thin = False
                if min_width > 0 and not too_small:
                    perim = parcels[i].length()
                    if perim > 0:
                        too_thin = (4.0 * area / perim) < width_threshold
                if not (too_small or too_thin):
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
