"""GUI til valg af det VASP-profil hvis tværprofiler skal brændes i terrænet.

Viser en søgbar liste over de profil-datalag der faktisk har tværprofiler,
med vandløbsnavn, profilnavn og antal tværsnit. Brugeren vælger ét; lgdid
hentes via selected_profile().
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
)
from qgis.PyQt.QtCore import Qt


class TvpDialog(QDialog):
    """Dialog der lader brugeren vælge ét profil-datalag med tværprofiler."""

    def __init__(self, profiler, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Brænd vandløb i terræn — vælg profil")
        self.resize(620, 480)
        self._profiler = profiler

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Vælg det profil-datalag hvis tværprofiler skal brændes ned i "
            "terrænmodellen:"))

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Søg:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrér på vandløb eller profilnavn …")
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search)
        layout.addLayout(search_row)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self._list)
        self._populate(profiler)

        self._hint = QLabel(
            "Vandløbslinjen hentes automatisk fra den linje profilet er "
            "geokodet på. Derefter vælger du terrænmodel og indstillinger.")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._use_mike = False
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        mike_btn = buttons.addButton("Brug MIKE-fil i stedet …",
                                     QDialogButtonBox.ActionRole)
        mike_btn.clicked.connect(self._choose_mike)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_mike(self):
        """Luk dialogen og lad brugeren vælge en MIKE-eksport i stedet."""
        self._use_mike = True
        self.accept()

    def use_mike(self):
        """True hvis brugeren valgte MIKE-filen frem for et VASP-profil."""
        return self._use_mike

    def _label(self, prof):
        vlb = prof["vlbnavn"] or "(ukendt vandløb)"
        dele = []
        if prof["antal_tvp"]:
            dele.append("%d opmålte" % prof["antal_tvp"])
        if prof["antal_param"]:
            dele.append("%d parametriske" % prof["antal_param"])
        tal = ", ".join(dele) or "ingen tværsnit"
        mangler = "" if prof["geocodegdsid"] else "   [ikke geokodet]"
        return "%s  /  %s  —  %s%s" % (vlb, prof["navn"], tal, mangler)

    def _populate(self, profiler):
        self._list.clear()
        for prof in profiler:
            item = QListWidgetItem(self._label(prof))
            item.setData(Qt.UserRole, prof)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _apply_filter(self, text):
        text = text.strip().lower()
        if not text:
            filtered = self._profiler
        else:
            filtered = [
                p for p in self._profiler
                if text in (p["vlbnavn"] or "").lower()
                or text in (p["navn"] or "").lower()
            ]
        self._populate(filtered)

    def selected_profile(self):
        """Returnér den valgte profil-dict, eller None hvis intet er valgt."""
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None
