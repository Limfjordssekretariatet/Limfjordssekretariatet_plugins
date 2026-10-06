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


# ----------------------------------------------------------------------
# Opsætningen lagt på et lag, man har i forvejen
# ----------------------------------------------------------------------

#: De felter værktøjet ikke kan undvære. Resten kan godt mangle — de
#: afledte felter bliver blot ikke udfyldt.
PAAKRAEVEDE = ("terraen", "sommid_nuv", "sommid_frem")

#: Hvilken felttype hvert felt bør have, så koter og dybder kan skrives.
FORVENTET_TYPE = {navn: typ for navn, typ, _l, _p in SKEMA}


def mangler_felter(lag):
    """Navnene på de felter i skemaet, laget ikke har."""
    har = {f.name() for f in lag.fields()}
    return [navn for navn, _t, _l, _p in SKEMA if navn not in har]


def er_stoettepunktlag(lag):
    """Kan værktøjet arbejde på laget, som det ser ud nu?"""
    if lag is None:
        return False
    har = {f.name() for f in lag.fields()}
    return all(navn in har for navn in PAAKRAEVEDE)


def _forkerte_typer(lag):
    """Felter der findes, men har en anden type end skemaet forventer."""
    ud = []
    for felt in lag.fields():
        forventet = FORVENTET_TYPE.get(felt.name())
        if forventet is None or felt.type() == forventet:
            continue
        # Heltal i et kommatalsfelt går an; den anden vej rundt mister
        # decimalerne, og tekst kan slet ikke regnes på.
        if (forventet == QVariant.Int and felt.type() == QVariant.Double):
            continue
        ud.append(felt.name())
    return ud


def tilfoej_opsaetning(lag):
    """Gør et punktlag, man har i forvejen, til et støttepunktlag.

    Lægger de manglende felter på og sætter formularopsætningen —
    dropdowns, standardværdien der henter koten fra højdemodellen, og de
    afledte felter — oven på laget. Stilen indeholder ingen symbologi, så
    lagets udseende bliver ikke rørt.

    Returnerer (rapport, fejltekst). ``rapport`` er en dict med
    'tilfoejede', 'forkerte_typer' og 'gemt' (om opsætningen også kunne
    gemmes i filen, så den holder ved næste åbning).
    """
    from qgis.core import QgsVectorDataProvider

    if lag is None:
        return None, "Vælg først et punktlag."
    if lag.geometryType() != QgsWkbTypes.PointGeometry:
        return None, "Støttepunkter skal være et punktlag."
    if lag.isEditable() and not lag.commitChanges():
        return None, ("Laget står i redigering med ændringer, der ikke er "
                      "gemt. Gem eller fortryd dem først.")

    manglende = mangler_felter(lag)
    tilfoejede = []
    if manglende:
        udbyder = lag.dataProvider()
        if not (udbyder.capabilities()
                & QgsVectorDataProvider.AddAttributes):
            return None, ("Laget kan ikke få nye felter (%s). Gem det som "
                          "GeoPackage først, eller opret et nyt "
                          "støttepunktlag." % udbyder.name())
        nye = QgsFields()
        for navn, typ, laengde, praecision in SKEMA:
            if navn in manglende:
                nye.append(QgsField(navn, typ, "", laengde, praecision))
        if not udbyder.addAttributes(list(nye)):
            return None, "Felterne kunne ikke lægges på laget."
        lag.updateFields()
        # Filformatet kan have afkortet eller omdøbt et navn undervejs —
        # så er feltet reelt ikke kommet med, og det skal siges.
        endnu_manglende = mangler_felter(lag)
        tilfoejede = [n for n in manglende if n not in endnu_manglende]
        if endnu_manglende:
            return ({"tilfoejede": tilfoejede,
                     "forkerte_typer": _forkerte_typer(lag),
                     "gemt": False},
                    "Felterne %s kunne ikke oprettes i formatet %s — "
                    "feltnavne er for lange. Brug en GeoPackage."
                    % (", ".join(endnu_manglende), lag.dataProvider().name()))

    besked, ok = lag.loadNamedStyle(
        os.path.join(os.path.dirname(__file__), "afvanding_punkter.qml"))
    if not ok:
        return ({"tilfoejede": tilfoejede,
                 "forkerte_typer": _forkerte_typer(lag), "gemt": False},
                "Felterne er på plads, men opsætningen kunne ikke "
                "indlæses: %s" % besked)
    lag.triggerRepaint()

    return {"tilfoejede": tilfoejede,
            "forkerte_typer": _forkerte_typer(lag),
            "gemt": _gem_standardstil(lag)}, None


def _gem_standardstil(lag):
    """Gem opsætningen i selve filen, så den holder ved næste åbning.

    Lykkes det ikke, virker laget stadig i det åbne projekt — så det er
    ikke en fejl, der skal stoppe noget.
    """
    try:
        try:
            from qgis.core import QgsMapLayer
            _besked, ok = lag.saveDefaultStyle(QgsMapLayer.AllStyleCategories)
        except TypeError:
            _besked, ok = lag.saveDefaultStyle()
        return bool(ok)
    except Exception:
        return False
