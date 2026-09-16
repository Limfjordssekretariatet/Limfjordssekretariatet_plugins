# -*- coding: utf-8 -*-
"""Trace — tegn et forløb ved at følge kanterne på eksisterende lag.

Et vandløbsforløb skal typisk følge noget, der allerede er kortlagt — en
eksisterende vandløbslinje, en grøft, kanten af en sø eller et
projektområde. Tegner man det i hånden, rammer man ved siden af, og så
hænger geometrien ikke sammen med det, den skal følge.

Værktøjet sporer i stedet: du klikker ét sted på nettet, fører musen
videre, og korteste vej langs de synlige linje- og fladelag tegnes op som
forhåndsvisning. Det svarer til Trace-værktøjet i ArcGIS Pro.

Snapning sker med værktøjets egen motor — hjørner og kanter på alle
synlige lag — og ikke gennem projektets snapindstillinger. Ellers ville
en opsætning, der kun snapper til det aktive lag, gøre sporingen ubrugelig
netop når man skal følge et andet lag.
"""

import math

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsSnappingConfig,
    QgsSnappingUtils,
    QgsTolerance,
    QgsTracer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolAdvancedDigitizing, QgsRubberBand, QgsSnapIndicator


class TraceTool(QgsMapToolAdvancedDigitizing):
    """Korttegneværktøj der sporer langs eksisterende linjer og flader.

    Sådan bruges det:

      * Klik én gang på en linje for at sætte startpunktet.
      * Før musen langs linjen. Sporet følger markøren skridt for skridt og
        huskes — det bliver ikke glemt, fordi markøren et øjeblik glider af
        linjen. Går du tilbage, trækkes sporet ind.
      * Er markøren væk fra nettet, vises det sporede plus en stiplet, lige
        linje hen til markøren.
      * Venstreklik låser det sporede fast. Klikker du væk fra nettet,
        kommer punktet med som fri hånd — sådan blandes sporing og
        frihåndstegning.
      * Enter eller højreklik gemmer det sporede. Frihåndshalen til
        markøren kommer ikke med, kun et klik lægger et frit punkt.
      * Backspace smider først det usikre spor efter sidste klik, derefter
        ét låst punkt ad gangen. Esc kasserer det hele.

    Hvorfor skridt for skridt: tidligere blev der ved hver musebevægelse
    regnet korteste vej fra sidste klik. På et langt, bugtet vandløb tog
    den så gerne en vej eller et matrikelskel, der gik mere direkte — og
    intet blev husket imellem, så en lige streg kunne erstatte hele sporet.
    """

    #: Hvor mange objekter sporingsgrafen højst bygges af. Et helt
    #: vandløbstema kan være hundredtusinder af linjer, og grafen bygges
    #: forfra hver gang kortudsnittet flytter sig.
    MAKS_OBJEKTER = 30000
    #: Grafen bygges af lidt mere end det synlige udsnit, så sporingen ikke
    #: stopper ved kanten af skærmen.
    UDSNIT_MARGIN = 1.5
    #: Snapafstand i skærmpixels.
    SNAP_TOLERANCE_PX = 12

    def __init__(self, iface):
        canvas = iface.mapCanvas()
        super().__init__(canvas, iface.cadDockWidget())
        self.iface = iface
        self.canvas = canvas

        self.tracer = QgsTracer()
        self.tracer.setMaxFeatureCount(self.MAKS_OBJEKTER)

        # Egen snapmotor, uafhængig af projektets snapindstillinger, så der
        # altid kan spores langs ethvert synligt lag — også når projektet er
        # sat til kun at snappe til det aktive lag.
        self.snap = QgsSnappingUtils()
        konfig = QgsSnappingConfig()
        konfig.setEnabled(True)
        konfig.setMode(QgsSnappingConfig.AllLayers)
        konfig.setTypeFlag(
            QgsSnappingConfig.VertexFlag | QgsSnappingConfig.SegmentFlag)
        konfig.setTolerance(self.SNAP_TOLERANCE_PX)
        konfig.setUnits(QgsTolerance.Pixels)
        self.snap.setConfig(konfig)

        self.lag = None
        self.geometritype = None      # "linje" eller "flade"
        self.punkter = []             # låste punkter, i kortets CRS
        # Det sporede efter sidste klik. Begynder i punkter[-1] og følger
        # markøren; låses først fast ved næste klik eller ved afslutning.
        self.spor = []
        # Markøren er væk fra nettet: forslaget får en lige hale hen til den.
        self._fri_hale = False
        self._sidste_punkt = None     # senest opsnappede markørposition
        # Beskeden om for mange objekter gives én gang pr. opbygning af
        # grafen — ellers ville den komme for hver musebevægelse.
        self._for_mange_meldt = False

        self.baand_laast = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.baand_laast.setColor(QColor(255, 80, 0, 220))
        self.baand_laast.setWidth(3)

        self.baand_forslag = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.baand_forslag.setColor(QColor(255, 140, 0, 200))
        self.baand_forslag.setLineStyle(Qt.DashLine)
        self.baand_forslag.setWidth(3)

        self.snapmarkering = QgsSnapIndicator(canvas)

        self.canvas.extentsChanged.connect(self._opdater_omfang)

    def cleanup(self):
        """Kaldes når pluginnet aflæsses."""
        try:
            self.canvas.extentsChanged.disconnect(self._opdater_omfang)
        except (TypeError, RuntimeError):
            pass
        self.baand_laast.reset(QgsWkbTypes.LineGeometry)
        self.baand_forslag.reset(QgsWkbTypes.LineGeometry)

    # ------------------------------------------------------------------
    # til- og frakobling
    # ------------------------------------------------------------------

    def activate(self):
        super().activate()
        lag = self.iface.activeLayer()
        ok, type_ = self._lag_type(lag)
        if not ok:
            self._sig_til(
                'Vælg først det lag, forløbet skal tegnes ind i — et '
                'linje- eller fladelag.')
            self.canvas.unsetMapTool(self)
            return

        if not lag.isEditable() and not lag.startEditing():
            self._sig_til(
                'Laget "{}" kunne ikke sættes i redigeringstilstand.'.format(
                    lag.name()))
            self.canvas.unsetMapTool(self)
            return

        self.lag = lag
        self.geometritype = type_
        self._nulstil_skitse()
        self._opdater_omfang()

    def deactivate(self):
        self._nulstil_skitse()
        self.lag = None
        self.geometritype = None
        super().deactivate()

    def _sig_til(self, besked):
        self.iface.messageBar().pushMessage(
            'Trace', besked, level=Qgis.Warning, duration=6)

    @staticmethod
    def _lag_type(lag):
        if lag is None or not hasattr(lag, 'wkbType'):
            return False, None
        geometri = QgsWkbTypes.geometryType(lag.wkbType())
        if geometri == QgsWkbTypes.LineGeometry:
            return True, 'linje'
        if geometri == QgsWkbTypes.PolygonGeometry:
            return True, 'flade'
        return False, None

    # ------------------------------------------------------------------
    # sporingsgrafens omfang
    # ------------------------------------------------------------------

    def _sporbare_lag(self):
        """De synlige linje- og fladelag der kan spores langs.

        Alle synlige lag tæller med, ikke kun det der redigeres: et
        vandløbsforløb skal netop følge noget andet end sig selv.
        """
        projekt = QgsProject.instance()
        rod = projekt.layerTreeRoot()
        lag = []
        for l in projekt.mapLayers().values():
            if not hasattr(l, 'wkbType'):
                continue
            if QgsWkbTypes.geometryType(l.wkbType()) not in (
                    QgsWkbTypes.LineGeometry, QgsWkbTypes.PolygonGeometry):
                continue
            knude = rod.findLayer(l.id())
            if knude is not None and not knude.isVisible():
                continue
            lag.append(l)
        return lag

    def _opdater_omfang(self):
        """Byg sporingsgrafen for det udsnit, der skal kunne spores i.

        Det er skærmudsnittet — og det sidst låste punkt. Uden punktet
        holdt sporingen op, så snart man panorerede langs et langt
        vandløb: punktet gled ud af grafen, der kunne ikke spores fra det,
        og værktøjet tegnede lige linjer i stedet. Rektanglet om begge
        dækker også strækningen imellem.
        """
        kortopsaetning = self.canvas.mapSettings()

        self.tracer.setDestinationCrs(
            kortopsaetning.destinationCrs(),
            QgsProject.instance().transformContext())
        self.tracer.setLayers(self._sporbare_lag())
        udsnit = self.canvas.extent()
        # Der spores fra sporets ende — den skal ligge i grafen, også når
        # man har panoreret væk fra den.
        ende = self.spor[-1] if self.spor else (
            self.punkter[-1] if self.punkter else None)
        if ende is not None:
            udsnit.combineExtentWith(ende.x(), ende.y())
        udsnit.scale(self.UDSNIT_MARGIN)
        self.tracer.setExtent(udsnit)
        self._for_mange_meldt = False

        self.snap.setMapSettings(kortopsaetning)

    def _spor_sti(self, fra, til):
        """Korteste vej langs nettet, eller None hvis der ikke er nogen."""
        if fra == til:
            return None
        punkter, fejl = self.tracer.findShortestPath(fra, til)
        if fejl == QgsTracer.ErrNone and punkter:
            return list(punkter)
        if fejl == QgsTracer.ErrTooManyFeatures and not self._for_mange_meldt:
            # Uden beskeden tegner værktøjet bare lige linjer, og man tror,
            # sporingen er gået i stykker.
            self._for_mange_meldt = True
            antal = '{:,}'.format(self.MAKS_OBJEKTER).replace(',', '.')
            self._sig_til(
                'Der er over {} objekter i udsnittet, så der spores ikke. '
                'Zoom ind, eller sluk tunge lag som matrikler og markkort — '
                'alle synlige linje- og fladelag tæller med.'.format(antal))
        return None

    def _afklar_punkt(self, e):
        """Markørens position, snappet med værktøjets egen motor.

        ``e.mapPoint()`` ville følge projektets snapindstillinger og kunne
        være begrænset til det aktive lag.
        """
        traef = self.snap.snapToMap(e.originalMapPoint())
        self.snapmarkering.setMatch(traef)
        if traef.isValid():
            return QgsPointXY(traef.point())
        return QgsPointXY(e.originalMapPoint())

    # ------------------------------------------------------------------
    # mus og tastatur
    # ------------------------------------------------------------------

    def cadCanvasMoveEvent(self, e):
        punkt = self._afklar_punkt(e)
        self._sidste_punkt = punkt

        if not self.punkter:
            self.baand_forslag.reset(QgsWkbTypes.LineGeometry)
            return

        self._foelg(punkt)
        self._vis_forslag(punkt)

    def cadCanvasPressEvent(self, e):
        if self.lag is None:
            return

        punkt = self._afklar_punkt(e)
        self._sidste_punkt = punkt

        if e.button() == Qt.RightButton:
            if self.punkter:
                self._foelg(punkt)
                self._laas_spor()
            self._afslut_skitse()
            return
        if e.button() != Qt.LeftButton:
            return

        if not self.punkter:
            self.punkter = [punkt]
            self.spor = [punkt]
            self._fri_hale = False
        else:
            self._foelg(punkt)
            # Et klik væk fra nettet er et frit punkt.
            self._laas_spor(punkt if self._fri_hale else None)

        self._opdater_baand()
        self.baand_forslag.reset(QgsWkbTypes.LineGeometry)

    def keyPressEvent(self, e):
        tast = e.key()
        if tast == Qt.Key_Escape:
            self._nulstil_skitse()
            e.ignore()
            return
        if tast in (Qt.Key_Return, Qt.Key_Enter):
            if self.punkter:
                self._laas_spor()
            self._afslut_skitse()
            e.ignore()
            return
        if tast in (Qt.Key_Backspace, Qt.Key_Delete):
            if len(self.spor) > 1:
                # Først det usikre spor efter sidste klik.
                self.spor = [self.punkter[-1]]
                self._fri_hale = False
            elif self.punkter:
                self.punkter.pop()
                self.spor = [self.punkter[-1]] if self.punkter else []
                self._fri_hale = False
            self._opdater_baand()
            if self._sidste_punkt is not None and self.punkter:
                self._vis_forslag(self._sidste_punkt)
            else:
                self.baand_forslag.reset(QgsWkbTypes.LineGeometry)
            e.ignore()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------
    # sporet
    # ------------------------------------------------------------------

    def _tolerance(self):
        """Hvor tæt på sporet markøren skal være for at trække det ind (kortenheder)."""
        return max(self.canvas.mapUnitsPerPixel() * 2.0, 1e-9)

    def _foelg(self, punkt):
        """Før sporet frem til ``punkt`` — eller træk det ind.

        Ligger punktet på det sporede, før enden, er brugeren gået tilbage,
        og sporet afkortes dertil. Ellers spores der fra sporets ende frem til
        punktet. Kan det ikke lade sig gøre — markøren er væk fra nettet —
        står sporet urørt, og forslaget får en fri hale.
        """
        if not self.spor:
            return
        tol = self._tolerance()

        if len(self.spor) >= 2:
            linje = QgsGeometry.fromPolylineXY(self.spor)
            pg = QgsGeometry.fromPointXY(punkt)
            if linje.distance(pg) <= tol:
                pos = linje.lineLocatePoint(pg)
                if pos < linje.length() - tol:
                    self.spor = self._afkort(self.spor, pos)
                    self._fri_hale = False
                    return

        sti = self._spor_sti(self.spor[-1], punkt)
        if sti:
            self.spor.extend(sti[1:])
            self._fri_hale = False
        else:
            self._fri_hale = (punkt.distance(self.spor[-1]) > tol)

    @staticmethod
    def _afkort(punkter, pos):
        """De første ``pos`` kortenheder af en linje, som punktliste."""
        ud = [punkter[0]]
        gaaet = 0.0
        for a, b in zip(punkter, punkter[1:]):
            stykke = math.hypot(b.x() - a.x(), b.y() - a.y())
            if stykke and gaaet + stykke >= pos:
                t_ = (pos - gaaet) / stykke
                p = QgsPointXY(a.x() + t_ * (b.x() - a.x()),
                               a.y() + t_ * (b.y() - a.y()))
                if p != ud[-1]:
                    ud.append(p)
                return ud
            ud.append(b)
            gaaet += stykke
        return ud

    def _laas_spor(self, frit_punkt=None):
        """Lås det sporede fast — og eventuelt et frit punkt bagefter."""
        if not self.punkter:
            return
        self.punkter.extend(self.spor[1:])
        if frit_punkt is not None and frit_punkt != self.punkter[-1]:
            self.punkter.append(frit_punkt)
        self.spor = [self.punkter[-1]]
        self._fri_hale = False

    def _vis_forslag(self, punkt):
        vis = list(self.spor)
        if self._fri_hale and (not vis or punkt != vis[-1]):
            vis.append(punkt)
        if len(vis) >= 2:
            self.baand_forslag.setToGeometry(
                QgsGeometry.fromPolylineXY(vis), None)
        else:
            self.baand_forslag.reset(QgsWkbTypes.LineGeometry)

    # ------------------------------------------------------------------
    # skitsen
    # ------------------------------------------------------------------

    def _opdater_baand(self):
        # Sporets ende har flyttet sig — grafen skal dække den.
        self._opdater_omfang()
        if len(self.punkter) >= 2:
            self.baand_laast.setToGeometry(
                QgsGeometry.fromPolylineXY(self.punkter), None)
        else:
            self.baand_laast.reset(QgsWkbTypes.LineGeometry)

    def _nulstil_skitse(self):
        self.punkter = []
        self.spor = []
        self._fri_hale = False
        self._sidste_punkt = None
        self._for_mange_meldt = False
        self.baand_laast.reset(QgsWkbTypes.LineGeometry)
        self.baand_forslag.reset(QgsWkbTypes.LineGeometry)

    def _afslut_skitse(self):
        if self.lag is None:
            return

        if self.geometritype == 'linje':
            if len(self.punkter) < 2:
                self._nulstil_skitse()
                return
            geom = QgsGeometry.fromPolylineXY(self.punkter)
        else:
            if len(self.punkter) < 3:
                self._nulstil_skitse()
                return
            ring = list(self.punkter)
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            geom = QgsGeometry.fromPolygonXY([ring])

        geom = self._til_lagets_crs(geom)
        if QgsWkbTypes.isMultiType(self.lag.wkbType()):
            geom.convertToMultiType()

        ok, _objekt = self.iface.vectorLayerTools().addFeature(
            self.lag, {}, geom)

        self._nulstil_skitse()
        if ok:
            # Det nye forløb skal selv kunne spores langs med det samme.
            self.tracer.invalidateGraph()

    def _til_lagets_crs(self, geom):
        kort_crs = self.canvas.mapSettings().destinationCrs()
        lag_crs = self.lag.crs()
        if kort_crs != lag_crs:
            geom.transform(QgsCoordinateTransform(
                kort_crs, lag_crs, QgsProject.instance()))
        return geom
