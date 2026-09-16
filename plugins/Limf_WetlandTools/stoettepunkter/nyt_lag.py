# -*- coding: utf-8 -*-
"""Opret et nyt støttepunktlag som GeoPackage.

Skemaet og hele formularopsætningen — dropdowns, standardværdier, aliasser,
afledte felter — skrives ind i selve filen som lagets standardstil. Så
virker laget ens i ethvert projekt og hos enhver kollega, uden at nogen
skal huske at indlæse en stilfil.
"""

import os
import sqlite3

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsField,
    QgsFields,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)

SKEMA = [
    ("punkt_id", QVariant.String, 0, 0),
    ("terraen", QVariant.Double, 10, 2),
    ("dtm_kilde", QVariant.String, 0, 0),
    ("sommid_nuv", QVariant.String, 0, 0),
    ("sommid_frem", QVariant.String, 0, 0),
    ("dybde_nuv", QVariant.Int, 0, 0),
    ("dybde_frem", QVariant.Int, 0, 0),
    ("vsp_nuv", QVariant.Double, 10, 2),
    ("vsp_frem", QVariant.Double, 10, 2),
    ("bemaerkning", QVariant.String, 0, 0),
]

STIL_DDL = """CREATE TABLE IF NOT EXISTS layer_styles (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 f_table_catalog TEXT, f_table_schema TEXT, f_table_name TEXT,
 f_geometry_column TEXT, styleName TEXT, styleQML TEXT, styleSLD TEXT,
 useAsDefault BOOLEAN, description TEXT, owner TEXT, ui TEXT,
 update_time DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))"""


def qml_tekst():
    sti = os.path.join(os.path.dirname(__file__), "afvanding_punkter.qml")
    with open(sti, "r", encoding="utf-8") as fh:
        return fh.read()


def opret_lag(sti, lagnavn="stoettepunkter", epsg="EPSG:25832"):
    """Skriv en ny GeoPackage. Returnerer (QgsVectorLayer, fejltekst)."""
    felter = QgsFields()
    for navn, typ, laengde, praecision in SKEMA:
        felter.append(QgsField(navn, typ, "", laengde, praecision))

    valg = QgsVectorFileWriter.SaveVectorOptions()
    valg.driverName = "GPKG"
    valg.layerName = lagnavn
    valg.fileEncoding = "UTF-8"
    valg.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

    skriver = QgsVectorFileWriter.create(
        sti, felter, QgsWkbTypes.Point,
        QgsCoordinateReferenceSystem(epsg),
        QgsProject.instance().transformContext(), valg)

    if skriver.hasError() != QgsVectorFileWriter.NoError:
        fejl = skriver.errorMessage()
        del skriver
        return None, fejl
    del skriver  # lukker filen, så sqlite kan åbne den

    try:
        con = sqlite3.connect(sti)
        cur = con.cursor()
        cur.execute(STIL_DDL)
        cur.execute(
            "DELETE FROM layer_styles WHERE f_table_name = ?", (lagnavn,))
        cur.execute(
            "INSERT INTO layer_styles (f_table_catalog, f_table_schema,"
            " f_table_name, f_geometry_column, styleName, styleQML, styleSLD,"
            " useAsDefault, description, owner, ui)"
            " VALUES ('', '', ?, 'geom', 'Standard', ?, NULL, 1, ?, NULL, NULL)",
            (lagnavn, qml_tekst(),
             "Dropdowns og afledte felter til afvandingsdybde"))
        con.commit()
        con.close()
    except Exception as exc:
        return None, "Laget blev oprettet, men stilen kunne ikke gemmes: %s" % exc

    lag = QgsVectorLayer("%s|layername=%s" % (sti, lagnavn), lagnavn, "ogr")
    if not lag.isValid():
        return None, "Laget kunne ikke indlæses efter oprettelsen."
    return lag, None
