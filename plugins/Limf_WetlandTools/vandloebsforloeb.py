# -*- coding: utf-8 -*-
"""Tegn vandløbsforløb ved at følge kanterne på eksisterende lag.

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


class VandloebsforloebTool(QgsMapToolAdvancedDigitizing):
    """Korttegneværktøj der sporer langs eksisterende linjer og flader.

    Sådan bruges det:

      * Klik én gang for at sætte startpunktet.
      * Før musen: ligger både sidste punkt og markøren på nettet af
        synlige linje- og fladelag, tegnes korteste vej derimellem som
        forhåndsvisning, der følger markøren. Kan der ikke spores, vises
        en almindelig ret linje i stedet.
      * Enter eller højreklik afslutter og gemmer. Det, der lige nu spores
        hen til markøren, kommer med — så et sammenhængende forløb koster
        kun to klik: start og slut.
      * Et venstreklik undervejs låser den sporede vej fast og starter en
        ny sporing derfra. Brugbart ved forgreninger, eller hvis en del af
        forløbet skal tegnes i fri hånd.
      * Backspace fortryder sidste låste punkt, Esc kasserer det hele.
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
        self._sidste_punkt = None     # senest opsnappede markørposition

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
            'Vandløbsforløb', besked, level=Qgis.Warning, duration=6)

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
        """Byg sporingsgrafen for det udsnit der er på skærmen nu."""
        kortopsaetning = self.canvas.mapSettings()

        self.tracer.setDestinationCrs(
            kortopsaetning.destinationCrs(),
            QgsProject.instance().transformContext())
        self.tracer.setLayers(self._sporbare_lag())
        udsnit = self.canvas.extent()
        udsnit.scale(self.UDSNIT_MARGIN)
        self.tracer.setExtent(udsnit)

        self.snap.setMapSettings(kortopsaetning)

    def _spor_sti(self, fra, til):
        """Korteste vej langs nettet, eller None hvis der ikke er nogen."""
        if fra == til:
            return None
        punkter, fejl = self.tracer.findShortestPath(fra, til)
        if fejl == QgsTracer.ErrNone and punkter:
            return list(punkter)
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

        sti = self._spor_sti(self.punkter[-1], punkt)
        forslag = sti if sti else [self.punkter[-1], punkt]
        self.baand_forslag.setToGeometry(
            QgsGeometry.fromPolylineXY(forslag), None)

    def cadCanvasPressEvent(self, e):
        if self.lag is None:
            return

        punkt = self._afklar_punkt(e)
        self._sidste_punkt = punkt

        if e.button() == Qt.RightButton:
            if self.punkter:
                self._forlaeng_til(punkt)
                self._opdater_baand()
            self._afslut_skitse()
            return
        if e.button() != Qt.LeftButton:
            return

        if not self.punkter:
            self.punkter.append(punkt)
        else:
            self._forlaeng_til(punkt)

        self._opdater_baand()
        self.baand_forslag.reset(QgsWkbTypes.LineGeometry)

    def keyPressEvent(self, e):
        tast = e.key()
        if tast == Qt.Key_Escape:
            self._nulstil_skitse()
            e.ignore()
            return
        if tast in (Qt.Key_Return, Qt.Key_Enter):
            if self.punkter and self._sidste_punkt is not None:
                self._forlaeng_til(self._sidste_punkt)
                self._opdater_baand()
            self._afslut_skitse()
            e.ignore()
            return
        if tast in (Qt.Key_Backspace, Qt.Key_Delete):
            if self.punkter:
                self.punkter.pop()
                self._opdater_baand()
            e.ignore()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------
    # skitsen
    # ------------------------------------------------------------------

    def _opdater_baand(self):
        if len(self.punkter) >= 2:
            self.baand_laast.setToGeometry(
                QgsGeometry.fromPolylineXY(self.punkter), None)
        else:
            self.baand_laast.reset(QgsWkbTypes.LineGeometry)

    def _forlaeng_til(self, punkt):
        """Lås vejen fra sidste punkt frem til ``punkt`` fast."""
        if punkt == self.punkter[-1]:
            return
        sti = self._spor_sti(self.punkter[-1], punkt)
        if sti:
            self.punkter.extend(sti[1:])
        else:
            self.punkter.append(punkt)

    def _nulstil_skitse(self):
        self.punkter = []
        self._sidste_punkt = None
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
