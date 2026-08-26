import os
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon


class ModelMappePlugin:

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    # Vist navn i værktøjslinje, menu og titler. Bruges som ÉN konstant, så
    # add/remove i menuen altid matcher.
    PLUGIN_NAVN = "Automatisk udfyldning af N-regneark (LIMF)"

    def initGui(self):
        """Opretter knap i plugins-værktøjslinjen."""
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.png')
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.action = QAction(icon, self.PLUGIN_NAVN, self.iface.mainWindow())
        self.action.setToolTip(f"Åbn {self.PLUGIN_NAVN}")
        self.action.triggered.connect(self.run)

        # Tilføj til plugins-menuen og værktøjslinjen
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.PLUGIN_NAVN, self.action)

    def unload(self):
        """Fjerner knap fra interface når plugin deaktiveres."""
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu(self.PLUGIN_NAVN, self.action)
        del self.action
        self.dialog = None

    def run(self):
        """Åbner plugin-dialogen."""
        import importlib
        from . import modelmappe_interface
        importlib.reload(modelmappe_interface)

        self.dialog = modelmappe_interface.ModelMappeDialog(self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
