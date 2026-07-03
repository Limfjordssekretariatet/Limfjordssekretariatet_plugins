"""GUI til valg af en vandspejlsberegning der skal importeres til GIS.

Viser en søgbar liste over beregninger (simpel + multi) med projekt, navn og
type. Brugeren vælger én; hentes via selected_calc().
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


class VspDialog(QDialog):
    """Dialog der lader brugeren vælge én vandspejlsberegning."""

    def __init__(self, calcs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Importer vandspejlsberegning til GIS")
        self.resize(600, 480)
        self._calcs = calcs

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Vælg den vandspejlsberegning der skal hentes ind i QGIS:"))

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Søg:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrér på projekt eller navn …")
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search)
        layout.addLayout(search_row)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self._list)
        self._populate(calcs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _label(self, c):
        typ = "MULTI" if c["multi"] else "simpel"
        prj = c["prjnavn"] or "(ukendt projekt)"
        return "[%s]  %s  /  %s" % (typ, prj, c["navn"])

    def _populate(self, calcs):
        self._list.clear()
        for c in calcs:
            item = QListWidgetItem(self._label(c))
            item.setData(Qt.UserRole, c)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _apply_filter(self, text):
        text = text.strip().lower()
        if not text:
            filtered = self._calcs
        else:
            filtered = [
                c for c in self._calcs
                if text in (c["prjnavn"] or "").lower()
                or text in (c["navn"] or "").lower()
            ]
        self._populate(filtered)

    def selected_calc(self):
        """Returnér den valgte beregnings-dict, eller None."""
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None
