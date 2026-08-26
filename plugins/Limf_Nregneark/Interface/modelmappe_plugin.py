# -*- coding: utf-8 -*-
"""Pluginnets knap i QGIS.

Knappen lægges i den fælles Vandprojekter-menu og -værktøjslinje, som resten af
samlingen bruger — ikke i QGIS' generelle plugin-linje. Ellers lander den et
andet sted end sine søskende.
"""
import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .. import faelles_gui

# Ikonet ligger i plugin-roden — samme fil som QGIS' pluginhåndtering viser.
# Ét ikon ét sted: der lå før en kopi her i Interface/, og værktøjslinjen endte
# med et andet billede end pluginlisten.
_ROD = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
IKON = os.path.join(_ROD, 'icon.png')


class ModelMappePlugin:

    # Vist navn i menu, værktøjslinje og titler. Samme navn som i metadata.txt,
    # så pluginlisten og menuen kalder det det samme.
    PLUGIN_NAVN = "Vandprojekter – N-regneark"

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        ikon = QIcon(IKON) if os.path.isfile(IKON) else QIcon()
        self.action = QAction(ikon, self.PLUGIN_NAVN, self.iface.mainWindow())
        self.action.setToolTip(
            'Udfyld kvælstof-regnearket (N) for et vådområdeprojekt')
        self.action.triggered.connect(self.run)
        faelles_gui.tilfoej(self.iface, self.action)

    def unload(self):
        if self.action is not None:
            faelles_gui.fjern(self.iface, self.action)
            self.action = None
        self.dialog = None

    def run(self):
        """Åbner plugin-dialogen."""
        import importlib

        from . import modelmappe_interface
        importlib.reload(modelmappe_interface)

        self.dialog = modelmappe_interface.ModelMappeDialog(
            self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
