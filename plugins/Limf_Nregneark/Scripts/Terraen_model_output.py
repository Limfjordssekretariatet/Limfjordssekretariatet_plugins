import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingOutputRasterLayer,
    QgsProcessingOutputVectorLayer,
    QgsProcessingException,
    QgsProject,
    QgsVectorFileWriter,
    QgsCoordinateTransformContext,
)

# Sørg for at scriptets egen mappe er på sys.path, så 'utils' altid kan
# importeres uanset hvordan QGIS' script-provider loader filen.
import os as _os, sys as _sys
try:
    _d = _os.path.dirname(_os.path.abspath(__file__))
    if _d and _d not in _sys.path:
        _sys.path.insert(0, _d)
except NameError:
    pass  # __file__ ikke defineret i denne load-kontekst
from utils import find_output_filer  # noqa: F401


def _gem_vektor_sikkert(lag, maal_sti, projekt, feedback, lagnavn):
    """Gem 'lag' til maal_sti som GPKG, robust over for at _frigiv_gpkg_fil
    kan slette lagets underliggende C++-objekt.

    Rækkefølge-fælde: _frigiv_gpkg_fil() fjerner alle projekt-lag der bruger
    maal-filen. Hvis 'lag' selv er indlæst i projektet og peger på maal-filen
    (fx fra en tidligere kørsel), bliver dets C++-objekt slettet — og et
    efterfølgende writeAsVectorFormatV3(lag, …) crasher med
    "wrapped C/C++ object … has been deleted".

    Derfor: læs lagets kilde-sti FØR frigivelsen; hvis laget er dødt bagefter,
    genindlæs det fra kilde-stien inden vi skriver. Returnerer måls-stien ved
    succes, ellers ''."""
    from qgis.core import QgsVectorLayer

    # Kilde-sti mens laget stadig er levende.
    try:
        kilde = lag.source().split('|', 1)[0]
    except RuntimeError:
        kilde = None

    _frigiv_gpkg_fil(projekt, maal_sti, feedback)

    # Er laget stadig gyldigt efter frigivelsen? Ellers genindlæs fra kilde.
    try:
        gyldig = lag.isValid()
    except RuntimeError:
        gyldig = False
    if not gyldig:
        if not kilde or not os.path.exists(kilde.split('|', 1)[0]):
            feedback.reportError(
                f'{lagnavn}-laget blev frigivet og kan ikke genindlæses '
                f'(kilde: {kilde}).')
            return ''
        lag = QgsVectorLayer(kilde, lagnavn, 'ogr')
        if not lag.isValid():
            feedback.reportError(f'{lagnavn}: kunne ikke genindlæse fra {kilde}.')
            return ''
        feedback.pushInfo(f'{lagnavn}: genindlæst fra kilde efter frigivelse.')

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = 'GPKG'
    options.fileEncoding = 'UTF-8'
    error, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
        lag, maal_sti, QgsCoordinateTransformContext(), options)
    if error == QgsVectorFileWriter.NoError:
        feedback.pushInfo(f'{lagnavn} gemt: {maal_sti}')
        return maal_sti
    feedback.reportError(f'Kunne ikke gemme {lagnavn}: {error}')
    return ''


def _frigiv_gpkg_fil(projekt, gpkg_sti, feedback, forsog=6, pause_ms=250):
    """Fjern indlæste lag + luk GDAL/SQLite-forbindelser + slet GPKG-fil.

    Løser Windows-lås-problem hvor SQLite-forbindelse holdes i pool selv
    efter lag er fjernet fra projektet.
    """
    import gc
    import time

    sti_norm = os.path.normpath(gpkg_sti).lower()

    # 1) Fjern alle indlæste lag der bruger filen.
    # VIGTIGT: Vi kalder IKKE QgsApplication.processEvents() her. Denne funktion
    # kører INDE i en model-kørsel (processing.run), og at pumpe Qt's event-loop
    # midt i en processing-operation kan udløse en access violation / crash.
    lag_ids = []
    for lag in projekt.mapLayers().values():
        kilde = lag.source().split('|', 1)[0]
        if os.path.normpath(kilde).lower() == sti_norm:
            lag_ids.append(lag.id())
    if lag_ids:
        feedback.pushInfo(
            f'Fjerner {len(lag_ids)} indlæst(e) lag der bruger {os.path.basename(gpkg_sti)}'
        )
        try:
            projekt.removeMapLayers(lag_ids)
        except Exception:
            pass  # ikke kritisk

    # 2) Lad Python GC rydde op (uden at pumpe event-loopet).
    gc.collect()

    if not os.path.isfile(gpkg_sti):
        return

    # 3) Forsøg sletning via OGR (tvinger SQLite-forbindelser lukket)
    try:
        from osgeo import ogr, gdal
        # Invalider GDAL's interne dataset-cache
        try:
            gdal.GetDriverByName('GPKG')
        except Exception:
            pass
        drv = ogr.GetDriverByName('GPKG')
        if drv is not None:
            try:
                drv.DeleteDataSource(gpkg_sti)
            except Exception as e:
                feedback.pushInfo(f'OGR DeleteDataSource fejlede: {e}')
    except ImportError:
        pass

    if not os.path.isfile(gpkg_sti):
        return

    # 4) Retry os.remove med kort pause mellem forsøg.
    # Bemærk: ingen processEvents() — det er ikke sikkert under en model-kørsel.
    # gc.collect() + en kort pause er nok til at frigive de fleste fil-låse.
    for i in range(forsog):
        try:
            os.remove(gpkg_sti)
            feedback.pushInfo(f'{os.path.basename(gpkg_sti)} slettet ved forsøg {i + 1}.')
            return
        except PermissionError:
            gc.collect()
            time.sleep(pause_ms / 1000.0)
        except FileNotFoundError:
            return


class TerraenModelOutput(QgsProcessingAlgorithm):

    HOEJDEMODEL      = 'HOEJDEMODEL'
    CHANNEL_NETWORKS = 'CHANNEL_NETWORKS'
    DHMLINJE         = 'DHMLINJE'

    OUTPUT_HOEJDEMODEL      = 'OUTPUT_HOEJDEMODEL'
    OUTPUT_CHANNEL_NETWORKS = 'OUTPUT_CHANNEL_NETWORKS'
    OUTPUT_DHMLINJE         = 'OUTPUT_DHMLINJE'

    def name(self):
        return 'terraen_model_output'

    def displayName(self):
        return 'Terraen Model Output'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return TerraenModelOutput()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.HOEJDEMODEL,
            'Højdemodel'
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.CHANNEL_NETWORKS,
            'Channel Networks'
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.DHMLINJE,
            'DHMLinje (klippet og drappet)'
        ))
        self.addOutput(QgsProcessingOutputRasterLayer(
            self.OUTPUT_HOEJDEMODEL,
            'Hoejdemodel.sdat (gemt)'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.OUTPUT_CHANNEL_NETWORKS,
            'Channel_Networks.gpkg (gemt)'
        ))
        self.addOutput(QgsProcessingOutputVectorLayer(
            self.OUTPUT_DHMLINJE,
            'DHMLinje_Klippet.gpkg (gemt)'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        import time as _time
        _t0 = _time.perf_counter()
        output_mappe = find_output_filer()
        if not output_mappe:
            raise QgsProcessingException(
                'Outputfiler_<omraade>-mappen blev ikke fundet. '
                'Kør "Navngiv projekt" og "Tilføj dit projektområde (.shp)" i interfacet først.'
            )

        # Gem Højdemodel som GeoTIFF
        hoejdemodel_lag = self.parameterAsRasterLayer(parameters, self.HOEJDEMODEL, context)
        if hoejdemodel_lag is None:
            raise QgsProcessingException('Højdemodel-laget er ikke gyldigt.')

        hoejdemodel_sti = os.path.join(output_mappe, 'Hoejdemodel.sdat')

        # VIGTIGT: Læs lagets kilde-sti FØR vi fjerner lag fra projektet.
        # Hvis højdemodel-laget selv er indlæst i projektet (det peger på
        # Hoejdemodel.sdat i output-mappen), sletter removeMapLayers dets
        # underliggende C++-objekt — og et efterfølgende hoejdemodel_lag.source()
        # crasher så med "wrapped C/C++ object … has been deleted".
        from osgeo import gdal
        kilde_sti = hoejdemodel_lag.source().split('|', 1)[0]

        # Fjern evt. indlæste lag der bruger denne fil
        projekt = QgsProject.instance()
        for ext in ('.sdat', '.tif'):
            sti_check = os.path.normpath(os.path.join(output_mappe, 'Hoejdemodel' + ext))
            lag_der_skal_fjernes = [
                lag.id() for lag in projekt.mapLayers().values()
                if os.path.normpath(lag.source()) == sti_check
            ]
            if lag_der_skal_fjernes:
                try:
                    projekt.removeMapLayers(lag_der_skal_fjernes)
                except Exception:
                    pass  # ikke kritisk — fortsæt selv hvis lag ikke kan fjernes

        # Gem som SAGA via et DIREKTE GDAL-kald i stedet for et nested
        # processing.run('gdal:translate'). Et nested processing.run INDE i en
        # model-kørsel er ustabilt i denne QGIS-version og kan udløse en access
        # violation. gdal.Translate gør præcis det samme uden at re-entrere
        # QGIS' processing-framework.

        if os.path.normpath(kilde_sti).lower() == os.path.normpath(hoejdemodel_sti).lower():
            # Kilde og mål er SAMME fil — laget ligger allerede på den rigtige
            # sti i SAGA-format. Der er intet at konvertere.
            feedback.pushInfo(f'Hoejdemodel ligger allerede på plads: {hoejdemodel_sti}')
        else:
            # Skriv til en midlertidig fil og flyt den på plads, så vi aldrig
            # læser og skriver til samme datasæt (GDAL afviser det).
            import tempfile, shutil
            tmpdir = tempfile.mkdtemp(prefix='dhm_save_')
            tmp = os.path.join(tmpdir, 'Hoejdemodel.sdat')
            try:
                res = gdal.Translate(tmp, kilde_sti, format='SAGA')
                res = None
                if not os.path.isfile(tmp):
                    raise QgsProcessingException(
                        f'Højdemodel kunne ikke konverteres til SAGA: {kilde_sti}')
                # Flyt alle SAGA-sidefiler (.sdat/.sgrd/.prj/.sdat.aux.xml) på plads.
                tmp_rod = os.path.splitext(tmp)[0]
                maal_rod = os.path.splitext(hoejdemodel_sti)[0]
                for e in ('.sdat', '.sgrd', '.prj', '.sdat.aux.xml', '.mgrd'):
                    if os.path.isfile(tmp_rod + e):
                        shutil.copy2(tmp_rod + e, maal_rod + e)
            except QgsProcessingException:
                raise
            except Exception as e:
                raise QgsProcessingException(
                    f'Kunne ikke gemme højdemodel som SAGA:\n{e}')
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            if not os.path.isfile(hoejdemodel_sti):
                raise QgsProcessingException(
                    f'Højdemodel blev ikke gemt: {hoejdemodel_sti}')
            feedback.pushInfo(f'Hoejdemodel gemt: {hoejdemodel_sti}')

        # Gem Channel Networks som GeoPackage
        channel_networks_lag = self.parameterAsVectorLayer(parameters, self.CHANNEL_NETWORKS, context)
        channel_sti = os.path.join(output_mappe, 'Channel_Networks.gpkg')

        if channel_networks_lag is not None:
            channel_sti = _gem_vektor_sikkert(
                channel_networks_lag, channel_sti, projekt, feedback,
                'Channel_Networks')
        else:
            feedback.reportError('Channel_Networks-laget er ikke gyldigt — springer over.')
            channel_sti = ''

        # Gem DHMLinje (klippet + drappet) som GeoPackage
        dhmlinje_lag = self.parameterAsVectorLayer(parameters, self.DHMLINJE, context)
        dhmlinje_sti = os.path.join(output_mappe, 'DHMLinje_Klippet.gpkg')

        if dhmlinje_lag is not None:
            dhmlinje_sti = _gem_vektor_sikkert(
                dhmlinje_lag, dhmlinje_sti, projekt, feedback, 'DHMLinje_Klippet')
        else:
            feedback.reportError('DHMLinje-laget er ikke gyldigt — springer over.')
            dhmlinje_sti = ''

        feedback.pushInfo(
            f'[TID] Gem-trin (SAGA-DHM + GPKG) på '
            f'{_time.perf_counter() - _t0:.1f} s.')

        return {
            self.OUTPUT_HOEJDEMODEL:      hoejdemodel_sti,
            self.OUTPUT_CHANNEL_NETWORKS: channel_sti,
            self.OUTPUT_DHMLINJE:         dhmlinje_sti,
        }
