# -*- coding: utf-8 -*-
"""Fælles menu og værktøjslinje for Vandprojekter-pluginnene.

Alle pluginnene lægger deres knap i samme menu og deler én værktøjslinje, så
de står ét sted i QGIS i stedet for spredt ud over menulinjen.

Filen er ens i alle pluginnene. QGIS-plugins kan ikke importere fra hinanden
— hver er sin egen top-level pakke — så den kopieres i stedet for at ligge ét
sted. Rettes den, skal den rettes alle seks steder.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QToolBar

try:
    from qgis.PyQt import sip
except ImportError:      # ældre PyQt-udgaver
    sip = None

# Menuen ligger under Plugins. '&V' giver genvejstasten.
MENU = "&Vandprojekter"
TOOLBAR_TITEL = "Vandprojekter"
# objectName er nøglen til at genfinde linjen på tværs af plugins.
TOOLBAR_ID = "VandprojekterToolbar"

# Holder liv i værktøjslinjen — se _behold().
_linje = None


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
    _behold(linje)
    # Navnet skrives ved siden af ikonet. Et navn inde i selve ikonet er
    # ikke til at læse ved 24 px, som er QGIS' standardstørrelse.
    linje.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    # QGIS husker ikke plugin-linjers placering, så en ny linje lander sidst
    # i den øverste række. Er rækken fuld, kollapser alle knapperne ned i
    # ">>"-menuen, og linjen ligner ingenting. Et linjeskift giver de seks
    # ikoner deres egen række, hvor de er til at få øje på.
    iface.mainWindow().insertToolBarBreak(linje)
    linje.setVisible(True)
    return linje


def _behold(linje):
    """Sørg for at værktøjslinjen ikke bliver ryddet væk igen.

    ``iface.addToolBar()`` er erklæret ``/Factory/`` i QGIS' sip-binding:
    Python overtager ejerskabet over det oprettede objekt. Beholder man ikke
    en reference, sletter Pythons oprydning C++-objektet, så snart kaldet er
    færdigt — linjen forsvinder fra vinduet uden nogen fejlmeddelelse, mens
    menupunkterne bliver stående, fordi de ejes af QGIS. Derfor både en
    modulglobal reference og ejerskabet tilbage til C++, hvor hovedvinduet
    rydder op til sidst.
    """
    global _linje
    _linje = linje
    if sip is not None:
        try:
            sip.transferto(linje, None)
        except (TypeError, ValueError, RuntimeError):
            pass


def tilfoej(iface, action):
    """Læg en handling i den fælles menu og på den fælles værktøjslinje."""
    iface.addPluginToMenu(MENU, action)
    # Menupunktet beholder sit "…" — konventionen for "åbner en dialog" —
    # men på en knap ser det forkert ud, så knapteksten sættes uden.
    action.setIconText(action.text().replace("…", "").replace("...", "")
                       .strip())
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
