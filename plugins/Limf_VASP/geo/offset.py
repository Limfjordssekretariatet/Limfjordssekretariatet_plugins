"""Forskydning af et forløb til side, med bevarelse af stationeringen.

Stationeringen skal følge det OPRINDELIGE profil: når brugeren vælger 1 m
interval, betyder det 1 m målt på den oprindelige profillinje — så hvert
station-trin svarer præcist til originalprofilet, uanset at den forskudte linje
(fx 10 m ude) i sving fysisk bliver lidt længere eller kortere.

Hybrid fremgangsmåde:
  1. Forskyd hele linjen parallelt med QGIS' offsetline (glat offset-kurve, der
     ikke selvkrydser i sving). Behold kun den længste del (splinter kasseres).
  2. Resample den ORIGINALE linje til faste stationer (0, interval, 2*interval …)
     — det giver punkt + korrekt original-station (genbruger resample).
  3. For hver original-station: brug nærmeste punkt på den forskudte linje.
     Stationen kommer fra originalen, geometrien fra den glatte offset-kurve.

I skarpe indersving (radius < forskydningsafstand) kan få punkter ligge tæt,
fordi en parallel offset ikke kan følge en så skarp kurve — men stationeringen
forbliver tro mod originalprofilet, hvilket er det vigtigste her.
"""

from qgis.core import (
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
)
import processing

from . import resample

# Sidevalg (matcher VaspExcel: venstre vs højre).
SIDE_LEFT = "left"
SIDE_RIGHT = "right"


def _line_layer(points, epsg):
    """Byg et midlertidigt linjelag af punkterne (i rækkefølge)."""
    layer = QgsVectorLayer(
        "LineString?crs=EPSG:%d" % epsg, "centerline", "memory")
    feat = QgsFeature()
    feat.setGeometry(QgsGeometry.fromPolylineXY(
        [QgsPointXY(p["x"], p["y"]) for p in points]))
    layer.dataProvider().addFeature(feat)
    layer.updateExtents()
    return layer


def _longest_offset_geometry(line_layer):
    """Returnér den længste dels geometri fra et (multipart) linjelag.

    offsetline kan returnere flere dele: én lang hoveddel plus korte
    selvkryds-splinter. Vi beholder den længste. Returnerer en QgsGeometry
    (LineString) eller None.
    """
    best_pts = None
    best_len = -1.0
    for feat in line_layer.getFeatures():
        geom = feat.geometry()
        if geom.isEmpty():
            continue
        parts = geom.asMultiPolyline() if geom.isMultipart() \
            else [geom.asPolyline()]
        for part in parts:
            length = sum(part[i].distance(part[i + 1])
                         for i in range(len(part) - 1))
            if length > best_len:
                best_len = length
                best_pts = part
    if not best_pts:
        return None
    return QgsGeometry.fromPolylineXY(best_pts)


def offset_line_points(points, distance, side, interval, start_station,
                       epsg):
    """Forskyd forløbet til siden, med punkter på originalens stationering.

    points:        centerline-punkter (dicts med 'x', 'y', 'station'), i
                   rækkefølge og med stigende station.
    distance:      forskydningsafstand i meter (positiv).
    side:          SIDE_LEFT eller SIDE_RIGHT (set i stationeringsretningen).
    interval:      interval mellem punkter — målt på den ORIGINALE linje.
    start_station: ubrugt (stationen kommer fra punkternes egen station);
                   beholdt for kald-kompatibilitet.
    epsg:          EPSG-kode (til de midlertidige lag).

    Returnerer en liste af dicts med 'x', 'y', 'station', hvor station er
    den oprindelige profil-station. Z hentes separat fra DHM.
    """
    if len(points) < 2:
        return []

    # 1) Forskyd hele linjen parallelt (glat). Positiv = venstre.
    signed = distance if side == SIDE_LEFT else -distance
    offset_res = processing.run("native:offsetline", {
        "INPUT": _line_layer(points, epsg),
        "DISTANCE": signed,
        "SEGMENTS": 8,
        "JOIN_STYLE": 0,   # Round (følger sving blødt)
        "MITER_LIMIT": 2,
        "OUTPUT": "memory:",
    })
    off_geom = _longest_offset_geometry(offset_res["OUTPUT"])
    if off_geom is None:
        return []

    # 2) Resample originallinjen efter station (bevarer original-station).
    base = resample.resample_centerline(
        [{"station": p["station"], "x": p["x"], "y": p["y"],
          "kote": p.get("kote", 0.0)} for p in points],
        interval)

    # 3) For hver original-station: nærmeste punkt på den forskudte linje.
    result = []
    for b in base:
        nearest = off_geom.nearestPoint(
            QgsGeometry.fromPointXY(QgsPointXY(b["x"], b["y"])))
        pt = nearest.asPoint()
        result.append({
            "x": pt.x(),
            "y": pt.y(),
            "station": b["station"],
        })
    return result
