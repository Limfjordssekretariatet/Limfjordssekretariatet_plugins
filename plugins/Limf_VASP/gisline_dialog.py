"""GUI til valg af en vandløbslinje (VANDLØBGIS) der skal importeres til GIS.

Viser en søgbar liste over alle vandløbslinjer med vandløbsnavn, linjenavn og
længde. Brugeren vælger én; gisdataid hentes via selected_line().
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


class GisLineDialog(QDialog):
    """Dialog der lader brugeren vælge én vandløbslinje."""

    def __init__(self, linjer, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Importer vandløbslinje til GIS")
        self.resize(560, 460)
        self._linjer = linjer

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Vælg den vandløbslinje der skal hentes ind i QGIS:"))

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Søg:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrér på vandløb eller linjenavn …")
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search)
        layout.addLayout(search_row)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self._list)
        self._populate(linjer)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _label(self, linje):
        laengde = linje["laengde"]
        laengde_txt = "%.0f m" % laengde if laengde is not None else "? m"
        vlb = linje["vlbnavn"] or "(ukendt vandløb)"
        return "%s  /  %s  —  %s" % (vlb, linje["navn"], laengde_txt)

    def _populate(self, linjer):
        self._list.clear()
        for linje in linjer:
            item = QListWidgetItem(self._label(linje))
            item.setData(Qt.UserRole, linje)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _apply_filter(self, text):
        text = text.strip().lower()
        if not text:
            filtered = self._linjer
        else:
            filtered = [
                l for l in self._linjer
                if text in (l["vlbnavn"] or "").lower()
                or text in (l["navn"] or "").lower()
            ]
        self._populate(filtered)

    def selected_line(self):
        """Returnér den valgte linje-dict, eller None hvis intet er valgt."""
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None
