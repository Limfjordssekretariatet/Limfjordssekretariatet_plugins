"""Henter terrænkoter fra DHM (Danmarks Højdemodel) via WCS.

Slår hver punkts terrænkote (Z) op fra DHM-terrænmodellen. Punkterne er
allerede i EPSG:25832 (samme som DHM'en), så ingen reprojektion er nødvendig.

WCS'en afviser store udtræk og rate-limiter mange hurtige kald, så DHM hentes
i mindre fliser (tiles): punkterne fordeles i et gitter, og kun de fliser der
faktisk indeholder punkter hentes — ét raster ad gangen, med en lille pause.
"""

import os
import time
import tempfile
import urllib.request
import urllib.parse
import urllib.error

from .. import config


class DhmError(Exception):
    """Rejses ved fejl i DHM-hentning eller -opslag."""


# HTTP-statuskoder der typisk er forbigående og kan forsøges igen.
_TRANSIENT_STATUS = (502, 503, 504, 429)


def _http_get_with_retry(url):
    """Hent en URL og returnér rå bytes, med genforsøg ved forbigående fejl.

    Forsøger igen ved 502/503/504/429 og timeout/forbindelsesfejl, med
    fordoblet ventetid mellem forsøg. Rejser DhmError, hvis alle forsøg
    fejler.
    """
    last_err = None
    for attempt in range(config.DHM_MAX_RETRIES):
        if attempt > 0:
            time.sleep(config.DHM_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
        try:
            with urllib.request.urlopen(
                    url, timeout=config.DHM_HTTP_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_err = "HTTP %s: %s" % (exc.code, exc.reason)
            if exc.code not in _TRANSIENT_STATUS:
                raise DhmError("Kunne ikke hente DHM fra WCS:\n%s" % last_err)
            # ellers: forbigående — prøv igen
        except (urllib.error.URLError, OSError) as exc:
            # Timeout og forbindelsesfejl er typisk forbigående.
            last_err = str(getattr(exc, "reason", exc))
    raise DhmError(
        "Kunne ikke hente DHM fra WCS efter %d forsøg.\nSidste fejl: %s\n\n"
        "Serveren er muligvis midlertidigt overbelastet — prøv igen om lidt."
        % (config.DHM_MAX_RETRIES, last_err))


def _download_tile(minx, miny, maxx, maxy):
    """Hent DHM-terrænmodellen for én flise som midlertidig GeoTIFF.

    Flisen er afrundet til hele pixels. Returnerer stien (kalderen sletter).
    """
    res = config.DHM_RESOLUTION
    width = max(1, int(round((maxx - minx) / res)))
    height = max(1, int(round((maxy - miny) / res)))

    params = {
        "service": "WCS",
        "version": "1.0.0",
        "request": "GetCoverage",
        "coverage": config.DHM_COVERAGE,
        "format": "GTiff",
        "crs": "epsg:%d" % config.DHM_EPSG,
        "bbox": "%f,%f,%f,%f" % (minx, miny, maxx, maxy),
        "width": str(width),
        "height": str(height),
        "token": config.DHM_WCS_TOKEN,
    }
    url = config.DHM_WCS_BASE + "?" + urllib.parse.urlencode(params)

    fd, path = tempfile.mkstemp(suffix=".tif", prefix="vasp_dhm_")
    os.close(fd)
    try:
        data = _http_get_with_retry(url)
    except DhmError:
        os.remove(path)
        raise

    # WCS-fejl kommer som XML i stedet for et billede; fang det tidligt.
    if data[:5] in (b"<?xml", b"<Serv", b"<ows:") or len(data) < 256:
        os.remove(path)
        raise DhmError(
            "WCS returnerede ikke et billede.\nSvar: %s"
            % data[:300].decode("utf-8", "replace"))

    with open(path, "wb") as f:
        f.write(data)
    return path


def _tile_key(x, y, tile_m):
    """Gitter-celle (heltalskoordinat) for et punkt ved flisestørrelse tile_m."""
    return (int(x // tile_m), int(y // tile_m))


def _sample_tile(path, tile_points):
    """Slå Z op for punkterne i én flise fra dens GeoTIFF.

    tile_points: liste af de punkt-dicts der hører til flisen.
    Sætter p['z'] (None hvis uden for raster eller nodata).
    """
    from osgeo import gdal
    gdal.UseExceptions()

    ds = gdal.Open(path)
    if ds is None:
        raise DhmError("Kunne ikke åbne den hentede DHM-flise.")
    try:
        band = ds.GetRasterBand(1)
        nodata = band.GetNoDataValue()
        inv = gdal.InvGeoTransform(ds.GetGeoTransform())
        if inv is None:
            raise DhmError("DHM-flisen har en ugyldig geotransform.")
        nx, ny = ds.RasterXSize, ds.RasterYSize
        data = band.ReadAsArray()  # hele flisen i hukommelsen (lille)

        for p in tile_points:
            col = int(inv[0] + inv[1] * p["x"] + inv[2] * p["y"])
            row = int(inv[3] + inv[4] * p["x"] + inv[5] * p["y"])
            if 0 <= col < nx and 0 <= row < ny:
                z = float(data[row][col])
                if nodata is not None and z == nodata:
                    z = None
            else:
                z = None
            p["z"] = z
    finally:
        ds = None


def add_terrain_z(points, margin=None, progress=None, is_canceled=None):
    """Slå terrænkote (Z) op for hvert punkt fra DHM og sæt p['z'].

    points:      liste af dicts med 'x', 'y' i EPSG:25832.
    margin:      ekstra meter omkring hver flise (default = forskydning + lidt),
                 så punkter nær flisekant stadig har dækning.
    progress:    valgfri callback progress(done, total) kaldt pr. flise — til
                 fremgangslinje, når kaldt fra en baggrundstråd.
    is_canceled: valgfri callback der returnerer True hvis arbejdet skal
                 afbrydes (fx når brugeren annullerer tasken).

    Returnerer punkterne (samme liste) med 'z'. DHM hentes i fliser, så det
    virker for vilkårligt lange forløb uden at overskride WCS'ens grænser.
    """
    if not points:
        return points
    if margin is None:
        margin = config.OFFSET_DISTANCE + 5.0

    tile_m = config.DHM_TILE_PIXELS * config.DHM_RESOLUTION

    # Fordel punkter i fliser efter gitter-celle.
    tiles = {}
    for p in points:
        tiles.setdefault(_tile_key(p["x"], p["y"], tile_m), []).append(p)

    total = len(tiles)
    for i, tile_points in enumerate(tiles.values()):
        if is_canceled is not None and is_canceled():
            break
        # Hent kun den stramme bbox omkring flisens punkter (+ margin), så
        # download bliver så lille som muligt. Gitteret sikrer at bbox'en
        # aldrig overskrider WCS'ens størrelsesgrænse.
        xs = [p["x"] for p in tile_points]
        ys = [p["y"] for p in tile_points]
        minx, maxx = min(xs) - margin, max(xs) + margin
        miny, maxy = min(ys) - margin, max(ys) + margin

        if i > 0:
            time.sleep(config.DHM_TILE_PAUSE)  # skån WCS'ens rate-limit

        path = _download_tile(minx, miny, maxx, maxy)
        try:
            _sample_tile(path, tile_points)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        if progress is not None:
            progress(i + 1, total)
    return points
