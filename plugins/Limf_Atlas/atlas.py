# -*- coding: utf-8 -*-
"""Atlas Mapbook – hovedklasse.

Tilføjer en knap i QGIS' menu/værktøjslinje. Når brugeren klikker:
  1. vises dialogen (lagvalg + felt-mapping),
  2. bygges atlasset ud fra skabelonen via AtlasBuilder,
  3. åbnes layoutet i Layout Designer.
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import QgsProject

from .dialog import AtlasDialog
from .atlas_builder import AtlasBuilder, AtlasBuildError
from .reference_data import ensure_reference

PLUGIN_DIR = os.path.dirname(__file__)
TEMPLATE_PATH = os.path.join(PLUGIN_DIR, "resources", "mapbook_skabelon.qpt")
# Referencekortet (arealtabel: Omdrift/Permanent græs) er for stort til plugin-
# zippen og downloades fra en GitHub release ved første brug (se reference_data).


class AtlasMapbook:
    """QGIS plugin implementering."""

    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.menu = "&Atlas Mapbook"
        self.toolbar = self.iface.addToolBar("Atlas Mapbook")
        self.toolbar.setObjectName("AtlasMapbook")

    # ------------------------------------------------------------------ GUI
    def initGui(self):
        icon_path = os.path.join(PLUGIN_DIR, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        action = QAction(icon, "Byg lodsejer-atlas…", self.iface.mainWindow())
        action.triggered.connect(self.run)
        action.setStatusTip("Generér et mapbook-atlas ud fra et lodsejerlag")

        self.toolbar.addAction(action)
        self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.toolbar.removeAction(action)
        del self.toolbar

    # ------------------------------------------------------------------ run
    def run(self):
        """Vis dialogen og byg atlasset hvis brugeren bekræfter."""
        if not os.path.exists(TEMPLATE_PATH):
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Atlas Mapbook",
                "Skabelonen blev ikke fundet:\n{}".format(TEMPLATE_PATH),
            )
            return

        dialog = AtlasDialog(self.iface, parent=self.iface.mainWindow())
        if not dialog.exec_():
            return  # brugeren annullerede

        result = dialog.result()  # AtlasDialogResult
        layer = result.layer

        # Referencekortet downloades fra release ved første brug (caches lokalt).
        reference_path = ensure_reference(self.iface.mainWindow())
        if reference_path is None:
            return  # bruger annullerede download eller fejl (allerede vist)

        try:
            builder = AtlasBuilder(
                project=QgsProject.instance(),
                template_path=TEMPLATE_PATH,
                iface=self.iface,
            )
            layout = builder.build(
                coverage_layer=layer,
                field_mapping=result.field_mapping,
                generate_lobenr=result.generate_lobenr,
                generate_postnr=result.generate_postnr,
                layout_name=result.layout_name,
                owner_field=result.owner_field,
                project_area_layer=result.project_area_layer,
                background_auto=result.background_auto,
                background_layer=result.background_layer,
                reference_path=reference_path,
            )
        except AtlasBuildError as exc:
            QMessageBox.critical(
                self.iface.mainWindow(), "Atlas Mapbook", str(exc)
            )
            return
        except Exception as exc:  # pragma: no cover - sikkerhedsnet
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Atlas Mapbook",
                "Uventet fejl under opbygning af atlas:\n{}".format(exc),
            )
            return

        # Start atlas-preview på første ejer, så hovedkortet straks zoomer
        # korrekt (ellers viser det skabelonens gamle extent indtil brugeren
        # selv slår preview til).
        atlas = layout.atlas()
        try:
            atlas.beginRender()
            atlas.first()
        except Exception:
            pass

        # Åbn layoutet i Layout Designer, klar til brug.
        self.iface.openLayoutDesigner(layout)

        page_count = atlas.count()
        self.iface.messageBar().pushSuccess(
            "Atlas Mapbook",
            "Atlasset '{}' er klar – {} sider (ejere).".format(
                result.layout_name, page_count
            ),
        )
