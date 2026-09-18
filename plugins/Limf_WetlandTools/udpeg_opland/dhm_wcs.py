# -*- coding: utf-8 -*-
"""Henter DHM/Terræn fra Dataforsyningen.

Løsrevet udgave af hentetrinnet fra N-regnearkspluginnet: samme WCS-kald,
samme genprøvning og samme opdeling i felter, men uden bindingen til det
plugins mappestruktur. Her returneres bare en GeoTIFF på den sti man beder om.

Terrænet leveres RÅT. Oplandsmodellen konditionerer selv, og et terræn der
allerede er brændt og fyldt, kan ikke gøres råt igen.
"""
import math
import os
import tempfile
import time

from osgeo import gdal
from qgis.core import QgsProcessingException

WCS_URL = 'https://api.dataforsyningen.dk/dhm_wcs_DAF'
COVERAGE = 'dhm_terraen'
CRS = 'EPSG:25832'

# WCS-serveren afviser kald hvor resultatet er over 10.000 px pr. side. Der
# holdes en margin under, saa en afrunding ikke vaelter kaldet.
MAKS_PX = 9000


def _aaben_wcs(token):
    """Aabner WCS-servicen via GDAL og returnerer datasaettet, eller None."""
    xml = (
        '<WCS_GDAL>'
        f'<ServiceURL>{WCS_URL}?token={token}&amp;</ServiceURL>'
        f'<CoverageName>{COVERAGE}</CoverageName>'
        '<Version>1.0.0</Version>'
        # Laengere timeout, saa serveren faar tid til at svare paa store felter.
        '<Timeout>300</Timeout>'
        '</WCS_GDAL>'
    )
    vsi = '/vsimem/udpeg_opland_wcs.xml'
    gdal.FileFromMemBuffer(vsi, xml.encode('utf-8'))
    ds = gdal.Open(vsi)
    gdal.Unlink(vsi)
    return ds


def _hent_felt(token, ud, minx, miny, maxx, maxy, res, feedback, forsoeg=3):
    """Henter ét rektangel. Sandt ved held.

    Falsk betyder "prøv med mindre felter" — enten fordi serveren løb tør for
    tid, eller fordi den afviste feltet som for stort. Andre fejl sendes videre;
    en tavs fejl her ville give et hul i terrænet, og et hul i terrænet giver et
    opland der ser rigtigt ud og er forkert.
    """
    for n in range(1, forsoeg + 1):
        if feedback.isCanceled():
            return False
        ds = _aaben_wcs(token)
        if ds is None:
            feedback.pushInfo(f'  kunne ikke aabne WCS (forsøg {n}/{forsoeg}) …')
            time.sleep(3)
            continue
        try:
            svar = gdal.Translate(ud, ds, projWin=[minx, maxy, maxx, miny],
                                  projWinSRS=CRS, xRes=res, yRes=res,
                                  format='GTiff')
            ds = None
            if svar is not None:
                svar = None
                return True
        except RuntimeError as e:
            ds = None
            besked = str(e)
            lav = besked.lower()
            if '504' in besked or 'timed out' in lav or 'timeout' in lav:
                feedback.pushInfo(
                    f'  server-timeout (forsøg {n}/{forsoeg}) — prøver igen …')
                time.sleep(5)
                continue
            if ('maxsize' in lav or 'no more than' in lav
                    or ('width' in lav and 'height' in lav)):
                feedback.pushInfo('  feltet er for stort for serveren — deles op …')
                return False
            raise
    return False


def _hent_i_felter(token, ud, minx, miny, maxx, maxy, res, feedback, antal=2):
    """Deler omraadet i antal x antal felter og samler dem bagefter."""
    feedback.pushInfo(f'Deler omraadet i {antal}×{antal} felter …')
    dx = (maxx - minx) / antal
    dy = (maxy - miny) / antal
    mappe = tempfile.mkdtemp(prefix='udpeg_opland_dhm_')
    felter = []
    try:
        i = 0
        for r in range(antal):
            for c in range(antal):
                if feedback.isCanceled():
                    return False
                i += 1
                felt = os.path.join(mappe, f'felt_{r}_{c}.tif')
                feedback.pushInfo(f'  felt {i}/{antal * antal} …')
                if not _hent_felt(token, felt,
                                  minx + c * dx, miny + r * dy,
                                  minx + (c + 1) * dx, miny + (r + 1) * dy,
                                  res, feedback):
                    return False
                felter.append(felt)
                feedback.setProgress(5 + int(i / (antal * antal) * 25))
        feedback.pushInfo('Samler felterne …')
        vrt = os.path.join(mappe, 'samlet.vrt')
        gdal.BuildVRT(vrt, felter)
        gdal.Translate(ud, vrt, format='GTiff')
        return os.path.isfile(ud)
    finally:
        import shutil
        shutil.rmtree(mappe, ignore_errors=True)


def hent(token, ud, minx, miny, maxx, maxy, res, feedback):
    """Henter terraenet for rektanglet til 'ud'. Rejser hvis det ikke lykkes."""
    if not token:
        raise QgsProcessingException(
            'Der er hverken et præberegnet grundlag der dækker området, en '
            'udpeget højdemodel eller et Dataforsyningen-token — så er der '
            'ingen måde at skaffe terrænet på.\n\n'
            'Hent et token på dataforsyningen.dk (det er gratis) og skriv det i '
            'feltet, eller peg på en højdemodel du har i forvejen.')

    px_bredde = (maxx - minx) / res
    px_hoejde = (maxy - miny) / res
    mindst = max(1, math.ceil(max(px_bredde, px_hoejde) / MAKS_PX))

    feedback.pushInfo(
        f'Henter terræn fra Dataforsyningen: '
        f'{(maxx - minx) / 1000:.1f} × {(maxy - miny) / 1000:.1f} km i {res:g} m '
        f'({round(px_bredde)} × {round(px_hoejde)} px) …')

    ok = False
    if mindst <= 1:
        ok = _hent_felt(token, ud, minx, miny, maxx, maxy, res, feedback)
    else:
        feedback.pushInfo(f'  omraadet er stort — hentes i {mindst}×{mindst} felter')

    if not ok and not feedback.isCanceled():
        for antal in (mindst, mindst + 1, mindst + 2):
            ok = _hent_i_felter(token, ud, minx, miny, maxx, maxy, res, feedback,
                                antal=max(2, antal))
            if ok or feedback.isCanceled():
                break

    if feedback.isCanceled():
        return None
    if not ok or not os.path.isfile(ud):
        raise QgsProcessingException(
            'Terrænet kunne ikke hentes fra Dataforsyningen — serveren svarede '
            'ikke i tide, heller ikke da området blev delt op.\n\n'
            'Prøv igen om lidt, vælg et mindre område, eller hent højdemodellen '
            'manuelt og peg på den.')
    return ud
