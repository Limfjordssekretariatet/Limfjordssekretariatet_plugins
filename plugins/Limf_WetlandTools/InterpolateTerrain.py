# -*- coding: utf-8 -*-
"""Interpoler terræn: fyld et område med en flade, der passer til kanten.

Oprindeligt en model eksporteret fra QGIS. Fremgangsmåden er den samme:
punkter langs områdets kant får terrænkoten fra DHM'et, og derfra
interpoleres en flade (IDW) ind over området, som lægges oven på DHM'et.
Kanten er dermed fælles for de to, og overgangen bliver glat — men kun
hvis fladen ligger i DHM'ets eget net. Gør den ikke det, skal den
skaleres om ved sammenlægningen, og så springer koterne ved kanten.
"""

from qgis.core import QgsProcessing
from qgis.core import QgsProcessingAlgorithm
from qgis.core import QgsProcessingException
from qgis.core import QgsProcessingMultiStepFeedback
from qgis.core import QgsProcessingParameterVectorLayer
from qgis.core import QgsProcessingParameterRasterLayer
from qgis.core import QgsProcessingParameterRasterDestination
from qgis.core import QgsProcessingUtils
from qgis.core import QgsVectorFileWriter, QgsCoordinateTransformContext
from qgis.core import QgsMessageLog, Qgis
from osgeo import gdal
import processing

#: Så mange DHM-celler lægges uden om området, når fladen regnes ud. Den
#: skal dække hele området efter klipningen, også når kanten ikke følger
#: cellerne.
MARGIN_CELLER = 4

#: Tomme celler i den interpolerede flade. 0 ville blive til kote 0 i det
#: færdige terræn — et hul midt i området, der ligner en sø.
TOM = -9999.0

#: Hvor mange kantpunkter hver celle må vægte sammen. Med de 12 nærmeste
#: ligger de alle på den nærmeste kant, og fladen bliver til telte med
#: folder ned langs diagonalerne — målt til 1,5 m spring mellem naboceller
#: på et område på 300 m. Med 400 punkter tæller flere kanter med, og
#: springet falder til 6 cm.
MAKS_PUNKTER = 400

#: Vægtningen falder med kvadratet på afstanden. Højere eksponent lader
#: det nærmeste punkt bestemme alt — samme foldede flade.
POTENS = 2

#: Flere kantpunkter end dette giver ikke en bedre flade, men koster tid.
#: Punkterne lægges med 1 m, indtil kanten bliver længere end det.
MAKS_KANTPUNKTER = 2000

#: Så mange celler må den interpolerede flade højst have. Et område på
#: 2,5 x 1,4 km i et DHM på 0,4 m er 22 mio. celler, og interpolationen
#: tog 3½ minut. Fladen fra kantpunkterne er glat, så den kan regnes
#: grovere og skaleres op til DHM'ets celler bagefter.
MAKS_CELLER = 2_000_000


class InterpolerTerrn(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('omrde', 'Område', types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterRasterLayer('dhm', 'DHM', defaultValue=None))
        self.addParameter(QgsProcessingParameterRasterDestination('Merge', 'Fyldt højdemodel', createByDefault=True, defaultValue=None))

    def processAlgorithm(self, parameters, context, model_feedback):
        feedback = QgsProcessingMultiStepFeedback(5, model_feedback)
        results = {}
        outputs = {}

        omr_lag = self.parameterAsVectorLayer(parameters, 'omrde', context)
        dhm_lag = self.parameterAsRasterLayer(parameters, 'dhm', context)

        # Points along geometry
        alg_params = {
            'DISTANCE': self._punktafstand(omr_lag),
            'END_OFFSET': 0,
            'INPUT': parameters['omrde'],
            'START_OFFSET': 0,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PointsAlongGeometry'] = processing.run('native:pointsalonglines', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        # Sample raster values
        alg_params = {
            'COLUMN_PREFIX': 'z',
            'INPUT': outputs['PointsAlongGeometry']['OUTPUT'],
            'RASTERCOPY': parameters['dhm'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['SampleRasterValues'] = processing.run('native:rastersampling', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        # Grid (IDW with nearest neighbor searching)
        #
        # Fladen lægges i DHM'ets eget net og med en søgeradius, der dækker
        # hele området. Uden -txe/-tye/-outsize bruger gdal_grid sine egne
        # 256 x 256 celler: på et område på 300 m bliver cellen over en
        # meter, og ved sammenlægningen med et DHM på 0,4 m skal den så
        # skaleres om — det er dér, overgangen holder op med at være glat.
        net = self._net(omr_lag, dhm_lag)
        alg_params = {
            'DATA_TYPE': 5,  # Float32
            'EXTRA': net['extra'],
            'INPUT': outputs['SampleRasterValues']['OUTPUT'],
            'MAX_POINTS': MAKS_PUNKTER,
            'MIN_POINTS': 0,
            'NODATA': TOM,
            'OPTIONS': None,
            'POWER': POTENS,
            'RADIUS': net['radius'],
            'SMOOTHING': 0,
            'Z_FIELD': 'z1',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        feedback.pushInfo(
            'Interpoleret flade: %d x %d celler á %.2f m, søgeradius %.0f m.'
            % (net['kolonner'], net['raekker'], net['celle'], net['radius']))
        outputs['GridIdwWithNearestNeighborSearching'] = processing.run('gdal:gridinversedistancenearestneighbor', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # Klip fladen til området, og læg den samtidig i DHM'ets celler.
        # Er fladen regnet grovere end DHM'et (store områder), skaleres den
        # op her — bilineært, så den bliver ved med at være glat.
        idw_path     = outputs['GridIdwWithNearestNeighborSearching']['OUTPUT']
        cutline_path = self._cutline(omr_lag)
        clipped_path = QgsProcessingUtils.generateTempFilename('clipped.tif')
        gdal.Warp(
            clipped_path, idw_path,
            cutlineDSName=cutline_path,
            cropToCutline=True,
            srcNodata=TOM,
            dstNodata=TOM,
            xRes=net['dhm_celle'], yRes=net['dhm_celle'],
            resampleAlg='bilinear',
            # De samme cellekanter som DHM'et. targetAlignedPixels ville
            # rette ind efter et net med nulpunkt i (0,0) — ligger DHM'ets
            # hjørne ikke dér, forskydes fladen en halv celle.
            outputBounds=net['grænser'],
            outputType=gdal.GDT_Float32,
            format='GTiff',
        )

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # Merge via Python GDAL (undgår gdal_merge.bat Windows-encodingfejl)
        #
        # Resultatet holdes i DHM'ets net og udstrækning: den interpolerede
        # flade ligger allerede i samme net, så den lægges oven på celle for
        # celle uden at blive skaleret om.
        out_path  = self.parameterAsOutputLayer(parameters, 'Merge', context)
        dhm_path  = self._raster_sti(dhm_lag)
        gdal.Warp(
            out_path, [dhm_path, clipped_path], format='GTiff',
            outputType=gdal.GDT_Float32,
            xRes=net['dhm_celle'], yRes=net['dhm_celle'],
            outputBounds=net['dhm_grænser'],
        )

        results['Merge'] = out_path
        return results

    @staticmethod
    def _punktafstand(omr_lag):
        """Afstand mellem kantpunkterne (m) — 1 m, indtil kanten er lang.

        Et projektområde på flere kilometer ville ellers give titusinder af
        punkter, og interpolationen bliver langsom uden at blive bedre.
        """
        omkreds = 0.0
        for objekt in omr_lag.getFeatures():
            geom = objekt.geometry()
            if geom is not None and not geom.isEmpty():
                omkreds += geom.length()   # for flader: kantens længde
        if omkreds <= 0:
            return 1.0
        return max(1.0, omkreds / MAKS_KANTPUNKTER)

    @staticmethod
    def _raster_sti(lag):
        """Filstien til et rasterlag, uden QGIS' tilføjelser efter '|'."""
        return lag.source().split('|')[0]

    def _net(self, omr_lag, dhm_lag):
        """Nettet den interpolerede flade skal ligge i — DHM'ets eget.

        Cellestørrelse og hjørner tages fra DHM'et, og områdets udstrækning
        rundes ud til hele celler med en margen. Søgeradius sættes, så
        kantpunkterne kan nå hele vejen ind til midten af området; med en
        fast radius fik et større område ingen værdier i midten.
        """
        ds = gdal.Open(self._raster_sti(dhm_lag))
        if ds is None:
            raise QgsProcessingException(
                'Højdemodellen kunne ikke åbnes: %s'
                % self._raster_sti(dhm_lag))
        gt = ds.GetGeoTransform()
        celle = abs(gt[1])
        x0, y1 = gt[0], gt[3]
        x1 = x0 + ds.RasterXSize * gt[1]
        y0 = y1 + ds.RasterYSize * gt[5]
        ds = None
        if not celle:
            raise QgsProcessingException(
                'Højdemodellen har ingen cellestørrelse.')

        omr = omr_lag.extent()
        dhm_celle = celle
        margin = MARGIN_CELLER * celle
        # Ud til nærmeste cellekant i DHM'ets net, så de to passer sammen.
        vest = x0 + celle * ((omr.xMinimum() - margin - x0) // celle)
        syd = y0 + celle * ((omr.yMinimum() - margin - y0) // celle)
        oest = x0 + celle * -(-(omr.xMaximum() + margin - x0) // celle)
        nord = y0 + celle * -(-(omr.yMaximum() + margin - y0) // celle)

        # Et stort område i DHM'ets egen opløsning bliver til millioner af
        # celler, og interpolationen tager minutter. Fladen fra kantpunkterne
        # er glat, så den kan regnes grovere og skaleres op bagefter uden at
        # miste noget. Faktoren er et helt tal, så cellekanterne stadig
        # falder sammen med DHM'ets.
        bredde = max(1, int(round((oest - vest) / celle)))
        hoejde = max(1, int(round((nord - syd) / celle)))
        faktor = 1
        while (bredde // faktor) * (hoejde // faktor) > MAKS_CELLER:
            faktor += 1
        celle = dhm_celle * faktor
        oest = vest + celle * -(-bredde // faktor)
        nord = syd + celle * -(-hoejde // faktor)
        kolonner = max(1, int(round((oest - vest) / celle)))
        raekker = max(1, int(round((nord - syd) / celle)))

        # Halvdelen af diagonalen rækker fra enhver kant til midten.
        radius = max(100.0, 0.5 * ((omr.width() ** 2 + omr.height() ** 2)
                                   ** 0.5) + margin)
        return {
            'celle': celle,
            'dhm_celle': dhm_celle,
            'kolonner': kolonner,
            'raekker': raekker,
            'radius': radius,
            'grænser': (vest, syd, oest, nord),
            'dhm_grænser': (x0, y0, x1, y1),
            'extra': ('-txe %.6f %.6f -tye %.6f %.6f -outsize %d %d'
                      % (vest, oest, syd, nord, kolonner, raekker)),
        }

    def _cutline(self, layer):
        """Områdelaget som en GPKG, GDAL kan klippe med.

        Der skrives altid en ny fil med kun de objekter, der HAR en
        geometri. To grunde:

        * Et objekt uden geometri — en række, der er oprettet, men ikke
          tegnet — får klipningen til at stoppe med "Cutline feature
          without a geometry", efter at interpolationen er kørt færdig.
        * Et midlertidigt lag har ingen fil at pege på. Det blev tidligere
          kendt på "memory:" i kilden, men QGIS skriver dem nu som
          "Polygon?crs=EPSG:25832&uid={…}", og lagadressen endte som
          filsti hos GDAL: "Cannot open Polygon?crs=…".
        """
        objekter = [f for f in layer.getFeatures()
                    if f.hasGeometry() and not f.geometry().isEmpty()]
        if not objekter:
            raise QgsProcessingException(
                'Områdelaget "%s" har ingen objekter med en geometri. Tegn '
                'området, eller vælg et andet lag.' % layer.name())
        uden = layer.featureCount() - len(objekter)
        if uden > 0:
            # Hellere end at stoppe: rækker uden geometri er almindelige i
            # et lag, man har tegnet i undervejs.
            QgsMessageLog.logMessage(
                '%d objekt(er) i "%s" har ingen geometri og bruges ikke som '
                'område.' % (uden, layer.name()), 'Vandprojekter', Qgis.Info)

        sti = QgsProcessingUtils.generateTempFilename('clip_mask.gpkg')
        skriver = QgsVectorFileWriter.create(
            sti, layer.fields(), layer.wkbType(), layer.crs(),
            QgsCoordinateTransformContext(),
            self._gpkg_muligheder())
        if skriver.hasError() != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(
                'Området kunne ikke skrives til en midlertidig fil: %s'
                % skriver.errorMessage())
        for f in objekter:
            skriver.addFeature(f)
        del skriver
        return sti

    @staticmethod
    def _gpkg_muligheder():
        muligheder = QgsVectorFileWriter.SaveVectorOptions()
        muligheder.driverName = 'GPKG'
        muligheder.layerName = 'omraade'
        return muligheder

    def name(self):
        return 'Interpoler terræn'

    def displayName(self):
        return 'Interpoler terræn'

    def group(self):
        return ''

    def groupId(self):
        return ''

    def createInstance(self):
        return InterpolerTerrn()
