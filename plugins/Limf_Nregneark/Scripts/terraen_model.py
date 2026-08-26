"""
Terrænmodel – henter DHM Terrænmodel fra Dataforsyningen WCS.

Bruges fra interfacet:
    token            : Dataforsyningen API-token
    projektomraade   : Sti til projektområde-fil (.shp, .gpkg, .geojson, ...)
    output_mappe     : Mappe til at gemme Hoejdemodel.tif

Dataforsyningen WCS:
    Service : https://services.dataforsyningen.dk/dhm
    Coverage: dhm_terraen  (0,4 m opløsning, EPSG:25832)
"""

import os
import urllib.request
import urllib.error

from osgeo import ogr


def hent_terraen_model(token, projektomraade_sti, output_mappe):
    """
    Henter terrænmodel fra Dataforsyningen og gemmer den som GeoTIFF.

    Parameters
    ----------
    token             : str   – Dataforsyningen API-token
    projektomraade_sti: str   – Fuld sti til projektområde-fil
    output_mappe      : str   – Mappe der gemmes i

    Returns
    -------
    output_sti : str  – Fuld sti til den gemte GeoTIFF
    """

    # --- 1. Åbn projektområde og hent extent ---
    ds = ogr.Open(projektomraade_sti)
    if ds is None:
        raise ValueError(f"Kan ikke åbne projektområde-fil: {projektomraade_sti}")

    lag = ds.GetLayer(0)
    if lag is None:
        raise ValueError("Projektområde-filen indeholder ingen lag.")

    extent = lag.GetExtent()   # (xmin, xmax, ymin, ymax)
    ds = None                  # Luk datasource

    xmin, xmax, ymin, ymax = extent

    # --- 2. Beregn billedstørrelse (DHM: 0,4 m/pixel, max 4000 px) ---
    resolution = 0.4
    width_px  = max(1, min(int((xmax - xmin) / resolution), 4000))
    height_px = max(1, min(int((ymax - ymin) / resolution), 4000))

    # --- 3. Byg WCS GetCoverage-URL ---
    wcs_url = (
        "https://services.dataforsyningen.dk/dhm"
        "?SERVICE=WCS"
        "&VERSION=1.0.0"
        "&REQUEST=GetCoverage"
        "&COVERAGE=dhm_terraen"
        "&CRS=EPSG:25832"
        f"&BBOX={xmin},{ymin},{xmax},{ymax}"
        f"&WIDTH={width_px}"
        f"&HEIGHT={height_px}"
        "&FORMAT=GTiff"
        f"&token={token}"
    )

    # --- 4. Download og gem ---
    os.makedirs(output_mappe, exist_ok=True)
    output_sti = os.path.join(output_mappe, "Hoejdemodel.tif")

    try:
        urllib.request.urlretrieve(wcs_url, output_sti)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"HTTP-fejl ved download af terrænmodel: {e.code} {e.reason}\n"
            f"Kontrollér at token er korrekt."
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Netværksfejl ved download af terrænmodel: {e.reason}"
        ) from e

    return output_sti
