"""GUI til valg af en vandspejlsberegning — og af et scenarie i den.

VspDialog viser en søgbar liste over beregninger (simpel + multi) med projekt,
navn og type; brugeren vælger én, som hentes via selected_calc().
ScenarieDialog vælger bagefter ét scenarie i en multiberegning.
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


from . import faelles_ui

class VspDialog(QDialog):
    """Dialog der lader brugeren vælge én vandspejlsberegning."""

    def __init__(self, calcs, parent=None,
                 titel="Importer vandspejlsberegning til GIS",
                 intro="Vælg den vandspejlsberegning der skal hentes ind i "
                       "QGIS:"):
        super().__init__(parent)
        self.setWindowTitle(titel)
        self.resize(600, 480)
        self._calcs = calcs

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(intro))

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Søg:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Filtrér på vandløb, projekt eller navn …")
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

        faelles_ui.anvend_stil(self)

    def _label(self, c):
        typ = "MULTI" if c["multi"] else "simpel"
        prj = c["prjnavn"] or "(ukendt projekt)"
        vlb = c.get("vlbnavn") or ""
        # Vandløbet først, som i de øvrige valglister. Projektet hedder ofte
        # noget andet end vandløbet, fx "Ryddet Lavbundsprojekt" / "Hasseris å".
        if vlb:
            return "%s  /  %s  —  [%s] %s" % (vlb, c["navn"], typ, prj)
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
                if text in (c.get("vlbnavn") or "").lower()
                or text in (c["prjnavn"] or "").lower()
                or text in (c["navn"] or "").lower()
            ]
        self._populate(filtered)

    def selected_calc(self):
        """Returnér den valgte beregnings-dict, eller None."""
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None


class ScenarieDialog(QDialog):
    """Dialog der lader brugeren vælge ét scenarie i en multiberegning."""

    def __init__(self, scenarier, calc_navn, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vælg scenarie")
        self.resize(460, 360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "'%s' er en multiberegning. Vælg det scenarie der skal bruges:"
            % calc_navn))

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        for i, scen in enumerate(scenarier):
            navn = (scen.get("navn") or "").strip() or "Scenarie %d" % (i + 1)
            item = QListWidgetItem(
                "%s  —  %d punkter" % (navn, len(scen.get("points") or [])))
            item.setData(Qt.UserRole, i)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        faelles_ui.anvend_stil(self)

    def selected_index(self):
        """Returnér indekset på det valgte scenarie, eller None."""
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None
