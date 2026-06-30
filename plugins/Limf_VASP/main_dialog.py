"""Hoveddialog for VASP-integration.

Åbnes fra den ene toolbar-/menuknap "VASP-integration" og samler pluginnets
handlinger som knapper: vælg database, "Terræn på profil" og "Opdater data
fra VASP". Selve handlingerne ligger i vasp_plugin; dialogen kalder dem via
callbacks.
"""

import os

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
)


class MainDialog(QDialog):
    """Lille menu-dialog med knapper til VASP-handlingerne."""

    def __init__(self, on_terraen, on_importer, on_importer_linje,
                 on_opdater, on_vaelg_database, get_db_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VASP-integration")
        self.setMinimumWidth(420)
        self._get_db_path = get_db_path
        self._on_vaelg_database = on_vaelg_database

        layout = QVBoxLayout(self)

        # --- aktuel database --------------------------------------------------
        layout.addWidget(QLabel("Aktiv VASP-database:"))
        self._db_label = QLabel()
        self._db_label.setWordWrap(True)
        self._db_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._db_label)

        btn_db = QPushButton("Vælg database …")
        btn_db.clicked.connect(self._vaelg_database)
        layout.addWidget(btn_db)
        self._refresh_db_label()

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        # --- handlinger -------------------------------------------------------
        layout.addWidget(QLabel("Vælg en handling:"))

        btn_importer = QPushButton("Importer længdeprofil til GIS")
        btn_importer.clicked.connect(lambda: self._run(on_importer))
        layout.addWidget(btn_importer)

        btn_importer_linje = QPushButton("Importer vandløbslinje til GIS")
        btn_importer_linje.clicked.connect(
            lambda: self._run(on_importer_linje))
        layout.addWidget(btn_importer_linje)

        btn_terraen = QPushButton("Terræn på profil")
        btn_terraen.clicked.connect(lambda: self._run(on_terraen))
        layout.addWidget(btn_terraen)

        btn_opdater = QPushButton("Genindlæs database")
        btn_opdater.clicked.connect(lambda: self._run(on_opdater))
        layout.addWidget(btn_opdater)

        btn_luk = QPushButton("Luk")
        btn_luk.clicked.connect(self.reject)
        layout.addWidget(btn_luk)

    def _refresh_db_label(self):
        """Opdatér visningen af den aktuelle database-sti."""
        path = self._get_db_path()
        self._db_label.setText(path)
        self._db_label.setToolTip(path)

    def _vaelg_database(self):
        """Lad brugeren vælge en database; bliv i dialogen og vis den nye."""
        # Pluginnet håndterer fil-valg, gem og evt. genopbygning, og
        # returnerer True hvis databasen blev ændret.
        if self._on_vaelg_database():
            self._refresh_db_label()

    def _run(self, callback):
        """Luk dialogen og kør den valgte handling.

        Dialogen lukkes først, så handlingens egne dialoger (profilvalg,
        bekræftelse) ikke ligger bag denne.
        """
        self.accept()
        callback()
