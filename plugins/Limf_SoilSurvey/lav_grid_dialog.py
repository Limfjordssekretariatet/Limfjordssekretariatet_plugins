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

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'lav_grid_dialog.ui'))

MARKKORT_PATH = os.path.join(os.path.dirname(__file__), 'Data', 'Markkort', 'Markkort_V3_snap.shp')
MAX_ASPECT_RATIO = 3.0   # default max length/width ratio before subdivision kicks in
SLIVER_WIDTH_M = 20.0     # polygoner smallere end dette (bredde) smeltes ind i nabo


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

        # Fase 1: Hent markkort-felter og split FØRST langs vandløb, så vandløbet
        # bliver en ren feltgrænse. DEREFTER opdeles hver vandløbs-afgrænsede brik
        # i celler → cellerne genereres så de PASSER til brikken og følger vandløbet,
        # i stedet for at vandløbet barberer tynde strimler af et færdigt grid (som
        # så enten bliver slivers eller – ved sammensmeltning – uregelmæssige arme).
        parcels = self._load_markkort_parcels(union_geom, work_crs)
        diag = [f'markkort={len(parcels)}']
        stream_layer, stream_geom, n_streams = self._load_streams(union_geom, work_crs)
        if stream_layer is not None:
            parcels = self._split_by_streams(parcels, stream_layer)
            diag.append(f'vandløbssplit={len(parcels)}')

        parcels = self._subdivide_large(parcels, avg_ha, max_ha, min_ha)
        if not parcels:
            QMessageBox.warning(self, 'Fejl', 'Ingen felter blev oprettet.')
            return

        # Fase 2: Byg REN topologisk dækning (polygonize → ingen overlap/needles).
        diag.append(f'celler={len(parcels)}')
        cleaned = self._polygonize_coverage(parcels)
        diag.append(f'dækning={len(cleaned)}')

        if not cleaned:
            QMessageBox.warning(self, 'Fejl', 'Ingen dækning kunne bygges.')
            return

        # Fase 2b: Adskil tynde haler/kanaler fra de tykke kerner (haletudse-former).
        # En tynd hale på et ellers stort polygon flagges ikke som sliver; ved at
        # skære den fra kan eliminate bagefter smelte den ind i naboen den løber
        # langs – i stedet for at den hænger på moderpolygonet.
        cleaned = self._split_thin_parts(cleaned, SLIVER_WIDTH_M)
        cleaned = self._polygonize_coverage(cleaned)
        diag.append(f'split-tynd={len(cleaned)}')

        # Fase 3: Fjern slivers/små felter TOPOLOGISK (eliminate dissolver hver ind
        # i naboen med længst fælles kant). UDEN region-spærring: en hale/korridor
        # langs et vandløb skal lægges sammen med den røde mark den løber langs
        # (ofte på den anden side af vandløbet), ikke tvinges ind i moderklumpen.
        n_thin_before = self._count_thin(cleaned, SLIVER_WIDTH_M, min_ha)
        cleaned = self._eliminate_slivers(cleaned, min_ha, SLIVER_WIDTH_M)
        n_thin_after = self._count_thin(cleaned, SLIVER_WIDTH_M, min_ha)
        diag.append(f'efter elim={len(cleaned)}')
        diag.append(f'tynde {n_thin_before}->{n_thin_after}')

        if not cleaned:
            QMessageBox.warning(self, 'Fejl', 'Ingen felter tilbage.')
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
            + '\n\n[diag] ' + '  '.join(diag)
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

        # (combined-geometri ikke længere nødvendig – stream-aware merge er fjernet)
        return single, None, n

    def _split_by_streams(self, parcels, stream_layer):
        """Split parceller langs vandløb via polygonize (robust, ingen slits/dangles).

        splitwithlines på polygoner laver 'slits' (nul-bredde needles) når en
        vandløbslinje ender INDE i et felt. I stedet bygges et nodet linjenet af
        grid-grænser + vandløb, som polygoniseres: kun lukkede områder bliver
        polygoner, så et vandløb der ikke krydser helt igennem deler ikke feltet
        og efterlader ingen dangle. Alle output deler eksakt nodede kanter."""
        parcel_layer = self._build_layer(parcels)
        grid_lines = processing.run('native:polygonstolines',
                                    {'INPUT': parcel_layer, 'OUTPUT': 'memory:'})['OUTPUT']
        # Densificér feltgrænserne FØRST: et langt lige segment har ingen vertices i
        # midten, så snapgeometries (der kun flytter eksisterende vertices) kan ikke
        # trække midten ind på det bugtede vandløb. Med vertices hver ~2 m kan hele
        # segmentet snappes, så den tynde korridor mellem feltkant og vandløb kollapser.
        for dalg in ('native:densifygeometriesgivenaninterval', 'qgis:densifygeometriesgivenaninterval'):
            try:
                grid_lines = processing.run(dalg, {
                    'INPUT': grid_lines, 'INTERVAL': 2.0, 'OUTPUT': 'memory:'
                })['OUTPUT']
                break
            except Exception:
                continue
        # Snap så feltgrænserne ind på vandløbet (kaldes på felt-niveau før opdeling,
        # så kun feltgrænser snappes – ikke celle-grænser).
        for alg in ('native:snapgeometries', 'qgis:snapgeometries'):
            try:
                grid_lines = processing.run(alg, {
                    'INPUT': grid_lines, 'REFERENCE_LAYER': stream_layer,
                    'TOLERANCE': SLIVER_WIDTH_M, 'BEHAVIOR': 1, 'OUTPUT': 'memory:'
                })['OUTPUT']
                break
            except Exception:
                continue
        merged = processing.run('native:mergevectorlayers', {
            'LAYERS': [grid_lines, stream_layer],
            'CRS': parcel_layer.crs(), 'OUTPUT': 'memory:'
        })['OUTPUT']
        # Nod linjenettet (split hver linje ved alle skæringer) før polygonisering
        noded = processing.run('native:splitwithlines', {
            'INPUT': merged, 'LINES': merged, 'OUTPUT': 'memory:'
        })['OUTPUT']
        polygons = processing.run('native:polygonize', {
            'INPUT': noded, 'KEEP_FIELDS': False, 'OUTPUT': 'memory:'
        })['OUTPUT']

        result = [QgsGeometry(f.geometry()) for f in polygons.getFeatures()
                  if not f.geometry().isEmpty() and f.geometry().area() >= 1]
        QgsMessageLog.logMessage(
            f'SoilSurvey: polygonize-split {len(parcels)} → {len(result)} parceller',
            'SoilSurvey')
        return result if result else parcels

    def _polygonize_coverage(self, parcels):
        """Byg en REN topologisk dækning (ingen overlap, snappede hjørner) ved at
        nodne polygonernes grænselinjer og polygonisere. Fjerner overlap/slip som
        de geometriske operationer (intersection osv.) kan have efterladt."""
        layer = self._build_layer(parcels)
        lines = processing.run('native:polygonstolines',
                               {'INPUT': layer, 'OUTPUT': 'memory:'})['OUTPUT']
        noded = processing.run('native:splitwithlines',
                               {'INPUT': lines, 'LINES': lines, 'OUTPUT': 'memory:'})['OUTPUT']
        polys = processing.run('native:polygonize',
                               {'INPUT': noded, 'KEEP_FIELDS': False, 'OUTPUT': 'memory:'})['OUTPUT']
        result = [QgsGeometry(f.geometry()) for f in polys.getFeatures()
                  if not f.geometry().isEmpty() and f.geometry().area() >= 1]
        return result if result else parcels

    def _split_thin_parts(self, parcels, min_width):
        """Skær tynde haler/kanaler fra de tykke kerner i hvert polygon.

        En miter-opening (erode min_width/2 → dilate tilbage) giver de tykke
        KERNER (åbning ⊆ original, så ingen overshoot). Resten = original − kerne
        er de tynde HALER. Begge lægges tilbage som separate polygoner; kerne ∪ hale
        = det oprindelige areal, så dækningen bevares (ingen huller). Bagefter kan
        eliminate smelte hver hale ind i naboen den deler længst kant med."""
        r = min_width / 2.0
        layer = self._build_layer(parcels)
        try:
            eroded = processing.run('native:buffer', {
                'INPUT': layer, 'DISTANCE': -r, 'SEGMENTS': 2,
                'JOIN_STYLE': 1, 'MITER_LIMIT': 10, 'DISSOLVE': False,
                'OUTPUT': 'memory:'})['OUTPUT']        # 1 = miter (skarpe hjørner)
            cores = processing.run('native:buffer', {
                'INPUT': eroded, 'DISTANCE': r, 'SEGMENTS': 2,
                'JOIN_STYLE': 1, 'MITER_LIMIT': 10, 'DISSOLVE': False,
                'OUTPUT': 'memory:'})['OUTPUT']
            thin = processing.run('native:difference', {
                'INPUT': layer, 'OVERLAY': cores, 'OUTPUT': 'memory:'})['OUTPUT']
        except Exception as e:
            QgsMessageLog.logMessage(f'SoilSurvey: split_thin_parts fejlede: {e}', 'SoilSurvey')
            return parcels

        n_cores = sum(1 for f in cores.getFeatures()
                      if not f.geometry().isEmpty() and f.geometry().area() >= 1)
        n_thin = sum(1 for f in thin.getFeatures()
                     if not f.geometry().isEmpty() and f.geometry().area() >= 1)
        result = []
        for lyr in (cores, thin):
            for f in lyr.getFeatures():
                g = f.geometry()
                if g and not g.isEmpty():
                    for part in self._single_parts(g):
                        if part.area() >= 1:
                            result.append(QgsGeometry(part))
        QgsMessageLog.logMessage(
            f'SoilSurvey: split_thin_parts {len(parcels)} → {n_cores} kerner '
            f'+ {n_thin} haler = {len(result)}', 'SoilSurvey')
        return result if result else parcels

    def _is_sliver(self, geom, min_area, min_width):
        """En polygon er en sliver hvis den er for lille (areal) ELLER for smal.

        Tyndhed måles ved EROSION: polygonen er tyndere end min_width overalt,
        hvis den forsvinder når den eroderes med min_width/2. Det fanger både
        lige OG buede strimler – men flagger IKKE en jagget-men-tyk celle (den
        beholder en kerne efter erosion). 2·A/P-målet gjorde netop det forkerte:
        det forvekslede jaggethed med tyndhed og overdetekterede slivers."""
        a = geom.area()
        if a < min_area:
            return True
        try:
            eroded = geom.buffer(-min_width / 2.0, 4)
            if eroded is None or eroded.isEmpty():
                return True
        except Exception:
            pass
        return False

    def _count_thin(self, parcels, min_width, min_ha):
        """Tæl slivers (for tynde eller for små) i en liste af geometrier."""
        min_area = min_ha * 10000
        return sum(1 for g in parcels if self._is_sliver(g, min_area, min_width))

    def _stream_regions(self, union_geom, stream_layer):
        """Del studieområdet i sammenhængende regioner adskilt af vandløbene.
        Bruges til at holde sliver-merge på SAMME side af vandløbet, så en
        strimmel aldrig smeltes på tværs (= det der skaber arme)."""
        layer = self._build_layer([QgsGeometry(union_geom)])
        lines = processing.run('native:polygonstolines',
                               {'INPUT': layer, 'OUTPUT': 'memory:'})['OUTPUT']
        merged = processing.run('native:mergevectorlayers', {
            'LAYERS': [lines, stream_layer], 'CRS': layer.crs(), 'OUTPUT': 'memory:'
        })['OUTPUT']
        noded = processing.run('native:splitwithlines',
                               {'INPUT': merged, 'LINES': merged, 'OUTPUT': 'memory:'})['OUTPUT']
        polys = processing.run('native:polygonize',
                               {'INPUT': noded, 'KEEP_FIELDS': False, 'OUTPUT': 'memory:'})['OUTPUT']
        regions = [QgsGeometry(f.geometry()) for f in polys.getFeatures()
                   if not f.geometry().isEmpty() and f.geometry().area() >= 1]
        QgsMessageLog.logMessage(
            f'SoilSurvey: {len(regions)} vandløbs-regioner', 'SoilSurvey')
        return regions

    def _eliminate_slivers(self, parcels, min_ha, min_width, regions=None):
        """Fjern slivers (op til min_width bred, eller under min areal) ved at
        smelte hver ind i naboen de deler STØRST fælles kant med
        (qgis:eliminateselectedpolygons MODE=2 – bevarer ren dækning, ingen needles).

        Hvis 'regions' (vandløbs-adskilte områder) er angivet, grupperes parcellerne
        per region og elimineres SEPARAT inden for hver – så en strimmel kun smeltes
        ind i en nabo på SAMME side af vandløbet (ellers bliver den til en arm)."""
        if regions and len(regions) > 1:
            groups = [[] for _ in regions]
            leftover = []
            for g in parcels:
                pt = g.pointOnSurface()
                ri = next((i for i, reg in enumerate(regions) if reg.contains(pt)), None)
                if ri is None:
                    leftover.append(g)
                else:
                    groups[ri].append(g)
            QgsMessageLog.logMessage(
                f'SoilSurvey: region-grupper {[len(x) for x in groups]} '
                f'leftover={len(leftover)}', 'SoilSurvey')
            result = []
            for grp in groups:
                if grp:
                    result.extend(self._eliminate_slivers(grp, min_ha, min_width))
            result.extend(leftover)
            return result

        layer = self._build_layer(parcels)
        layer = processing.run('native:fixgeometries',
                               {'INPUT': layer, 'OUTPUT': 'memory:'})['OUTPUT']
        min_area = min_ha * 10000
        prev_n = None
        for _ in range(12):
            ids = [f.id() for f in layer.getFeatures()
                   if not f.geometry().isEmpty()
                   and self._is_sliver(f.geometry(), min_area, min_width)]
            n, total = len(ids), layer.featureCount()
            QgsMessageLog.logMessage(
                f'SoilSurvey: eliminate-pass – {n}/{total} targets', 'SoilSurvey')
            if n == 0 or n >= total:
                # n>=total: alt er "tyndt" (fx ét enkelt jagget felt) – stop, ellers
                # forsvinder hele laget.
                break
            if prev_n is not None and n >= prev_n:
                # ingen fremgang (en sliver kan ikke elimineres – fx isoleret) – stop
                break
            prev_n = n
            layer.selectByIds(ids)
            try:
                layer = processing.run('qgis:eliminateselectedpolygons', {
                    'INPUT': layer, 'MODE': 2, 'OUTPUT': 'memory:'  # 2 = største fælles kant
                })['OUTPUT']
                layer = processing.run('native:fixgeometries',
                                       {'INPUT': layer, 'OUTPUT': 'memory:'})['OUTPUT']
            except Exception as e:
                QgsMessageLog.logMessage(f'SoilSurvey: eliminate fejlede: {e}', 'SoilSurvey')
                break
        return [QgsGeometry(f.geometry()) for f in layer.getFeatures()
                if not f.geometry().isEmpty() and f.geometry().area() >= 1]

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
        eps = 0.0  # celler skal flugte EKSAKT (overlap → bittesmå sliver-needles)

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

    # (_merge_small fjernet: combine()-baseret merge skabte overlap/usnappede
    #  hjørner/spikes. Sliver-fjernelse sker nu topologi-sikkert via
    #  _eliminate_slivers, og ren dækning sikres af _polygonize_coverage.)
