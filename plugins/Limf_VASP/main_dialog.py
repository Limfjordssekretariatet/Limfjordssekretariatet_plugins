"""Hoveddialog for VASP-integration.

Åbnes fra den ene toolbar-/menuknap "VASP-integration" og samler pluginnets
handlinger i tre afsnit: den aktive database, hvad der kan hentes ind i GIS,
og analyserne. Selve handlingerne ligger i vasp_plugin; dialogen kalder dem
via callbacks.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFontMetrics
from qgis.PyQt.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
)

from . import faelles_ui

# Målene ligger i faelles_ui, så alle pluginnenes dialoger deler dem.
_KNAP_HOEJDE = faelles_ui.KNAP_HOEJDE


class MainDialog(QDialog):
    """Menu-dialog med VASP-handlingerne samlet i afsnit."""

    def __init__(self, on_terraen, on_importer, on_importer_linje,
                 on_importer_vsp, on_opdater, on_vaelg_database, get_db_path,
                 data_ready, on_braend_vandloeb=None,
                 on_afvandingsanalyse=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VASP-integration")
        self.setMinimumWidth(460)
        self._get_db_path = get_db_path
        self._on_vaelg_database = on_vaelg_database
        self._data_ready = data_ready
        self._db_sti = ""

        # Handlings-knapper der kræver en bygget datafil → samles så de kan
        # gråtones indtil data er klar.
        self._action_buttons = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(faelles_ui.MARGIN, faelles_ui.MARGIN,
                                  faelles_ui.MARGIN, faelles_ui.MARGIN)
        layout.setSpacing(faelles_ui.AFSTAND)

        layout.addWidget(self._database_boks())
        layout.addWidget(self._import_boks(
            on_importer, on_importer_linje, on_importer_vsp))
        layout.addWidget(self._analyse_boks(
            on_terraen, on_braend_vandloeb, on_afvandingsanalyse))
        layout.addStretch(1)
        layout.addLayout(self._bund_raekke(on_opdater))

        self._refresh_db_label()
        self._update_enabled()
        faelles_ui.goer_rulbar(self)

    # --- afsnit -----------------------------------------------------------

    def _database_boks(self):
        """Øverste afsnit: hvilken database der arbejdes på."""
        boks = QGroupBox("Database")
        indhold = QVBoxLayout(boks)
        indhold.setSpacing(8)

        self._db_label = QLabel()
        self._db_label.setStyleSheet("font-weight: bold;")
        self._db_label.setSizePolicy(QSizePolicy.Ignored,
                                     QSizePolicy.Preferred)
        indhold.addWidget(self._db_label)

        btn_db = self._knap("Vælg database …", self._vaelg_database,
                            ikon=QStyle.SP_DirOpenIcon)
        indhold.addWidget(btn_db)

        # Vises indtil en database/datafil er klar.
        self._hint = QLabel(
            "Vælg din VASP-database for at komme i gang. Handlingerne "
            "nedenfor låses op, når datafilen er bygget.")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #a04000;")
        indhold.addWidget(self._hint)
        return boks

    def _import_boks(self, on_importer, on_importer_linje, on_importer_vsp):
        """Afsnit med det der hentes fra VASP ind i QGIS."""
        boks = QGroupBox("Hent ind i GIS")
        indhold = QVBoxLayout(boks)
        indhold.setSpacing(6)
        # Alle tre åbner en valgdialog, derfor "…" på dem alle.
        for tekst, tip, handling in (
            ("Længdeprofil …",
             "Importer et længdeprofil fra VASP som lag i QGIS.",
             on_importer),
            ("Vandløbslinje …",
             "Importer en geokodet vandløbslinje fra VASP som lag i QGIS.",
             on_importer_linje),
            ("Vandspejlsberegning …",
             "Importer en beregnet vandspejlslinje (.ber) som punktlag.",
             on_importer_vsp),
        ):
            knap = self._knap(tekst, handling, tip=tip, handlingsknap=True)
            indhold.addWidget(knap)
        return boks

    def _analyse_boks(self, on_terraen, on_braend_vandloeb,
                      on_afvandingsanalyse):
        """Afsnit med det der regnes ud af terræn og profiler."""
        valg = [(
            "Terræn på profil …",
            "Læg terrænkoter fra DHM ind på et profil og skriv dem "
            "tilbage til VASP.", on_terraen)]
        if on_braend_vandloeb is not None:
            valg.append((
                "Brænd vandløb i terræn …",
                "Brænder tværprofilerne fra et VASP-profil ned i en "
                "terrænmodel.", on_braend_vandloeb))
        if on_afvandingsanalyse is not None:
            valg.append((
                "Afvandingsanalyse …",
                "Beregner afstanden fra terrænet ned til et beregnet "
                "vandspejl fra VASP og klassificerer den i "
                "afvandingsklasser.", on_afvandingsanalyse))

        boks = QGroupBox("Analyser")
        indhold = QVBoxLayout(boks)
        indhold.setSpacing(6)
        for tekst, tip, handling in valg:
            indhold.addWidget(
                self._knap(tekst, handling, tip=tip, handlingsknap=True))
        return boks

    def _bund_raekke(self, on_opdater):
        """Nederste række: vedligehold til venstre, Luk til højre."""
        raekke = QHBoxLayout()
        btn_opdater = self._knap(
            "Genindlæs database", on_opdater,
            tip="Genopbygger datafilen fra VASP-databasen, så nye profiler "
                "kommer med.",
            ikon=QStyle.SP_BrowserReload, handlingsknap=True)
        raekke.addWidget(btn_opdater)
        raekke.addStretch(1)

        btn_luk = QPushButton("Luk")
        btn_luk.setMinimumHeight(_KNAP_HOEJDE)
        btn_luk.setDefault(True)
        btn_luk.clicked.connect(self.reject)
        raekke.addWidget(btn_luk)
        return raekke

    # --- småting ----------------------------------------------------------

    def _knap(self, tekst, handling, tip="", ikon=None, handlingsknap=False):
        """Lav en knap i dialogens fælles stil.

        ``handlingsknap`` betyder at knappen kræver en bygget datafil: den
        lukker dialogen før handlingen og gråtones indtil data er klar.
        """
        knap = QPushButton(tekst)
        knap.setMinimumHeight(_KNAP_HOEJDE)
        if tip:
            knap.setToolTip(tip)
        if ikon is not None:
            knap.setIcon(self.style().standardIcon(ikon))
        if handlingsknap:
            knap.clicked.connect(lambda: self._run(handling))
            self._action_buttons.append(knap)
        else:
            knap.clicked.connect(handling)
        return knap

    def resizeEvent(self, event):
        """Hold database-stien på én linje, uanset dialogens bredde."""
        super().resizeEvent(event)
        self._vis_db_sti()

    def _refresh_db_label(self):
        """Opdatér visningen af den aktuelle database-sti."""
        self._db_sti = self._get_db_path() or ""
        self._db_label.setToolTip(self._db_sti)
        self._vis_db_sti()

    def _vis_db_sti(self):
        """Skriv stien forkortet på midten, så både drev og filnavn ses."""
        tekst = self._db_sti or "(ingen database valgt)"
        bredde = max(160, self._db_label.width())
        metrics = QFontMetrics(self._db_label.font())
        self._db_label.setText(
            metrics.elidedText(tekst, Qt.ElideMiddle, bredde))

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
