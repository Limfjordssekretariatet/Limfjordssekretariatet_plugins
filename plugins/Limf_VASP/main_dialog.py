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
                 on_opdater, on_vaelg_database, get_db_path, data_ready,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("VASP-integration")
        self.setMinimumWidth(420)
        self._get_db_path = get_db_path
        self._on_vaelg_database = on_vaelg_database
        self._data_ready = data_ready

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

        # Hjælpe-tekst der vises indtil en database/datafil er klar.
        self._hint = QLabel(
            "Vælg din VASP-database for at komme i gang. "
            "Handlingerne nedenfor låses op når datafilen er bygget.")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #a00;")
        layout.addWidget(self._hint)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        # --- handlinger -------------------------------------------------------
        layout.addWidget(QLabel("Vælg en handling:"))

        # Handlings-knapper der kræver en bygget datafil → samles så de kan
        # gråtones indtil data er klar.
        self._action_buttons = []

        btn_importer = QPushButton("Importer længdeprofil til GIS")
        btn_importer.clicked.connect(lambda: self._run(on_importer))
        layout.addWidget(btn_importer)
        self._action_buttons.append(btn_importer)

        btn_importer_linje = QPushButton("Importer vandløbslinje til GIS")
        btn_importer_linje.clicked.connect(
            lambda: self._run(on_importer_linje))
        layout.addWidget(btn_importer_linje)
        self._action_buttons.append(btn_importer_linje)

        btn_terraen = QPushButton("Terræn på profil")
        btn_terraen.clicked.connect(lambda: self._run(on_terraen))
        layout.addWidget(btn_terraen)
        self._action_buttons.append(btn_terraen)

        btn_opdater = QPushButton("Genindlæs database")
        btn_opdater.clicked.connect(lambda: self._run(on_opdater))
        layout.addWidget(btn_opdater)
        self._action_buttons.append(btn_opdater)

        btn_luk = QPushButton("Luk")
        btn_luk.clicked.connect(self.reject)
        layout.addWidget(btn_luk)

        self._refresh_db_label()
        self._update_enabled()

    def _refresh_db_label(self):
        """Opdatér visningen af den aktuelle database-sti."""
        path = self._get_db_path()
        self._db_label.setText(path or "(ingen valgt)")
        self._db_label.setToolTip(path or "")

    def _update_enabled(self):
        """Gråtone handlings-knapperne indtil datafilen (gpkg) er klar."""
        ready = bool(self._data_ready())
        for btn in self._action_buttons:
            btn.setEnabled(ready)
        self._hint.setVisible(not ready)

    def _vaelg_database(self):
        """Lad brugeren vælge en database; bliv i dialogen og vis den nye.

        Pluginnet håndterer fil-valg, gem og automatisk opbygning af datafilen,
        og returnerer True hvis databasen blev ændret."""
        if self._on_vaelg_database():
            self._refresh_db_label()
        # Opdatér knap-tilstand uanset (datafilen kan nu være bygget).
        self._update_enabled()

    def _run(self, callback):
        """Luk dialogen og kør den valgte handling.

        Dialogen lukkes først, så handlingens egne dialoger (profilvalg,
        bekræftelse) ikke ligger bag denne.
        """
        self.accept()
        callback()
