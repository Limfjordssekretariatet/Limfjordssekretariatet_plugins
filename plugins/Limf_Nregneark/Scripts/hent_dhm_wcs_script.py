from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterRasterDestination,
    QgsProcessingException,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsVectorLayer,
    QgsGeometry,
    QgsFeatureRequest,
)
import os
import tempfile
from osgeo import gdal, ogr

# Sørg for at scriptets egen mappe er på sys.path, så 'utils' kan importeres.
import os as _os, sys as _sys
try:
    _d = _os.path.dirname(_os.path.abspath(__file__))
    if _d and _d not in _sys.path:
        _sys.path.insert(0, _d)
except NameError:
    pass

# ── Indstillinger ─────────────────────────────────────────────────────────────
WCS_URL  = 'https://api.dataforsyningen.dk/dhm_wcs_DAF'
COVERAGE = 'dhm_terraen'
CRS      = 'EPSG:25832'
RESOLUT  = 0.4   # meter pr. pixel
# WCS-serveren afviser kald hvor resultatet er > 10.000 px pr. side. Vi holder
# os under en sikker grænse og deler større områder op i felter (tiling).
MAX_PX   = 9000  # maks pixels pr. side pr. WCS-kald (sikkerhedsmargin under 10.000)

# Oplands-afgrænsning: download-området begrænses til de oplande projektområdet
# rører, så man ikke henter terræn langt uden for det relevante vandopland.
OPLANDE_REL = os.path.join('Oplande', 'oplande_1orden_region.shp')  # i Grunddata/
OPLAND_MARGIN = 500   # m: tillad op til 500 m uden for oplandsgrænsen (kan være unøjagtig)

# Stream-burning: de rigtige vandløb (Vandloeb) brændes ned i DHM'en, så de
# beregnede strømningsveje TVINGES til at følge de faktiske vandløb i stedet for
# terræn-baserede (og i fladt vådområde-terræn ofte forkerte) forløb. DHMLinje
# (rør/broer/sluser) brændes stadig ovenpå i modellen, så vandet kan gå under veje.
VANDLOEB_REL = os.path.join('Vandloeb', 'Vandloeb_DK.shp')  # i Grunddata/
BURN_DYBDE   = 3.0   # m: hvor dybt vandløbene sænkes i DHM'en (nok til at dominere
                     # sub-meter terrænstøj i fladt terræn, men ikke skabe store artefakter)
# ─────────────────────────────────────────────────────────────────────────────


class HentDHMWCS(QgsProcessingAlgorithm):

    INPUT   = 'INPUT'
    TOKEN   = 'TOKEN'
    RES     = 'RESOLUTION'
    RAA     = 'SPRING_BRAENDING_OVER'
    OUTPUT  = 'OUTPUT'

    def name(self):
        return 'hent_dhm_wcs'

    def displayName(self):
        return 'Hent DHM Terræn (WCS)'

    def group(self):
        return 'DMI Grid'

    def groupId(self):
        return 'dmigrid'

    def createInstance(self):
        return HentDHMWCS()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                'Bufferzone (til afgrænsning af download)',
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.TOKEN,
                'Kortforsyningen token (ikke nødvendig med en valgt højdemodel)',
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RES,
                'Opløsning (m/pixel) — 0.4 er max, 1–2 m anbefales til store områder',
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.4,
                maxValue=10.0,
            )
        )
        # Stream-burning hører til den gamle strømningsvejs-model. Oplandsmodellen
        # konditionerer selv (tilpasningskoter, monoton brænding, breaching, fill) og
        # skal have RÅT terræn — brændes det to gange, ligger renderne 4 m dybt og
        # breaching har intet at rette op på.
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.RAA,
                'Spring stream-burning over (leverer råt terræn)',
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT,
                'DHM Terræn (rå)',
            )
        )

    @staticmethod
    def _valgt_hoejdemodel():
        """Returnerer stien til en brugervalgt højdemodel for det aktive
        projektområde (sat via interfacets 'Vælg højdemodel'-knap), eller None.
        Slår op pr. område, så hvert projektområde kan have sin egen."""
        from qgis.core import QgsSettings
        s = QgsSettings()
        omraade = s.value('vaadomraade_modeller/projektomraade_navn', '')
        if omraade:
            sti = s.value(f'vaadomraade_modeller/valgt_hoejdemodel/{omraade}', '')
            if sti and os.path.isfile(sti):
                return sti
        return None

    def _efterbehandl(self, output, raa, feedback):
        """Gemmer det rå terræn og brænder vandløbene — medmindre der er bedt om råt.

        raa=True betyder at kalderen konditionerer selv (oplandsmodellen gør det), og
        at `output` derfor ER den rå terrænmodel. Så er der hverken noget at kopiere
        eller noget at brænde.
        """
        if raa:
            feedback.pushInfo(
                'Stream-burning sprunget over — output er råt terræn, som kalderen '
                'selv konditionerer.')
            return
        self._gem_raa_dhm(output, feedback)
        self._braend_vandloeb(output, feedback)

    def _gem_raa_dhm(self, dhm_sti, feedback):
        """Gemmer en kopi af den RÅ højdemodel i Outputfiler_<omraade> som DHM_raa.tif.

        Kaldes FØR stream-burning. Oplandsmodellen ('Udpeg oplande') laver sin egen
        hydrologiske konditionering — tilpasningskoter, brænding med monotont fald,
        breaching og fill til sidst — og skal have råt terræn for at kunne det. Får
        den i stedet den færdige Hoejdemodel, er lavningerne allerede fyldt af
        fillsinkswangliu, og fyldt terræn kan ikke gendannes: oplandsgrænserne
        følger så kunstige plateauer med vilkårlig strømningsretning.

        Fejler blødt — kan kopien ikke skrives, kører resten af modellen uændret.
        """
        try:
            from utils import find_output_filer
            output_mappe = find_output_filer()
        except Exception:
            output_mappe = None
        if not output_mappe:
            feedback.pushInfo(
                'Outputfiler-mappen ikke fundet — gemmer ikke DHM_raa.tif.')
            return

        maal = os.path.join(output_mappe, 'DHM_raa.tif')
        if os.path.normpath(maal).lower() == os.path.normpath(dhm_sti).lower():
            return   # output ligger allerede på den rigtige sti
        try:
            kilde = gdal.Open(dhm_sti)
            if kilde is None:
                feedback.pushInfo('Kunne ikke åbne DHM til rå kopi — springer over.')
                return
            # Komprimeret kopi: den ligger side om side med Hoejdemodel, og
            # float32-terræn i 0,4 m bliver hurtigt hundreder af MB.
            resultat = gdal.Translate(
                maal, kilde, format='GTiff',
                creationOptions=['COMPRESS=DEFLATE', 'TILED=YES', 'PREDICTOR=3'])
            kilde = None
            resultat = None
            feedback.pushInfo(f'Rå højdemodel gemt til oplandsmodellen: {maal}')
        except Exception as e:
            feedback.pushInfo(
                f'Kunne ikke gemme DHM_raa.tif ({e}) — springer over. '
                'Oplandsmodellen falder tilbage på Hoejdemodel.')

    def _braend_vandloeb(self, dhm_sti, feedback):
        """Brænder de rigtige vandløb (Vandloeb) ned i DHM'en, så de beregnede
        strømningsveje følger de faktiske vandløb.

        Metode (klassisk stream-burning): rasterisér vandløbs-polygonerne til
        DHM'ens NØJAGTIGE grid (samme extent/opløsning/celle-placering), og sænk
        terrænet BURN_DYBDE meter i de celler der er vandløb. I fladt vådområde-
        terræn dominerer denne rende sub-meter terrænstøj, så SAGA's flow-
        beregning tvinges ned i vandløbene. DHMLinje-culverts brændes stadig
        ovenpå i modellen, så segmenterne forbindes under veje.

        Fejler blødt (springer over) hvis Vandloeb-filen mangler."""
        try:
            from utils import find_grunddata
            grunddata = find_grunddata()
        except Exception:
            grunddata = None
        if not grunddata:
            feedback.pushInfo('Grunddata ikke fundet — springer stream-burning over.')
            return
        vandloeb_sti = os.path.join(grunddata, VANDLOEB_REL)
        if not os.path.isfile(vandloeb_sti):
            feedback.pushInfo(
                f'Vandløb-fil ikke fundet ({vandloeb_sti}) — springer stream-burning over.')
            return

        ds = gdal.Open(dhm_sti, gdal.GA_Update)
        if ds is None:
            feedback.pushInfo('Kunne ikke åbne DHM til stream-burning — springer over.')
            return
        try:
            gt = ds.GetGeoTransform()
            xsize, ysize = ds.RasterXSize, ds.RasterYSize
            band = ds.GetRasterBand(1)
            nodata = band.GetNoDataValue()

            # DHM'ens udstrækning (til at spatial-filtrere vandløbene → hurtigt).
            minx = gt[0]
            maxy = gt[3]
            maxx = minx + gt[1] * xsize
            miny = maxy + gt[5] * ysize

            vsrc = ogr.Open(vandloeb_sti)
            if vsrc is None:
                feedback.pushInfo('Kunne ikke åbne vandløbs-filen — springer over.')
                return
            vlyr = vsrc.GetLayer(0)
            vlyr.SetSpatialFilterRect(min(minx, maxx), min(miny, maxy),
                                      max(minx, maxx), max(miny, maxy))
            n_vandloeb = vlyr.GetFeatureCount()
            if not n_vandloeb:
                feedback.pushInfo('Ingen vandløb i området — springer stream-burning over.')
                return

            # Rasterisér vandløb til en maske på DHM'ens NØJAGTIGE grid.
            mask_ds = gdal.GetDriverByName('MEM').Create('', xsize, ysize, 1, gdal.GDT_Byte)
            mask_ds.SetGeoTransform(gt)
            mask_ds.SetProjection(ds.GetProjection())
            gdal.RasterizeLayer(mask_ds, [1], vlyr, burn_values=[1])
            mask = mask_ds.GetRasterBand(1).ReadAsArray()

            arr = band.ReadAsArray()
            braend = (mask == 1)
            if nodata is not None:
                braend &= (arr != nodata)
            antal = int(braend.sum())
            if antal:
                arr[braend] = arr[braend] - BURN_DYBDE
                band.WriteArray(arr)
                ds.FlushCache()
                feedback.pushInfo(
                    f'Stream-burning: {n_vandloeb} vandløb sænket {BURN_DYBDE} m '
                    f'i {antal} celler.')
            else:
                feedback.pushInfo('Vandløb ramte ingen DHM-celler — intet brændt.')
        finally:
            ds = None

    def _klip_til_oplande(self, buffer_geom, feedback):
        """Begrænser download-området til de oplande som bufferzonen rører.

        Returnerer en geometri = buffer_geom ∩ buffer(berørte_oplande, MARGIN).
        Så hentes der op til bufferzonens udstrækning, men aldrig mere end
        OPLAND_MARGIN meter uden for oplandsgrænsen. Oplands-laget forventes i
        EPSG:25832 (samme som buffer_geom).

        Returnerer None hvis oplands-filen mangler eller intet opland rører
        bufferzonen — så falder kalderen tilbage til den uklippede buffer."""
        from utils import find_grunddata
        grunddata = find_grunddata()
        if not grunddata:
            feedback.pushInfo('Grunddata-mappen ikke fundet — springer opland-klip over.')
            return None
        opland_sti = os.path.join(grunddata, OPLANDE_REL)
        if not os.path.isfile(opland_sti):
            feedback.pushInfo(f'Opland-fil ikke fundet ({opland_sti}) — springer opland-klip over.')
            return None

        lag = QgsVectorLayer(opland_sti, 'oplande', 'ogr')
        if not lag.isValid():
            feedback.pushInfo('Opland-laget kunne ikke indlæses — springer opland-klip over.')
            return None

        # Find oplande der overlapper bufferzonen (hurtigt via bounding-box-filter).
        bbox = buffer_geom.boundingBox()
        req = QgsFeatureRequest().setFilterRect(bbox)
        roerte = []
        for f in lag.getFeatures(req):
            g = f.geometry()
            if g and not g.isEmpty() and g.intersects(buffer_geom):
                roerte.append(g)
        if not roerte:
            feedback.pushInfo('Ingen oplande rører projektområdet — bruger fuld buffer.')
            return None

        # Slå de berørte oplande sammen og buffer dem med MARGIN.
        samlet = QgsGeometry.unaryUnion(roerte)
        if samlet.isEmpty():
            return None
        opland_med_margin = samlet.buffer(OPLAND_MARGIN, 8)

        # Skær bufferzonen ned til oplande+margin.
        klippet = buffer_geom.intersection(opland_med_margin)
        if klippet.isEmpty():
            feedback.pushInfo('Skæring mellem buffer og oplande blev tom — bruger fuld buffer.')
            return None
        feedback.pushInfo(
            f'Download-område begrænset til {len(roerte)} berørt(e) opland(e) '
            f'(+{OPLAND_MARGIN} m).')
        return klippet

    @staticmethod
    def _aaben_wcs(token):
        """Åbner WCS-servicen via GDAL og returnerer datasættet (eller None)."""
        wcs_xml = (
            f'<WCS_GDAL>'
            f'<ServiceURL>{WCS_URL}?token={token}&amp;</ServiceURL>'
            f'<CoverageName>{COVERAGE}</CoverageName>'
            f'<Version>1.0.0</Version>'
            # Længere timeout, så serveren får tid til at svare på store felter.
            f'<Timeout>300</Timeout>'
            f'</WCS_GDAL>'
        )
        vsi_path = '/vsimem/dhm_wcs.xml'
        gdal.FileFromMemBuffer(vsi_path, wcs_xml.encode('utf-8'))
        ds = gdal.Open(vsi_path)
        gdal.Unlink(vsi_path)
        return ds

    def _hent_felt(self, token, output, minx, miny, maxx, maxy, res,
                   feedback, forsoeg=3):
        """Henter ét rektangel fra WCS til 'output' med automatisk genprøvning
        ved timeout/504. Returnerer True ved succes."""
        import time
        for n in range(1, forsoeg + 1):
            if feedback.isCanceled():
                return False
            ds = self._aaben_wcs(token)
            if ds is None:
                feedback.pushInfo(f'  Kunne ikke åbne WCS (forsøg {n}/{forsoeg})…')
                time.sleep(3)
                continue
            try:
                result = gdal.Translate(
                    output, ds,
                    projWin=[minx, maxy, maxx, miny],
                    projWinSRS=CRS,
                    xRes=res, yRes=res,
                    format='GTiff',
                )
                ds = None
                if result is not None:
                    result = None
                    return True
            except RuntimeError as e:
                ds = None
                besked = str(e)
                lav = besked.lower()
                if '504' in besked or 'timed out' in lav or 'timeout' in lav:
                    feedback.pushInfo(
                        f'  Server-timeout (forsøg {n}/{forsoeg}) — prøver igen…')
                    time.sleep(5)
                    continue
                # Området er for stort til ét WCS-kald (serveren tillader maks
                # 10.000 px pr. side). Det løses ved at dele området op (tiling),
                # så vi returnerer False og lader kalderen falde tilbage til det.
                if 'maxsize' in lav or 'no more than' in lav or \
                        ('width' in lav and 'height' in lav):
                    feedback.pushInfo(
                        '  Området er for stort til ét kald (server-grænse) — '
                        'deler det op…')
                    return False
                raise  # anden fejl — videresend
        return False

    def _hent_med_tiling(self, token, output, minx, miny, maxx, maxy, res,
                         feedback, antal=2):
        """Fallback: deler området i antal×antal felter, henter hvert med
        genprøvning og merger dem til 'output'. Returnerer True ved succes."""
        import tempfile
        feedback.pushInfo(
            f'Deler området i {antal}×{antal} felter for at undgå timeout…')
        dx = (maxx - minx) / antal
        dy = (maxy - miny) / antal
        tmpdir = tempfile.mkdtemp(prefix='dhm_tiles_')
        tiles = []
        try:
            i = 0
            for r in range(antal):
                for c in range(antal):
                    if feedback.isCanceled():
                        return False
                    i += 1
                    tx0 = minx + c * dx
                    tx1 = minx + (c + 1) * dx
                    ty0 = miny + r * dy
                    ty1 = miny + (r + 1) * dy
                    tile = os.path.join(tmpdir, f'tile_{r}_{c}.tif')
                    feedback.pushInfo(f'  Felt {i}/{antal*antal}…')
                    if not self._hent_felt(token, tile, tx0, ty0, tx1, ty1,
                                           res, feedback):
                        return False
                    tiles.append(tile)
                    feedback.setProgress(10 + int(i / (antal*antal) * 80))
            # Merge alle felter til output
            feedback.pushInfo('Samler felterne…')
            vrt = os.path.join(tmpdir, 'merged.vrt')
            gdal.BuildVRT(vrt, tiles)
            gdal.Translate(output, vrt, format='GTiff')
            return os.path.isfile(output)
        finally:
            # Ryd midlertidige filer
            try:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    def processAlgorithm(self, parameters, context, feedback):
        import time as _time
        _t0 = _time.perf_counter()
        lag    = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        token  = self.parameterAsString(parameters, self.TOKEN, context)
        res    = self.parameterAsDouble(parameters, self.RES, context)
        raa    = self.parameterAsBool(parameters, self.RAA, context)
        output = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

        if lag is None:
            raise QgsProcessingException('Bufferzonen kunne ikke indlæses.')

        # ── Hent extent og reprojektér til DHM-data'ets CRS (EPSG:25832) ──────
        # Projektområdet kan være i et hvilket som helst CRS (fx WGS84 grader).
        # DHM-WCS leverer i EPSG:25832 (meter), og vi henter med projWinSRS=CRS,
        # så extent SKAL være i meter i 25832 — ellers bliver området forkert
        # (eller tomt). Vi transformerer derfor lagets extent til 25832.
        ml_crs = lag.crs()
        dhm_crs = QgsCoordinateReferenceSystem(CRS)
        tr = None
        if ml_crs.isValid() and ml_crs != dhm_crs:
            tr = QgsCoordinateTransform(ml_crs, dhm_crs, QgsProject.instance())

        # Saml bufferzonens geometri (alle features) og reprojektér til 25832,
        # så vi kan klippe den mod oplandene (som er i 25832).
        buf_geoms = []
        for f in lag.getFeatures():
            g = f.geometry()
            if g and not g.isEmpty():
                if tr is not None:
                    g = QgsGeometry(g)
                    if g.transform(tr) != 0:
                        continue
                buf_geoms.append(g)
        if not buf_geoms:
            raise QgsProcessingException('Bufferzonen har ingen geometri.')
        buffer_geom = QgsGeometry.unaryUnion(buf_geoms)
        if tr is not None:
            feedback.pushInfo(
                f'Projektområde er i {ml_crs.authid()} — omregnet til {CRS}.')

        # Begræns til de oplande projektområdet rører (+margin). Hvis det ikke
        # kan lade sig gøre, bruges den fulde bufferzone.
        klippet = self._klip_til_oplande(buffer_geom, feedback)
        if klippet is not None and not klippet.isEmpty():
            buffer_geom = klippet

        ext = buffer_geom.boundingBox()
        minx = ext.xMinimum()
        miny = ext.yMinimum()
        maxx = ext.xMaximum()
        maxy = ext.yMaximum()

        # ── Har brugeren valgt en eksisterende højdemodel? ────────────────────
        # Hvis ja, springes WCS-downloaden over: vi klipper den valgte DHM til
        # bufferzonens extent og bruger den. Resten af modellen (fill sinks,
        # channel networks) køres uændret oven på resultatet.
        valgt_dhm = self._valgt_hoejdemodel()
        if not valgt_dhm and not token:
            raise QgsProcessingException(
                'Der er hverken et Dataforsyningen-token eller en valgt '
                'højdemodel — så er der ingen måde at skaffe terrænet på.'
                + chr(10) +
                'Kør trinnet fra interfacet (det spørger om token) eller vælg '
                '"Brug lokal højdemodel".')
        if valgt_dhm:
            feedback.pushInfo(f'Bruger valgt højdemodel (springer WCS over): {valgt_dhm}')
            kilde_ds = gdal.Open(valgt_dhm)
            if kilde_ds is None:
                raise QgsProcessingException(
                    f'Den valgte højdemodel kunne ikke åbnes:\n{valgt_dhm}')
            result = gdal.Translate(
                output, kilde_ds,
                projWin=[minx, maxy, maxx, miny],
                projWinSRS=CRS,
                xRes=res, yRes=res,
                format='GTiff',
            )
            kilde_ds = None
            if result is None:
                raise QgsProcessingException(
                    'Kunne ikke klippe den valgte højdemodel til projektområdet. '
                    'Tjek at højdemodellen dækker området og er i EPSG:25832.')
            result = None
            self._efterbehandl(output, raa, feedback)
            feedback.setProgress(100)
            feedback.pushInfo(
                f'[TID] Lokal DHM klippet+resamplet til {res} m på '
                f'{_time.perf_counter() - _t0:.1f} s.')
            feedback.pushInfo(f'Højdemodel (valgt) klargjort: {output}')
            return {self.OUTPUT: output}

        bredde = maxx - minx
        hoejde = maxy - miny
        feedback.pushInfo(
            f'Extent: {minx:.1f}, {miny:.1f}, {maxx:.1f}, {maxy:.1f} '
            f'({bredde/1000:.1f} x {hoejde/1000:.1f} km)'
        )
        feedback.pushInfo(
            f'Opløsning: {res} m/pixel → '
            f'{round(bredde/res)} x {round(hoejde/res)} px'
        )

        # ── Hent terræn med robust strategi ───────────────────────────────────
        # Serveren afviser kald > 10.000 px/side. Vi beregner derfor på forhånd
        # hvor mange felter (tiling) området kræver, så hvert kald er lille nok.
        # Derudover gives genprøvning ved timeout/504, og opdelingen optrappes
        # hvis serveren stadig timer ud.
        import math
        px_bredde = bredde / res
        px_hoejde = hoejde / res
        n_min = max(1, math.ceil(max(px_bredde, px_hoejde) / MAX_PX))

        feedback.pushInfo('Henter terræn fra Dataforsyningen (WCS)…')
        feedback.setProgress(10)

        ok = False
        if n_min <= 1:
            # Området er lille nok til ét samlet kald.
            ok = self._hent_felt(token, output, minx, miny, maxx, maxy, res,
                                 feedback)
        else:
            feedback.pushInfo(
                f'Området er stort ({round(px_bredde)}×{round(px_hoejde)} px) — '
                f'henter i {n_min}×{n_min} felter.')

        # Hvis ét kald ikke lykkedes (eller området var for stort fra start),
        # del op. Optrapp opdelingen indtil det lykkes (eller for mange felter).
        if not ok and not feedback.isCanceled():
            for antal in (n_min, n_min + 1, n_min + 2):
                if antal < 2:
                    antal = 2
                ok = self._hent_med_tiling(token, output, minx, miny, maxx, maxy,
                                           res, feedback, antal=antal)
                if ok or feedback.isCanceled():
                    break

        if feedback.isCanceled():
            return {}

        if not ok or not os.path.isfile(output):
            raise QgsProcessingException(
                'Kunne ikke hente højdemodellen fra Dataforsyningen — serveren '
                'svarede ikke i tide (timeout/504) eller området var for stort, '
                'heller ikke ved opdeling.\n\n'
                'Prøv igen om lidt (serveren kan være midlertidigt overbelastet), '
                'vælg et mindre projektområde, eller download højdemodellen '
                'manuelt og brug "Brug lokal højdemodel".'
            )

        self._efterbehandl(output, raa, feedback)
        feedback.setProgress(100)
        feedback.pushInfo(
            f'[TID] DHM downloadet ({res} m) på '
            f'{_time.perf_counter() - _t0:.1f} s.')
        feedback.pushInfo(f'DHM gemt: {output}')

        return {self.OUTPUT: output}
