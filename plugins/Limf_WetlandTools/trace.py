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
from qgis.PyQt.QtWidgets import QApplication

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeature,
    QgsFeatureRequest,
    QgsFields,
    QgsGeometry,
    QgsMemoryProviderUtils,
    QgsPointLocator,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
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

    #: Hvor mange objekter sporingsnettet højst bygges af. Et helt
    #: vandløbstema kan være hundredtusinder af linjer, og nettet bygges
    #: forfra, hver gang man panorerer ud af det.
    MAKS_OBJEKTER = 30000
    #: Nettet bygges af lidt mere end det synlige udsnit, så sporingen ikke
    #: stopper ved kanten af skærmen.
    UDSNIT_MARGIN = 1.5
    #: Snapafstand i skærmpixels.
    SNAP_TOLERANCE_PX = 12

    def __init__(self, iface):
        canvas = iface.mapCanvas()
        super().__init__(canvas, iface.cadDockWidget())
        self.iface = iface
        self.canvas = canvas

        # Nettet der spores langs: de synlige linjer og fladekanter i
        # udsnittet, knudet i skæringerne. Se _byg_net.
        self.net = QgsMemoryProviderUtils.createMemoryLayer(
            'Sporingsnet', QgsFields(), QgsWkbTypes.LineString,
            canvas.mapSettings().destinationCrs())
        self.net_finder = None        # QgsPointLocator på nettet
        self._net_omfang = None       # det udsnit, nettet dækker
        self._net_foraeldet = True
        self._fejlet_behov = None     # udsnit, hvor nettet ikke kunne bygges
        self._kildelag = []           # lag, hvis ændringer gør nettet forældet

        self.tracer = QgsTracer()
        self.tracer.setLayers([self.net])

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
        self._markoer = None          # markørens position, før snap
        # En besked om, at der ikke kan spores, gives én gang pr. udsnit —
        # ellers ville den komme for hver musebevægelse.
        self._meldt = False

        self.baand_laast = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.baand_laast.setColor(QColor(255, 80, 0, 220))
        self.baand_laast.setWidth(3)

        self.baand_forslag = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.baand_forslag.setColor(QColor(255, 140, 0, 200))
        self.baand_forslag.setLineStyle(Qt.DashLine)
        self.baand_forslag.setWidth(3)

        self.snapmarkering = QgsSnapIndicator(canvas)

        for signal, slot in self._kortsignaler():
            signal.connect(slot)

    def _kortsignaler(self):
        return (
            (self.canvas.extentsChanged, self._kort_flyttet),
            (self.canvas.layersChanged, self._net_er_foraeldet),
            (self.canvas.destinationCrsChanged, self._net_er_foraeldet),
        )

    def cleanup(self):
        """Kaldes når pluginnet aflæsses."""
        for signal, slot in self._kortsignaler():
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._afkobl_kildelag()
        self.tracer.setLayers([])
        self.net_finder = None
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
        self._net_foraeldet = True
        self._kort_flyttet()

    def deactivate(self):
        self._nulstil_skitse()
        self.lag = None
        self.geometritype = None
        super().deactivate()

    def _sig_til(self, besked):
        self.iface.messageBar().pushMessage(
            'Trace', besked, level=Qgis.Warning, duration=6)

    def _meld(self, besked):
        if not self._meldt:
            self._meldt = True
            self._sig_til(besked)

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
    # sporingsnettet
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

    def _kort_flyttet(self):
        # Snapafstanden i pixels regnes om til kortenheder ud fra udsnittet.
        self.snap.setMapSettings(self.canvas.mapSettings())
        self._meldt = False

    def _net_er_foraeldet(self, *_):
        self._net_foraeldet = True

    def _afkobl_kildelag(self):
        for lag in self._kildelag:
            try:
                lag.layerModified.disconnect(self._net_er_foraeldet)
                lag.dataChanged.disconnect(self._net_er_foraeldet)
            except (TypeError, RuntimeError):
                pass
        self._kildelag = []

    def _sikr_net(self):
        """Sørg for, at nettet dækker skærmen og sporets ende.

        Sporets ende skal med, også når man har panoreret væk fra den —
        ellers holder sporingen op, så snart den glider ud af udsnittet.
        """
        behov = self.canvas.extent()
        ende = self.spor[-1] if self.spor else (
            self.punkter[-1] if self.punkter else None)
        if ende is not None:
            behov.combineExtentWith(ende.x(), ende.y())

        if not self._net_foraeldet:
            if self._net_omfang is not None and self._net_omfang.contains(behov):
                return
            if self._fejlet_behov is not None and self._fejlet_behov == behov:
                return        # ikke et nyt forsøg ved hver musebevægelse

        omfang = QgsRectangle(behov)
        omfang.scale(self.UDSNIT_MARGIN)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok = self._byg_net(omfang)
        finally:
            QApplication.restoreOverrideCursor()
        self._net_foraeldet = False
        self._net_omfang = omfang if ok else None
        self._fejlet_behov = None if ok else QgsRectangle(behov)

    def _byg_net(self, omfang):
        """Byg nettet for ``omfang`` (kortets CRS). False, hvis det ikke går.

        QgsTracer knuder selv linjerne med GEOS, men kontrollerer ikke
        resultatet. Giver GEOS op — det sker, når linjer ligger næsten oven i
        hinanden, fx flere udgaver af det samme vandløbsforløb — bliver
        grafen tom, og så kan der slet ikke spores, heller ikke langs alt det
        andet i udsnittet. Værktøjet snappede stadig til linjerne, så det så
        ud, som om sporingen bare ikke ville følge med.

        Derfor knudes linjerne her først med unaryUnion, der klarer sig
        igennem den slags, og både sporing og snap arbejder på resultatet —
        så et snappet punkt altid også ligger i grafen.
        """
        projekt = QgsProject.instance()
        kort_crs = self.canvas.mapSettings().destinationCrs()
        self._afkobl_kildelag()
        self.net_finder = None
        self.net.dataProvider().truncate()
        self.tracer.invalidateGraph()

        geometrier = []
        antal = 0
        for lag in self._sporbare_lag():
            lag.layerModified.connect(self._net_er_foraeldet)
            lag.dataChanged.connect(self._net_er_foraeldet)
            self._kildelag.append(lag)

            til_kort = None
            lagomfang = omfang
            if lag.crs() != kort_crs:
                til_kort = QgsCoordinateTransform(lag.crs(), kort_crs, projekt)
                try:
                    lagomfang = QgsCoordinateTransform(
                        kort_crs, lag.crs(), projekt).transformBoundingBox(omfang)
                except QgsCsException:
                    continue

            anmodning = QgsFeatureRequest().setFilterRect(
                lagomfang).setNoAttributes()
            for objekt in lag.getFeatures(anmodning):
                antal += 1
                if antal > self.MAKS_OBJEKTER:
                    graense = '{:,}'.format(self.MAKS_OBJEKTER).replace(',', '.')
                    self._meld(
                        'Der er over {} objekter i udsnittet, så der spores '
                        'ikke. Zoom ind, eller sluk tunge lag som matrikler og '
                        'markkort — alle synlige linje- og fladelag tæller '
                        'med.'.format(graense))
                    return False
                geom = self._som_linjer(objekt.geometry())
                if geom is None:
                    continue
                if til_kort is not None:
                    try:
                        geom.transform(til_kort)
                    except QgsCsException:
                        continue
                geometrier.append(geom)

        if not geometrier:
            return True       # intet at spore langs her — ikke en fejl

        dele = [d for d in self._knyt(geometrier, kort_crs).asGeometryCollection()
                if not d.isEmpty()]
        objekter = []
        for del_ in dele:
            objekt = QgsFeature()
            objekt.setGeometry(del_)
            objekter.append(objekt)
        self.net.setCrs(kort_crs)
        self.net.dataProvider().addFeatures(objekter)
        self.net.updateExtents()
        self.net_finder = QgsPointLocator(self.net)
        self.tracer.setDestinationCrs(kort_crs, projekt.transformContext())
        self.tracer.invalidateGraph()

        if dele and not self.tracer.isPointSnapped(QgsPointXY(dele[0].vertexAt(0))):
            self._meld(
                'Linjerne i udsnittet kunne ikke samles til et net, så der '
                'spores ikke her. Det sker, når linjer ligger næsten oven i '
                'hinanden — sluk de lag, det gælder, eller zoom et andet sted hen.')
            return False
        return True

    @staticmethod
    def _som_linjer(geom):
        """Geometrien som 2D-linjer: linjer, som de er, og flader som kanter."""
        if geom is None or geom.isNull() or geom.isEmpty():
            return None
        if QgsWkbTypes.isCurvedType(geom.wkbType()):
            geom.convertToStraightSegment()
        type_ = QgsWkbTypes.geometryType(geom.wkbType())
        if type_ == QgsWkbTypes.PolygonGeometry:
            kant = geom.constGet().boundary()
            if kant is None:
                return None
            geom = QgsGeometry(kant)
        elif type_ != QgsWkbTypes.LineGeometry:
            return None
        abstrakt = geom.get()
        abstrakt.dropZValue()
        abstrakt.dropMValue()
        return geom

    @staticmethod
    def _knyt(geometrier, crs):
        """Linjerne knudet i alle skæringer.

        unaryUnion falder selv tilbage på snapning, når de flydende tal ikke
        slår til. Slår den alligevel fejl, lægges linjerne på et fint gitter
        og knudes igen. Som sidste udvej gives linjerne, som de er, og
        QgsTracer forsøger selv.
        """
        samlet = QgsGeometry.collectGeometry(geometrier)
        gitter = 1e-9 if crs.isGeographic() else 1e-4
        for forsoeg in (
                lambda: QgsGeometry.unaryUnion(geometrier),
                lambda: samlet.snappedToGrid(gitter, gitter).node()):
            knudet = forsoeg()
            if knudet is not None and not knudet.isNull() and not knudet.isEmpty():
                return knudet
        return samlet

    def _spor_sti(self, fra, til):
        """Korteste vej langs nettet, eller None hvis der ikke er nogen."""
        if fra == til:
            return None
        self._sikr_net()
        if self.net_finder is None:
            return None
        punkter, fejl = self.tracer.findShortestPath(fra, til)
        if fejl == QgsTracer.ErrNone and punkter:
            return list(punkter)
        return None

    def _afklar_punkt(self, e):
        """Markørens position — snappet, og lagt på nettet, hvis den er der.

        Der snappes med værktøjets egen motor til alle synlige lag;
        ``e.mapPoint()`` ville følge projektets snapindstillinger og kunne
        være begrænset til det aktive lag. Punktet flyttes derefter over på
        nettet (højst en pixel), for kun fra punkter i nettet kan der spores.
        """
        kortpunkt = QgsPointXY(e.originalMapPoint())
        self._markoer = kortpunkt
        traef = self.snap.snapToMap(kortpunkt)
        self.snapmarkering.setMatch(traef)
        punkt = QgsPointXY(traef.point()) if traef.isValid() else kortpunkt
        return self._paa_nettet(punkt) or punkt

    def _paa_nettet(self, punkt):
        self._sikr_net()
        if self.net_finder is None:
            return None
        tolerance = self.canvas.mapUnitsPerPixel()
        traef = self.net_finder.nearestVertex(punkt, tolerance)
        if not traef.isValid():
            traef = self.net_finder.nearestEdge(punkt, tolerance)
        return QgsPointXY(traef.point()) if traef.isValid() else None

    def _naboer(self, markoer):
        """Det nærmeste punkt på hvert stykke af nettet inden for snapafstanden."""
        if self.net_finder is None:
            return []
        r = self.canvas.mapUnitsPerPixel() * self.SNAP_TOLERANCE_PX
        rektangel = QgsRectangle(markoer.x() - r, markoer.y() - r,
                                 markoer.x() + r, markoer.y() + r)
        stykker = {m.featureId() for m in self.net_finder.edgesInRect(rektangel)}
        markoer_geom = QgsGeometry.fromPointXY(markoer)
        ud = []
        for fid in stykker:
            geom = self.net.getFeature(fid).geometry()
            punkt = geom.nearestPoint(markoer_geom).asPoint()
            if punkt.distance(markoer) <= r:
                ud.append(QgsPointXY(punkt))
        return ud

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
        """Før sporet frem mod markøren — eller træk det ind.

        Kandidaterne er det snappede ``punkt`` og det nærmeste punkt på hvert
        stykke af nettet inden for snapafstanden. For hver kandidat:

          * ligger den på det sporede, før enden, er brugeren gået tilbage,
            og sporet kan afkortes dertil;
          * ligger den ved sporets ende, kan sporet blive, hvor det er;
          * ellers kan der spores fra sporets ende frem til den.

        Prisen er afstanden til markøren plus den omvej, sporet skal tage.
        Det gør sporet klæbende: ligger en anden linje tættere på markøren,
        men kun kan nås ad en omvej, fortsætter sporet ad den linje, det
        følger. Uden det hoppede sporet frem og tilbage mellem linjer, der
        ligger tæt — en grøft og et matrikelskel langs vandløbet — og fik
        lange sløjfer med.

        Er der ingen kandidat — markøren er væk fra nettet — står sporet
        urørt, og forslaget får en fri hale.
        """
        if not self.spor:
            return
        tol = self._tolerance()
        markoer = self._markoer if self._markoer is not None else punkt
        ende = self.spor[-1]
        linje = (QgsGeometry.fromPolylineXY(self.spor)
                 if len(self.spor) >= 2 else None)
        # Længere omveje end dette tages ikke — så bliver sporet stående.
        maks_omvej = self.canvas.mapUnitsPerPixel() * 25

        bedst = None                  # (pris, handling, data)
        for kandidat in [punkt] + self._naboer(markoer):
            valg = None
            afstand = markoer.distance(kandidat)
            pg = QgsGeometry.fromPointXY(kandidat)
            if kandidat.distance(ende) <= tol:
                valg = (afstand, 'bliv', None)
            elif linje is not None and linje.distance(pg) <= tol:
                pos = linje.lineLocatePoint(pg)
                if pos < linje.length() - tol:
                    valg = (afstand, 'tilbage', pos)
            if valg is None:
                sti = self._spor_sti(ende, kandidat)
                if not sti:
                    continue
                lige = ende.distance(kandidat)
                omvej = QgsGeometry.fromPolylineXY(sti).length() - lige
                if omvej > 3 * lige + maks_omvej:
                    continue
                valg = (afstand + omvej, 'frem', sti)
            if bedst is None or valg[0] < bedst[0]:
                bedst = valg

        if bedst is None:
            self._fri_hale = (punkt.distance(ende) > tol)
            return
        _pris, handling, data = bedst
        if handling == 'tilbage':
            self.spor = self._afkort(self.spor, data)
        elif handling == 'frem':
            # Den gamle ende ligger som regel midt på et stykke, som sporet
            # nu fortsætter ad — så er den et overflødigt knudepunkt.
            if len(self.spor) >= 2 and QgsGeometry.fromPolylineXY(
                    [self.spor[-2], data[1]]).distance(
                    QgsGeometry.fromPointXY(ende)) <= tol / 200:
                self.spor.pop()
            self.spor.extend(data[1:])
        self._fri_hale = False

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
        self._meldt = False
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
            self._net_foraeldet = True

    def _til_lagets_crs(self, geom):
        kort_crs = self.canvas.mapSettings().destinationCrs()
        lag_crs = self.lag.crs()
        if kort_crs != lag_crs:
            geom.transform(QgsCoordinateTransform(
                kort_crs, lag_crs, QgsProject.instance()))
        return geom
