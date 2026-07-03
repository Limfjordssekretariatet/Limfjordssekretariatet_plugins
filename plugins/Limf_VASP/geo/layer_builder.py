"""Bygger QGIS-lag ud fra VASP-profildata.

Tager de rene punkt-dicts fra dbaccess.read_profile_points og laver et
memory-punktlag med terrænkote og stationering som attributter.
"""

from qgis.core import (
    QgsVectorLayer,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
)
from qgis.PyQt.QtCore import QVariant

from .. import config


def epsg_for(koordsysid):
    """Oversæt VASP-koordinatsystem-id til en EPSG-kode (med fallback)."""
    return config.KOORDSYS_TO_EPSG.get(koordsysid, config.FALLBACK_EPSG)


def build_profile_layer(layer_name, points, koordsysid):
    """Lav et memory-punktlag for ét profil-datalag.

    points: liste af dicts (station, x, y, kote, tvpid) fra dbaccess.
    Returnerer et QgsVectorLayer (ikke tilføjet til projektet endnu).
    """
    epsg = epsg_for(koordsysid)
    layer = QgsVectorLayer(
        "Point?crs=EPSG:%d" % epsg, layer_name, "memory"
    )
    provider = layer.dataProvider()

    fields = QgsFields()
    fields.append(QgsField("tvpid", QVariant.Int))
    fields.append(QgsField("station", QVariant.Double))
    fields.append(QgsField("kote", QVariant.Double))
    provider.addAttributes(fields)
    layer.updateFields()

    features = []
    for p in points:
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(p["x"], p["y"])))
        # Resamplede stationeringspunkter har ingen tvpid; lad den være tom.
        feat.setAttributes([p.get("tvpid"), p["station"], p["kote"]])
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def build_gisline_layer(layer_name, points, koordsysid):
    """Lav et LineString-lag med én vandløbslinje ud fra dens knudepunkter.

    points: liste af dicts med 'x', 'y' i rækkefølge langs linjen.
    Returnerer et memory-LineString-lag med én feature.
    """
    epsg = epsg_for(koordsysid)
    layer = QgsVectorLayer(
        "LineString?crs=EPSG:%d" % epsg, layer_name, "memory")
    provider = layer.dataProvider()

    feat = QgsFeature()
    feat.setGeometry(QgsGeometry.fromPolylineXY(
        [QgsPointXY(p["x"], p["y"]) for p in points]))
    provider.addFeature(feat)
    layer.updateExtents()
    return layer


def build_vsp_layer(layer_name, points, koordsysid, fields_spec):
    """Lav et punktlag for vandspejlsberegningspunkter (fra .ber).

    points:      liste af dicts med 'x', 'y' + de felter der er i fields_spec.
    fields_spec: liste af (feltnavn, dict-nøgle) — attributterne der skrives.
                 Simpel: station, vsp, bund, energi, vnf, manning, bredde …
                 Multi:  ét vsp-felt pr. scenarie (fx vsp_MedMin, vsp_Sommer).
    Punkternes vandspejl (vsp) lægges også som geometrisk Z (PointZ).
    """
    epsg = epsg_for(koordsysid)
    layer = QgsVectorLayer(
        "PointZ?crs=EPSG:%d" % epsg, layer_name, "memory")
    provider = layer.dataProvider()

    fields = QgsFields()
    for fname, _ in fields_spec:
        fields.append(QgsField(fname, QVariant.Double))
    provider.addAttributes(fields)
    layer.updateFields()

    features = []
    for p in points:
        # Z = vandspejlskoten hvis den findes, ellers 0.
        z = p.get("vsp")
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry(QgsPoint(
            p["x"], p["y"], z if z is not None else 0.0)))
        feat.setAttributes([p.get(key) for _, key in fields_spec])
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def build_terrain_layer(layer_name, points, koordsysid):
    """Lav et PointZ-lag hvor punktets Z er terrænkoten fra DHM.

    points: liste af dicts med 'x', 'y', 'station' og 'z' (DHM-terrænkote).
    Z lægges i geometrien (PointZ). Station beholdes som attribut til
    reference. Punkter uden gyldig z (None) springes over.
    """
    epsg = epsg_for(koordsysid)
    layer = QgsVectorLayer(
        "PointZ?crs=EPSG:%d" % epsg, layer_name, "memory"
    )
    provider = layer.dataProvider()

    fields = QgsFields()
    fields.append(QgsField("station", QVariant.Double))
    provider.addAttributes(fields)
    layer.updateFields()

    features = []
    for p in points:
        z = p.get("z")
        if z is None:
            continue
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry(QgsPoint(p["x"], p["y"], z)))
        feat.setAttributes([p.get("station")])
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()
    return layer
