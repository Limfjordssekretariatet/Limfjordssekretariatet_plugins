# -*- coding: utf-8 -*-
"""Fælles menu og værktøjslinje for Vandprojekter-pluginnene.

Alle pluginnene lægger deres knap i samme menu og deler én værktøjslinje, så
de står ét sted i QGIS i stedet for spredt ud over menulinjen.

Filen er ens i alle pluginnene. QGIS-plugins kan ikke importere fra hinanden
— hver er sin egen top-level pakke — så den kopieres i stedet for at ligge ét
sted. Rettes den, skal den rettes alle seks steder.
"""

from qgis.PyQt.QtWidgets import QToolBar

# Menuen ligger under Plugins. '&V' giver genvejstasten.
MENU = "&Vandprojekter"
TOOLBAR_TITEL = "Vandprojekter"
# objectName er nøglen til at genfinde linjen på tværs af plugins. QGIS
# gemmer også linjens placering under det navn, så det skal ligge fast.
TOOLBAR_ID = "VandprojekterToolbar"


def vaerktoejslinje(iface):
    """Den fælles værktøjslinje. Oprettes af det første plugin der beder om den.

    Slås op på objectName frem for at blive gemt i pluginnet, så det er
    ligegyldigt hvilket plugin QGIS indlæser først.
    """
    for linje in iface.mainWindow().findChildren(QToolBar):
        if linje.objectName() == TOOLBAR_ID:
            # Var den tom og skjult, skal den frem igen.
            linje.setVisible(True)
            return linje
    linje = iface.addToolBar(TOOLBAR_TITEL)
    linje.setObjectName(TOOLBAR_ID)
    return linje


def tilfoej(iface, action):
    """Læg en handling i den fælles menu og på den fælles værktøjslinje."""
    iface.addPluginToMenu(MENU, action)
    vaerktoejslinje(iface).addAction(action)


def fjern(iface, action):
    """Fjern handlingen igen.

    Kun denne ene knap tages af linjen — ellers ville afinstallation af ét
    plugin rydde de øvriges knapper væk. Er linjen tom bagefter, skjules
    den. Den slettes med vilje ikke: QGIS kalder unload og initGui i samme
    omgang, når et plugin opgraderes, og et slettet objekt ville så nå at
    blive genfundet og få den nye knap lagt på sig.
    """
    iface.removePluginMenu(MENU, action)
    for linje in iface.mainWindow().findChildren(QToolBar):
        if linje.objectName() != TOOLBAR_ID:
            continue
        linje.removeAction(action)
        if not linje.actions():
            linje.setVisible(False)
        return
