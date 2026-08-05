import os
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon

from . import faelles_gui


class DrænudløbspunkterPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(icon, "Dræn …", self.iface.mainWindow())
        self.action.setStatusTip(
            "Find drænudløbspunkter ud fra fald og terrænmodel")
        self.action.triggered.connect(self.run)
        faelles_gui.tilfoej(self.iface, self.action)

    def unload(self):
        if self.action is not None:
            faelles_gui.fjern(self.iface, self.action)
            self.action = None

    def run(self):
        script_path = os.path.join(self.plugin_dir, "Opening.py")
        with open(script_path, "r", encoding="utf-8") as f:
            code = f.read()
        # PLUGIN_DIR gives med, så scriptet kan finde faelles_ui ved siden af
        # sig selv — det køres uden pakkekontekst og kan ikke importere.
        exec(compile(code, script_path, "exec"),
             {"iface": self.iface, "PLUGIN_DIR": self.plugin_dir})
