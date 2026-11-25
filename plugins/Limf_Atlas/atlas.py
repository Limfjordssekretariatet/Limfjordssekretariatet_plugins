# -*- coding: utf-8 -*-
"""Atlas Mapbook – hovedklasse.

Tilføjer en knap i QGIS' menu/værktøjslinje. Når brugeren klikker:
  1. vises dialogen (lagvalg + felt-mapping),
  2. bygges atlasset ud fra skabelonen via AtlasBuilder,
  3. åbnes layoutet i Layout Designer.
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QPushButton
from qgis.core import Qgis, QgsProject

from . import faelles_gui
from .dialog import BYG_OVERSIGT, AtlasDialog
from .atlas_builder import AtlasBuilder, AtlasBuildError
from .oversigtskort import OversigtskortBuilder
from .reference_data import ensure_reference, kategorifelt

PLUGIN_DIR = os.path.dirname(__file__)
TEMPLATE_PATH = os.path.join(PLUGIN_DIR, "resources", "mapbook_skabelon.qpt")
# Referencekortene (arealtabel: Omdrift/Permanent græs) er for store til plugin-
# zippen og downloades fra en GitHub release ved første brug – ét pr. periode,
# som brugeren vælger i dialogen (se reference_data).


class AtlasMapbook:
    """QGIS plugin implementering."""

    def __init__(self, iface):
        self.iface = iface
        self.actions = []

    # ------------------------------------------------------------------ GUI
    def initGui(self):
        icon_path = os.path.join(PLUGIN_DIR, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        action = QAction(icon, "Lodsejeratlas …", self.iface.mainWindow())
        action.triggered.connect(self.run)
        action.setStatusTip("Generér et mapbook-atlas ud fra et lodsejerlag")

        faelles_gui.tilfoej(self.iface, action)
        self.actions.append(action)

    def unload(self):
        for action in self.actions:
            faelles_gui.fjern(self.iface, action)
        self.actions = []

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
        if not dialog.exec():
            return  # brugeren annullerede

        result = dialog.result()  # AtlasDialogResult
        layer = result.layer

        if result.byg == BYG_OVERSIGT:
            self._byg_oversigt(result)
            return

        # Referencekortet for den valgte periode downloades fra release ved
        # første brug (caches lokalt, ét kort pr. periode).
        periode = result.reference_periode
        reference_path = ensure_reference(self.iface.mainWindow(), periode)
        if reference_path is None:
            return  # bruger annullerede download eller fejl (allerede vist)

        try:
            builder = AtlasBuilder(
                project=QgsProject.instance(),
                template_path=TEMPLATE_PATH,
                iface=self.iface,
                matrikel_kant=result.matrikel_kant,
                matrikel_fyld=result.matrikel_fyld,
                omraade_kant=result.omraade_kant,
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
                reference_field=kategorifelt(periode),
                reference_years=periode,
                min_area_m2=result.min_area_m2,
                extra_layers=result.extra_layers,
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

    def _byg_oversigt(self, result):
        """Byg oversigtskortet (fanen «Oversigtskort») og åbn det.

        Referencekortet hentes ikke — det bruges kun til atlassets
        arealtabel.
        """
        try:
            builder = OversigtskortBuilder(
                project=QgsProject.instance(),
                template_path=TEMPLATE_PATH,
                iface=self.iface,
            )
            layout = builder.build(
                parcel_layer=result.layer,
                field_mapping=result.field_mapping,
                generate_lobenr=result.generate_lobenr,
                generate_postnr=result.generate_postnr,
                owner_field=result.owner_field,
                valg=result.oversigt,
                project_area_layer=result.project_area_layer,
                background_auto=result.background_auto,
                background_layer=result.background_layer,
                extra_layers=result.extra_layers,
            )
        except AtlasBuildError as exc:
            QMessageBox.critical(
                self.iface.mainWindow(), "Oversigtskort", str(exc))
            return
        except Exception as exc:  # pragma: no cover - sikkerhedsnet
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Oversigtskort",
                "Uventet fejl under opbygning af oversigtskortet:\n{}".format(
                    exc),
            )
            return

        designer = self.iface.openLayoutDesigner(layout)
        lag = getattr(builder, "ejere_lag", None)
        tekst = ("Oversigtskortet '{}' er klar. Er du utilfreds med en "
                 "lodsejers farve eller skravering, kan du rette den — "
                 "kortet og forklaringen følger med.".format(layout.name()))
        self.iface.messageBar().pushSuccess("Oversigtskort", tekst)
        if designer is None or lag is None:
            return
        try:
            besked = designer.messageBar().createMessage("Oversigtskort", tekst)
            knap = QPushButton("Ret symboler…")
            knap.clicked.connect(
                lambda _=False, l=lag, w=designer.window():
                self._ret_symboler(l, w))
            besked.layout().addWidget(knap)
            designer.messageBar().pushWidget(besked, Qgis.MessageLevel.Info, 0)
        except Exception:  # pragma: no cover - beskeden er en bekvemmelighed
            pass

    def _ret_symboler(self, lag, forael=None):
        """Åbn QGIS' symbologi for lodsejerlaget.

        Laget ligger i lagpanelet (gruppen med layoutets navn), så det kan
        også rettes derfra senere: højreklik → Egenskaber → Symbologi. Dobbeltklik
        på en lodsejer i listen for at skifte farve eller skravering.
        """
        if QgsProject.instance().mapLayer(lag.id()) is None:
            QMessageBox.information(
                forael or self.iface.mainWindow(), "Oversigtskort",
                "Lodsejerlaget findes ikke længere i projektet. Byg "
                "oversigtskortet igen.")
            return
        self.iface.showLayerProperties(lag, "mOptsPage_Symbology")
