"""Lav grid – v3.0.0 (markskel-først tilgang).

Denne version vender logikken om ift. v2.x: i stedet for at lægge et kunstigt
grid over alt og bagefter rydde slivers op, RESPEKTERER vi markerne som de er og
laver kun kunstig opdeling hvor en mark ikke allerede passer til målstørrelsen.

Proces (jf. brugerens beslutningstræ):
  1. Hent marker fra markkortet. Streams + roades er allerede klippet ind som
     markgrænser i forbehandlingen, så vi bruger markerne direkte (ingen
     barrier-skæring ved kørsel).
  1b. Fjern nålespidser (spikes) vertex-baseret: drop vertices der danner en
     tynd ud-og-tilbage-tunge. Bevarer ægte hjørner skarpe (ingen buffer →
     ingen runde hjørner). Hele tynde slivers smeltes ind i nabo til sidst.
  Klassificér hver mark efter areal vs. gennemsnit (±20% = passer):
  2. Marker der passer direkte → grid som de er.
  4-5. For SMÅ marker (under avg·0.8): lad hver vokse ved at sluge naboer (så
       mange som nødvendigt) indtil arealet rammer ±20%. Greedy via ægte nabo-
       graf; delvis smeltning beholdes hvis målet ikke kan rammes præcist.
       Isolerede små marker (uden lille nabo) smeltes til sidst ind i deres
       mindste nabo, så de ikke trækker gennemsnittet ned.
  3. For STORE marker (over avg·1.2): del i 2/3/4/5 lige dele hvis hver del
     rammer ±20% og holder min/max.
  6. Store marker der ikke kan deles pænt: kunstig grid-opdeling (MBR-celler)
     målrettet avg, holdt inden for min/max.
  7. Marker der bryder de HÅRDE grænser (under min eller over max) → markeres
     'rest' og tegnes RØDT.
  8. Popup-rapport med antal pr. trin.

Fallback: den gamle pipeline ligger urørt i lav_grid_dialog_old.py.
"""

import math
import os
import shutil
import tempfile
import urllib.request
import zipfile
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QMessageBox, QApplication, QProgressDialog
from qgis.PyQt.QtCore import Qt
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsField, QgsPointXY, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCoordinateTransformContext, QgsFeatureRequest, QgsMessageLog,
    QgsSpatialIndex, QgsFillSymbol,
    QgsRuleBasedRenderer, QgsApplication,
)

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'lav_grid_dialog.ui'))

# ------------------------------------------------------------------ #
#  Markkort-data: downloades fra en GitHub release og caches lokalt. #
# ------------------------------------------------------------------ #
# Markkortet (landsdækkende, ~175 MB) er for stort til at distribuere i selve
# plugin-zippen (GitHub Pages-grænse 100 MB). Det ligger i stedet som et
# release-asset og hentes første gang værktøjet bruges. Streams + roades er
# allerede klippet ind som markgrænser i forbehandlingen, så markerne bruges
# direkte (ingen barrier-skæring ved kørsel).
MARKKORT_VERSION = 'markkort-v1'   # = release-tag; bump for at tvinge ny download
MARKKORT_ZIP_NAME = 'Markkort_grid.zip'
MARKKORT_SHP_NAME = 'Markkort_grid.shp'
MARKKORT_URL = (
    'https://github.com/Limfjordssekretariatet/Limfjordssekretariatet_plugins'
    f'/releases/download/{MARKKORT_VERSION}/{MARKKORT_ZIP_NAME}'
)


def _markkort_cache_dir():
    """Cache i QGIS-profilens mappe, så den overlever plugin-opdateringer
    (downloades kun igen når MARKKORT_VERSION bumpes)."""
    base = os.path.join(QgsApplication.qgisSettingsDirPath(),
                        'Limf_SoilSurvey', MARKKORT_VERSION)
    return base


MARKKORT_PATH = os.path.join(_markkort_cache_dir(), MARKKORT_SHP_NAME)

WORK_CRS = 'EPSG:25832'
TOL_FRAC = 0.20          # ±20% af gennemsnit = "passer"
MAX_SPLIT_PARTS = 5      # del en stor mark i op til 5 lige dele (trin 3)
MIN_SHARED_EDGE_M = 1.0  # to marker er naboer hvis de deler mindst så lang en kant
THIN_WIDTH_M = 20.0      # hele polygoner smallere end dette smeltes ind i nabo (sliver)
SPIKE_ANGLE_DEG = 8.0    # vertex med spidsere vinkel end dette = nålespids → fjernes


def log(msg):
    QgsMessageLog.logMessage(f'SoilSurvey/grid: {msg}', 'SoilSurvey')


class Parcel:
    """En arbejds-mark under grid-processen."""
    __slots__ = ('geom', 'status')

    def __init__(self, geom, status='ny'):
        self.geom = geom
        self.status = status   # 'ok' | 'delt' | 'smeltet' | 'kunstig' | 'rest'

    @property
    def area_ha(self):
        return self.geom.area() / 10000.0


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

    # ------------------------------------------------------------------ #
    #  Hovedflow                                                          #
    # ------------------------------------------------------------------ #
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

        # Sørg for at markkortet er hentet (downloades fra release ved første brug).
        if not self._ensure_markkort():
            return

        work_crs = QgsCoordinateReferenceSystem(WORK_CRS)
        union_geom = self._union_of_layer(grense_layer, work_crs)
        if union_geom is None:
            QMessageBox.warning(self, 'Fejl', 'Laget indeholder ingen geometri.')
            return

        lo, hi = avg_ha * (1 - TOL_FRAC), avg_ha * (1 + TOL_FRAC)
        counts = {'ok': 0, 'delt': 0, 'smeltet': 0, 'kunstig': 0, 'rest': 0}

        # ---- Trin 1: hent marker fra markkortet ----
        # Markkortet er forbehandlet, så streams + roades allerede er klippet ind
        # som markgrænser. Vi bruger derfor markerne direkte – ingen barrier-
        # indlæsning eller -skæring (det skete i preprocessing).
        parcels = self._load_markkort_parcels(union_geom, work_crs)
        log(f'trin1: {len(parcels)} marker fra markkort')

        # ---- Trin 1b: fjern spids-vinkel-spikes (vertex-baseret) ----
        # Markkortet har tynde spyd hvor markgrænser mødes i en meget spids
        # vinkel. De fjernes ved at droppe netop de vertices der danner en
        # nålespids – ALLE andre hjørner bevares skarpe (ingen buffer → ingen
        # runde hjørner).
        parcels = [self._despike(g) for g in parcels]
        parcels = [g for g in parcels if g and not g.isEmpty()]

        parcels = [Parcel(g) for g in parcels]

        # ---- Klassificér + trin 2 (passer direkte) ----
        small, large, ok = [], [], []
        for p in parcels:
            a = p.area_ha
            if lo <= a <= hi:
                p.status = 'ok'
                ok.append(p)
            elif a < lo:
                small.append(p)
            else:
                large.append(p)
        log(f'klassif: ok={len(ok)} små={len(small)} store={len(large)}')

        result = list(ok)

        # ---- Trin 4-5: smelt små marker indbyrdes (små først) ----
        merged, leftover_small = self._merge_small(small, avg_ha, lo, hi, min_ha)
        result.extend(merged)

        # ---- Trin 3 + 6: del store marker (store først) ----
        for p in large:
            result.extend(self._split_large(p, avg_ha, lo, hi, min_ha, max_ha))

        # ---- Oprydning: isolerede små marker (ingen lille nabo) smeltes ind i
        # deres mindste nabo i det færdige resultat, så de ikke trækker
        # gennemsnittet ned. Resten lægges tilbage som de er.
        result = self._absorb_leftovers(leftover_small, result)

        # ---- Slut-oprydning A: fjern spids-vinkel-spikes på de færdige celler.
        # Subdivision/smeltning kan have skabt nye nålespidser; despike fjerner
        # dem vertex-baseret uden at runde hjørner.
        result = [p for p in (self._despike_parcel(p) for p in result) if p]

        # ---- Slut-oprydning B: fjern tynde slivers (hele tynde polygoner) fra
        # det FÆRDIGE grid. Hver smeltes ind i naboen med størst kontakt (buffer-
        # baseret, robust mod non-noded geometri). Umulige beholdes som røde.
        result = self._absorb_slivers(result, THIN_WIDTH_M, min_ha)

        # ---- Trin 7: markér røde (bryder hårde grænser) ----
        for p in result:
            a = p.area_ha
            if a < min_ha or a > max_ha:
                p.status = 'rest'

        for p in result:
            counts[p.status] = counts.get(p.status, 0) + 1

        if not result:
            QMessageBox.warning(self, 'Fejl', 'Ingen felter blev oprettet.')
            return

        # ---- Trin 4 (lag) + 8 (rapport) ----
        grid_layer = self._build_output_layer(result)
        self._apply_renderer(grid_layer)
        QgsProject.instance().addMapLayer(grid_layer)

        total_ha = sum(p.area_ha for p in result)
        avg_result = total_ha / len(result)
        QMessageBox.information(
            self, 'Grid oprettet',
            f'Grid oprettet med {len(result)} felter.\n'
            f'Gennemsnit: {avg_result:.2f} ha (mål: {avg_ha:.1f} ha)\n'
            f'Total areal: {total_ha:.1f} ha\n\n'
            f'Trin 2 – passede direkte:   {counts["ok"]}\n'
            f'Trin 3 – delt (store):      {counts["delt"]}\n'
            f'Trin 4-5 – smeltet (små):   {counts["smeltet"]}\n'
            f'Trin 6 – kunstig opdeling:  {counts["kunstig"]}\n'
            f'Trin 7 – rest (RØD):        {counts["rest"]}'
        )
        self.accept()

    # ------------------------------------------------------------------ #
    #  Markkort-download (release-asset, cachet i QGIS-profil)            #
    # ------------------------------------------------------------------ #
    def _ensure_markkort(self):
        """Sørg for at markkort-shapefilen findes i cachen. Hentes fra GitHub
        release og pakkes ud første gang (eller når MARKKORT_VERSION er bumpet).
        Returnerer True hvis filen er klar, ellers False (og viser fejl)."""
        if os.path.exists(MARKKORT_PATH):
            return True

        cache_dir = _markkort_cache_dir()
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle('Hent markkort')
        msg.setText(
            'Markkortet skal hentes første gang (ca. 100 MB).\n'
            'Det gemmes lokalt, så det kun hentes én gang.\n\nHent nu?')
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if msg.exec_() != QMessageBox.Yes:
            return False

        progress = QProgressDialog('Henter markkort…', 'Annullér', 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        QApplication.processEvents()

        tmp_zip = None
        try:
            os.makedirs(cache_dir, exist_ok=True)
            fd, tmp_zip = tempfile.mkstemp(suffix='.zip')
            os.close(fd)

            def _hook(block_num, block_size, total_size):
                if progress.wasCanceled():
                    raise InterruptedError('Download annulleret')
                if total_size > 0:
                    pct = min(100, int(block_num * block_size * 100 / total_size))
                    progress.setValue(pct)
                    QApplication.processEvents()

            urllib.request.urlretrieve(MARKKORT_URL, tmp_zip, _hook)

            progress.setLabelText('Pakker markkort ud…')
            progress.setValue(100)
            QApplication.processEvents()
            with zipfile.ZipFile(tmp_zip) as zf:
                zf.extractall(cache_dir)

            progress.close()
            if not os.path.exists(MARKKORT_PATH):
                QMessageBox.warning(
                    self, 'Fejl',
                    'Markkortet blev hentet, men forventede fil mangler:\n'
                    f'{MARKKORT_SHP_NAME}')
                return False
            log(f'markkort hentet og udpakket til {cache_dir}')
            return True

        except InterruptedError:
            progress.close()
            shutil.rmtree(cache_dir, ignore_errors=True)
            return False
        except Exception as e:
            progress.close()
            shutil.rmtree(cache_dir, ignore_errors=True)
            QMessageBox.critical(
                self, 'Fejl ved download',
                f'Kunne ikke hente markkortet:\n{e}\n\nURL:\n{MARKKORT_URL}')
            return False
        finally:
            if tmp_zip and os.path.exists(tmp_zip):
                try:
                    os.remove(tmp_zip)
                except OSError:
                    pass

    # ------------------------------------------------------------------ #
    #  Trin 1: indlæs marker                                              #
    # ------------------------------------------------------------------ #
    def _union_of_layer(self, layer, work_crs):
        src_crs = layer.crs()
        to_work = QgsCoordinateTransform(src_crs, work_crs, QgsCoordinateTransformContext())
        need = src_crs != work_crs
        u = None
        for feat in layer.getFeatures():
            g = feat.geometry()
            if not g or g.isNull() or g.isEmpty():
                continue
            if need:
                g = QgsGeometry(g)
                g.transform(to_work)
            u = QgsGeometry(g) if u is None else u.combine(g)
        if u is None or u.isNull() or u.isEmpty():
            return None
        return u

    # ------------------------------------------------------------------ #
    #  Spike-fjernelse (vertex-baseret – runder IKKE hjørner)            #
    # ------------------------------------------------------------------ #
    def _despike(self, geom):
        """Fjern nålespidser (spikes) fra en polygon uden at runde hjørner.

        En spike er en vertex B (mellem A og C) hvor de to kanter går næsten ud
        og direkte tilbage – dvs. vinklen ABC er meget spids (< SPIKE_ANGLE_DEG)
        OG den smalleste "bredde" af spidsen er lille: nabopunktet C (eller A)
        ligger tæt på den modsatte kant. Det adskiller en tynd ud-og-tilbage-
        tunge fra et ægte, bredt spidst hjørne (hvor A og C er vidt forskellige
        retninger og hele formen er bred). Alle ægte hjørner bevares skarpe."""
        if geom is None or geom.isNull() or geom.isEmpty():
            return geom
        out_parts = []
        changed = False
        for part in self._single_parts(geom):
            poly = part.asPolygon()
            if not poly:
                out_parts.append(part)
                continue
            new_rings = []
            for ring in poly:
                nr, ch = self._despike_ring(ring)
                new_rings.append(nr)
                changed = changed or ch
            if new_rings and len(new_rings[0]) >= 4:
                ng = QgsGeometry.fromPolygonXY(new_rings)
                if ng and not ng.isEmpty():
                    ng = self._cleanup(ng)
                    out_parts.append(ng)
                else:
                    out_parts.append(part)
            else:
                out_parts.append(part)
        if not out_parts:
            return geom
        result = out_parts[0]
        for p in out_parts[1:]:
            result = result.combine(p)
        return result if changed else geom

    def _despike_ring(self, ring):
        """Returnér (renset_ring, ændret?). Fjerner spids-vinkel-vertices
        iterativt indtil ingen tilbage. ring er en lukket liste af QgsPointXY."""
        if len(ring) < 4:
            return ring, False
        pts = ring[:-1] if ring[0] == ring[-1] else list(ring)
        changed = False
        i = 0
        guard = 0
        while len(pts) > 3 and guard < len(pts) * 4:
            guard += 1
            n = len(pts)
            a = pts[(i - 1) % n]
            b = pts[i % n]
            c = pts[(i + 1) % n]
            # spike = spids vinkel ved B OG spidsen er tynd: afstanden fra det
            # nærmeste nabopunkt til den modsatte kant er lille (en nålespids,
            # ikke et bredt spidst hjørne).
            if (self._spike_angle(a, b, c) < SPIKE_ANGLE_DEG
                    and self._spike_width(a, b, c) < THIN_WIDTH_M):
                del pts[i % n]
                changed = True
                # bliv på samme indeks (nu peger det på næste punkt)
                if i >= len(pts):
                    i = 0
            else:
                i += 1
                if i >= len(pts):
                    break
        pts.append(pts[0])   # luk ringen igen
        return pts, changed

    def _spike_angle(self, a, b, c):
        """Vinkel ABC i grader (0-180). Lille vinkel = spids nålespids."""
        v1x, v1y = a.x() - b.x(), a.y() - b.y()
        v2x, v2y = c.x() - b.x(), c.y() - b.y()
        n1 = math.hypot(v1x, v1y)
        n2 = math.hypot(v2x, v2y)
        if n1 < 1e-9 or n2 < 1e-9:
            return 180.0
        cosang = (v1x * v2x + v1y * v2y) / (n1 * n2)
        cosang = max(-1.0, min(1.0, cosang))
        return math.degrees(math.acos(cosang))

    def _spike_width(self, a, b, c):
        """Tyndhedsmål for spidsen ved B: afstanden fra det nærmeste nabopunkt
        (A eller C) vinkelret ind på den modsatte kant.

        For en nålespids (lang tynd tunge) er denne afstand lille uanset hvor
        lang tungen er. For et bredt spidst hjørne er den stor. Det adskiller en
        spike fra et ægte skarpt hjørne."""
        # afstand fra A til linjen B–C, og fra C til linjen B–A; tag den mindste
        d1 = self._point_seg_dist(a, b, c)
        d2 = self._point_seg_dist(c, b, a)
        return min(d1, d2)

    def _point_seg_dist(self, p, s1, s2):
        """Vinkelret afstand fra punkt p til den uendelige linje gennem s1–s2."""
        dx, dy = s2.x() - s1.x(), s2.y() - s1.y()
        seg = math.hypot(dx, dy)
        if seg < 1e-9:
            return math.hypot(p.x() - s1.x(), p.y() - s1.y())
        # |kryds-produkt| / |segment|
        cross = abs((p.x() - s1.x()) * dy - (p.y() - s1.y()) * dx)
        return cross / seg

    def _despike_parcel(self, parcel):
        """Despike en Parcel in-place; returnér den (eller None hvis tom)."""
        g = self._despike(parcel.geom)
        if g is None or g.isEmpty() or g.area() < 1:
            return None
        parcel.geom = g
        return parcel

    def _is_thin(self, geom, min_width):
        """True hvis polygonen er tyndere end min_width OVERALT (forsvinder ved
        erosion med min_width/2). Bruges KUN til detektion (læser geometrien,
        ændrer den ikke), så ingen runde-hjørne-bivirkning. Ren QgsGeometry."""
        try:
            eroded = geom.buffer(-min_width / 2.0, 4)
            return eroded is None or eroded.isEmpty()
        except Exception:
            return False

    def _absorb_slivers(self, parcels, min_width, min_ha):
        """Smelt tynde slivers ind i naboen med STØRST kontakt (buffer-baseret).

        En sliver = en polygon der er tynd overalt (erosion-test) ELLER under
        min areal. Kontakt måles ved at buffere sliveren en smule og se hvor
        meget af hver nabo den overlapper – robust mod non-noded geometri hvor
        eksakt delt-kant-længde er upålidelig. Sliveren smeltes ind i naboen med
        størst overlap. Slivers uden brugbar nabo beholdes (markeres rød i trin 7)."""
        if not parcels:
            return parcels
        min_area = min_ha * 10000

        # find sliver-indekser
        sliver_ids = [i for i, p in enumerate(parcels)
                      if self._is_thin(p.geom, min_width) or p.geom.area() < min_area]
        if not sliver_ids:
            return parcels
        sliver_set = set(sliver_ids)

        index = QgsSpatialIndex()
        for i, p in enumerate(parcels):
            f = QgsFeature(i)
            f.setGeometry(p.geom)
            index.addFeature(f)

        absorbed = set()    # slivers smeltet væk
        n_abs = 0
        # mindste først, så en lille sliver ikke æder en anden
        for i in sorted(sliver_ids, key=lambda k: parcels[k].geom.area()):
            if i in absorbed:
                continue
            sliver = parcels[i].geom
            probe = sliver.buffer(1.0, 4)   # lille bufferzone til kontaktmåling
            bbox = probe.boundingBox()
            best_j, best_overlap = None, 0.0
            for cid in index.intersects(bbox):
                if cid == i or cid in absorbed or cid in sliver_set:
                    continue   # smelt kun ind i en RIGTIG (ikke-sliver) nabo
                other = parcels[cid].geom
                if not probe.intersects(other):
                    continue
                inter = probe.intersection(other)
                if inter is None or inter.isEmpty():
                    continue
                ov = inter.area()
                if ov > best_overlap:
                    best_j, best_overlap = cid, ov
            if best_j is not None and best_overlap > 0:
                host = parcels[best_j]
                host.geom = self._cleanup(host.geom.combine(sliver))
                if host.status == 'ok':
                    host.status = 'smeltet'
                absorbed.add(i)
                n_abs += 1

        log(f'slivers: {len(sliver_ids)} fundet → {n_abs} absorberet, '
            f'{len(sliver_ids) - n_abs} tilbage (rød)')
        return [p for k, p in enumerate(parcels) if k not in absorbed]

    # ------------------------------------------------------------------ #
    #  Trin 4-5: smelt små marker (nabo-graf, greedy)                     #
    # ------------------------------------------------------------------ #
    def _merge_small(self, smalls, avg_ha, lo, hi, min_ha):
        """Smelt små marker sammen så de rammer målstørrelsen (±20%).

        Greedy: tag den mindste ubehandlede lille mark og lad den vokse ved at
        sluge naboer (delt kant), så mange som nødvendigt, indtil arealet er i
        [lo, hi]. Gentag. Naboer findes via ægte nabo-graf. Se _grow_region for
        detaljer om delvis smeltning når målet ikke kan rammes præcist."""
        if not smalls:
            return [], []

        # Arbejd på ALLE små som node-objekter; naboskab beregnes mod hele settet.
        nodes = [Parcel(QgsGeometry(p.geom)) for p in smalls]
        active = list(range(len(nodes)))
        index = QgsSpatialIndex()
        for i, p in enumerate(nodes):
            f = QgsFeature(i)
            f.setGeometry(p.geom)
            index.addFeature(f)

        consumed = set()
        merged_out = []

        # mindste først
        order = sorted(active, key=lambda i: nodes[i].area_ha)
        for i in order:
            if i in consumed:
                continue
            base = nodes[i]
            if base.area_ha >= lo:   # er allerede blevet stor nok (efter tidligere flet)
                continue
            geom, chosen = self._grow_region(i, nodes, index, consumed, avg_ha, lo, hi)
            if not chosen:
                continue   # ingen naboer overhovedet – lades til leftover
            for j in chosen:
                consumed.add(j)
            consumed.add(i)
            base.geom = self._cleanup(geom)
            base.status = 'smeltet'
            merged_out.append(base)

        leftover = []
        for i in active:
            if i in consumed:
                continue
            p = nodes[i]
            # lille mark der ikke kunne smeltes: accepteres som normal hvis >= min,
            # ellers falder den igennem til trin 7 (rød) via areal-tjek senere.
            p.status = 'ok' if p.area_ha >= min_ha else 'rest'
            leftover.append(p)

        log(f'trin4-5: {len(smalls)} små → {len(merged_out)} smeltede + {len(leftover)} rest/små')
        return merged_out, leftover

    def _grow_region(self, i, nodes, index, consumed, avg_ha, lo, hi):
        """Lad en lille mark vokse ved at sluge naboer (delt kant) indtil arealet
        rammer [lo, hi] – eller indtil ingen naboer er tilbage. Returnerer
        (samlet_geom, liste_af_slugte_naboer).

        Tilføjer i hvert skridt den nabo der bringer summen tættest på avg.
        Til forskel fra den gamle 'alt-eller-intet': der er INGEN øvre grænse på
        antal naboer (en mark der er 1/7 af avg skal kunne sluge 6 naboer), og en
        DELVIS smeltning beholdes hvis vi ikke kan ramme [lo,hi] præcist – det er
        bedre at efterlade én mark på 1,4 ha end syv marker på 0,2 ha (som ellers
        trækker gennemsnittet langt under mål)."""
        used = set(consumed)
        used.add(i)
        chosen = []
        cur_sum = nodes[i].area_ha
        cur_geom = QgsGeometry(nodes[i].geom)

        # vokser indtil vi rammer intervallet eller løber tør for naboer
        for _ in range(len(nodes)):
            if lo <= cur_sum <= hi:
                break
            cands = self._neighbors(cur_geom, index, nodes, used)
            if not cands:
                break
            # vælg den nabo der bringer summen tættest på avg uden at skyde over hi
            best_j, best_diff = None, None
            for j in cands:
                s = cur_sum + nodes[j].area_ha
                # straf overskridelse af hi hårdere, så vi helst lander i intervallet
                diff = abs(s - avg_ha) + (max(0.0, s - hi) * 10.0)
                if best_diff is None or diff < best_diff:
                    best_j, best_diff = j, diff
            if best_j is None:
                break
            new_sum = cur_sum + nodes[best_j].area_ha
            # hvis vi allerede er i intervallet og næste nabo ville skyde forbi hi,
            # så stop – vi har en god gruppe
            if cur_sum >= lo and new_sum > hi:
                break
            chosen.append(best_j)
            used.add(best_j)
            cur_sum = new_sum
            cur_geom = cur_geom.combine(nodes[best_j].geom)

        return cur_geom, chosen

    def _neighbors(self, geom, index, nodes, used):
        """Indekser på node der deler en reel kant (≥ MIN_SHARED_EDGE_M) med geom."""
        out = []
        bbox = geom.boundingBox()
        bbox.grow(1.0)
        for cid in index.intersects(bbox):
            if cid in used:
                continue
            other = nodes[cid].geom
            if not geom.intersects(other):
                continue
            inter = geom.intersection(other)
            if inter is None or inter.isEmpty():
                continue
            # delt kant = linje-længde af snittet (ignorér ren punkt-berøring)
            if inter.length() >= MIN_SHARED_EDGE_M:
                out.append(cid)
        return out

    def _absorb_leftovers(self, leftovers, result):
        """Smelt hver isoleret lille mark (uden lille nabo) ind i sin MINDSTE
        nabo blandt result-markerne, så den ikke trækker gennemsnittet ned.

        Naboen vælges som den mindste der deler en kant – så den vokser mindst
        muligt. En leftover uden NOGEN nabo i result lægges tilbage som den er
        (kan ikke smeltes; håndteres af min/max-tjek i trin 7)."""
        if not leftovers:
            return result

        # Spatial index over result-markerne (modtagerne)
        index = QgsSpatialIndex()
        for i, p in enumerate(result):
            f = QgsFeature(i)
            f.setGeometry(p.geom)
            index.addFeature(f)

        n_absorbed = 0
        unmerged = []
        for lp in leftovers:
            cands = self._neighbors(lp.geom, index, result, set())
            if not cands:
                unmerged.append(lp)
                continue
            # mindste nabo (vokser mindst)
            j = min(cands, key=lambda k: result[k].area_ha)
            host = result[j]
            host.geom = self._cleanup(host.geom.combine(lp.geom))
            if host.status == 'ok':
                host.status = 'smeltet'
            n_absorbed += 1

        log(f'absorber: {len(leftovers)} isolerede små → {n_absorbed} smeltet '
            f'ind i nabo, {len(unmerged)} uden nabo')
        # de umuligt-smeltbare lægges tilbage (accepteres/rød via trin 7)
        for lp in unmerged:
            lp.status = 'ok'
        return result + unmerged

    # ------------------------------------------------------------------ #
    #  Trin 3 + 6: del store marker                                       #
    # ------------------------------------------------------------------ #
    def _split_large(self, parcel, avg_ha, lo, hi, min_ha, max_ha):
        """Trin 3: prøv at dele i 2..5 lige dele hvor hver del rammer ±20% og
        holder min/max. Lykkes det → 'delt'. Ellers trin 6: kunstig MBR-opdeling
        målrettet avg → 'kunstig'."""
        # Trin 3: lige opdeling i n dele
        for n in range(2, MAX_SPLIT_PARTS + 1):
            target = parcel.area_ha / n
            if not (lo <= target <= hi):
                continue
            if target < min_ha or target > max_ha:
                continue
            pieces = self._split_into(parcel.geom, n)
            if len(pieces) == n and all(lo <= (g.area() / 10000) <= hi for g in pieces):
                return [Parcel(g, 'delt') for g in pieces]

        # Trin 6: kunstig grid-opdeling
        pieces = self._subdivide(parcel.geom, avg_ha, max_ha, min_ha)
        out = []
        for g in pieces:
            out.append(Parcel(g, 'kunstig'))
        return out if out else [Parcel(parcel.geom, 'kunstig')]

    def _split_into(self, geom, n):
        """Del geom i n strimler langs den lange MBR-akse (lige store i længde)."""
        cx, cy, angle, length, width = self._get_mbr_params(geom)
        cos_a, sin_a = math.cos(angle), math.sin(angle)

        def make_pt(u, v):
            return QgsPointXY(cx + u * cos_a - v * sin_a,
                              cy + u * sin_a + v * cos_a)

        seg = length / n
        pieces = []
        for k in range(n):
            u0 = -length / 2 + k * seg
            u1 = -length / 2 + (k + 1) * seg
            cell = QgsGeometry.fromPolygonXY([[
                make_pt(u0, -width), make_pt(u1, -width),
                make_pt(u1, width), make_pt(u0, width), make_pt(u0, -width),
            ]])
            clipped = geom.intersection(cell)
            if not clipped or clipped.isNull() or clipped.isEmpty():
                continue
            merged = None
            for part in self._single_parts(clipped):
                if part.area() > 1:
                    merged = part if merged is None else merged.combine(part)
            if merged is not None:
                pieces.append(merged)
        return pieces

    def _subdivide(self, geom, avg_ha, max_ha, min_ha, depth=0):
        """Kunstig MBR-grid-opdeling (fra v2.x): celler ~kvadratiske, målrettet avg,
        holdt over min_ha. Rekurser på dele der stadig er over max_ha."""
        if depth > 10:
            return [geom]
        area_ha = geom.area() / 10000
        cx, cy, angle, length, width = self._get_mbr_params(geom)
        n_total = max(2, round(area_ha / avg_ha))
        aspect = length / max(width, 0.01)
        n_cols = max(1, round(math.sqrt(n_total * aspect)))
        n_rows = max(1, round(n_total / n_cols))
        if n_cols * n_rows < 2:
            n_cols = 2
        if min_ha > 0:
            max_n = max(2, int(area_ha / min_ha))
            n_cols = min(n_cols, max_n)
            n_rows = min(n_rows, max_n)

        cos_a, sin_a = math.cos(angle), math.sin(angle)
        col_w = length / n_cols
        row_h = width / n_rows

        def make_pt(u, v):
            return QgsPointXY(cx + u * cos_a - v * sin_a,
                              cy + u * sin_a + v * cos_a)

        pieces = []
        for col in range(n_cols):
            u0 = -length / 2 + col * col_w
            u1 = -length / 2 + (col + 1) * col_w
            for row in range(n_rows):
                v0 = -width / 2 + row * row_h
                v1 = -width / 2 + (row + 1) * row_h
                cell = QgsGeometry.fromPolygonXY([[
                    make_pt(u0, v0), make_pt(u1, v0),
                    make_pt(u1, v1), make_pt(u0, v1), make_pt(u0, v0),
                ]])
                clipped = geom.intersection(cell)
                if not clipped or clipped.isNull() or clipped.isEmpty():
                    continue
                for part in self._single_parts(clipped):
                    if part.area() > 1:
                        pieces.append(part)
        if not pieces:
            return [geom]
        result = []
        for piece in pieces:
            if piece.area() / 10000 > max_ha:
                result.extend(self._subdivide(piece, avg_ha, max_ha, min_ha, depth + 1))
            else:
                result.append(piece)
        return result if result else [geom]

    # ------------------------------------------------------------------ #
    #  Markkort-indlæsning (uændret fra v2.x – velafprøvet)               #
    # ------------------------------------------------------------------ #
    def _load_markkort_parcels(self, union_geom, work_crs):
        if not os.path.exists(MARKKORT_PATH):
            return [QgsGeometry(union_geom)]
        mk_layer = QgsVectorLayer(MARKKORT_PATH, 'mk', 'ogr')
        if not mk_layer.isValid():
            return [QgsGeometry(union_geom)]

        mk_crs = mk_layer.crs()
        to_work = QgsCoordinateTransform(mk_crs, work_crs, QgsCoordinateTransformContext())
        from_work = QgsCoordinateTransform(work_crs, mk_crs, QgsCoordinateTransformContext())
        need = mk_crs != work_crs

        if need:
            fg = QgsGeometry.fromRect(union_geom.boundingBox())
            fg.transform(from_work)
            rect = fg.boundingBox()
        else:
            rect = union_geom.boundingBox()

        raw = []
        for feat in mk_layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
            g = feat.geometry()
            if not g or g.isNull() or g.isEmpty():
                continue
            if need:
                g = QgsGeometry(g)
                g.transform(to_work)
            if not g.isGeosValid():
                g = g.makeValid()
            clipped = g.intersection(union_geom)
            if not clipped or clipped.isNull() or clipped.isEmpty():
                continue
            clipped = self._extract_polygons(clipped)
            if clipped is None or clipped.area() < 1:
                continue
            raw.append(clipped)

        if not raw:
            return [QgsGeometry(union_geom)]

        dissolved = QgsGeometry.unaryUnion(raw)
        if not dissolved or dissolved.isNull() or dissolved.isEmpty():
            return [QgsGeometry(union_geom)]

        parcels = []
        mk_cov = None
        for part in dissolved.asGeometryCollection():
            if QgsWkbTypes.geometryType(part.wkbType()) != QgsWkbTypes.PolygonGeometry:
                continue
            cb = part.intersection(union_geom)
            if not cb or cb.isNull() or cb.isEmpty():
                continue
            for sub in self._single_parts(cb):
                if sub.area() < 10:   # kun nul-areal-splinter fra intersection fjernes
                    continue
                parcels.append(sub)
                mk_cov = QgsGeometry(sub) if mk_cov is None else mk_cov.combine(sub)

        if mk_cov is not None:
            uncovered = union_geom.difference(mk_cov)
            if uncovered and not uncovered.isNull() and not uncovered.isEmpty():
                for part in uncovered.asGeometryCollection():
                    if (QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.PolygonGeometry
                            and part.area() > 10):
                        parcels.append(part)
        return parcels if parcels else [QgsGeometry(union_geom)]

    # ------------------------------------------------------------------ #
    #  Output-lag + renderer (trin 4 + 7)                                 #
    # ------------------------------------------------------------------ #
    def _build_output_layer(self, parcels):
        layer = QgsVectorLayer(f'Polygon?crs={WORK_CRS}', 'Grid', 'memory')
        prov = layer.dataProvider()
        prov.addAttributes([
            QgsField('id', QVariant.Int),
            QgsField('areal_ha', QVariant.Double),
            QgsField('status', QVariant.String),
        ])
        layer.updateFields()
        feats = []
        fid = 1
        for p in parcels:
            for part in self._single_parts(p.geom):
                if part.area() < 1:
                    continue
                f = QgsFeature()
                f.setGeometry(part)
                f.setAttributes([fid, round(part.area() / 10000, 4), p.status])
                feats.append(f)
                fid += 1
        prov.addFeatures(feats)
        layer.updateExtents()
        return layer

    def _apply_renderer(self, layer):
        """Rule-based: 'rest' = rødt, alt andet = normal grøn-ish kant."""
        base = QgsFillSymbol.createSimple({
            'color': '0,0,0,0', 'outline_color': '35,140,60',
            'outline_width': '0.4'})
        root = QgsRuleBasedRenderer.Rule(None)

        rest_sym = QgsFillSymbol.createSimple({
            'color': '230,30,30,80', 'outline_color': '200,0,0',
            'outline_width': '0.6'})
        rest_rule = QgsRuleBasedRenderer.Rule(rest_sym, label='Rest (uden for min/max)',
                                              filterExp='"status" = \'rest\'')
        other_rule = QgsRuleBasedRenderer.Rule(base.clone(), label='Grid',
                                               filterExp='"status" <> \'rest\'')
        root.appendChild(rest_rule)
        root.appendChild(other_rule)
        layer.setRenderer(QgsRuleBasedRenderer(root))

    # ------------------------------------------------------------------ #
    #  Hjælpere                                                           #
    # ------------------------------------------------------------------ #
    def _cleanup(self, geom):
        if geom is None or geom.isNull() or geom.isEmpty():
            return geom
        if not geom.isGeosValid():
            geom = geom.makeValid()
        return geom

    def _single_parts(self, geom):
        if geom is None or geom.isNull() or geom.isEmpty():
            return
        for part in geom.asGeometryCollection():
            if QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.PolygonGeometry:
                yield part

    def _extract_polygons(self, geom):
        if geom is None or geom.isNull() or geom.isEmpty():
            return None
        if QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.PolygonGeometry:
            return geom
        parts = list(self._single_parts(geom))
        if not parts:
            return None
        result = parts[0]
        for p in parts[1:]:
            result = result.combine(p)
        return result

    def _get_mbr_params(self, geom):
        """Minimum bounding rectangle via rotating calipers. Returnerer
        (cx, cy, angle, length, width) med length >= width."""
        hull = geom.convexHull()
        poly = hull.asPolygon()
        if not poly or not poly[0] or len(poly[0]) < 3:
            return self._bbox_params(geom)
        pts = poly[0]
        n = len(pts) - 1
        best_area = float('inf')
        best = None
        for i in range(n):
            dx = pts[(i + 1) % n].x() - pts[i].x()
            dy = pts[(i + 1) % n].y() - pts[i].y()
            edge = math.hypot(dx, dy)
            if edge < 1e-10:
                continue
            ux, uy = dx / edge, dy / edge
            vx, vy = -uy, ux
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
