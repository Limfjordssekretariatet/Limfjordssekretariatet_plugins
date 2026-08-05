# -*- coding: utf-8 -*-
"""Fælles udtryk for Vandprojekter-dialogerne.

Holder de få mål der får dialogerne til at ligne hinanden — margener,
afstande og knaphøjde — samt et par byggeklodser til dialoger skrevet i
Python. ``anvend_stil()`` virker også på dialoger hentet fra en Qt
Designer-fil, fordi den kun retter afstande og knapper og lader selve
opbygningen være.

Filen er ens i alle pluginnene. QGIS-plugins kan ikke importere fra
hinanden, så den kopieres. Rettes den, skal den rettes alle seks steder.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFontMetrics
from qgis.PyQt.QtWidgets import (
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
)

MARGIN = 14           # luft ud til dialogens kant
AFSTAND = 12          # mellem afsnit
AFSNIT_AFSTAND = 6    # mellem felter inde i et afsnit
KNAP_HOEJDE = 30


def anvend_stil(dialog):
    """Giv en færdigbygget dialog husets udtryk.

    Ændrer kun margener, afstande og knaphøjder, så den kan bruges på både
    håndskrevne og Designer-byggede dialoger uden at flytte rundt på noget.
    """
    layout = dialog.layout()
    if layout is not None:
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(AFSTAND)
    for boks in dialog.findChildren(QGroupBox):
        if boks.layout() is not None:
            boks.layout().setSpacing(AFSNIT_AFSTAND)
    for knap_ in dialog.findChildren(QPushButton):
        if knap_.minimumHeight() < KNAP_HOEJDE:
            knap_.setMinimumHeight(KNAP_HOEJDE)
    for boks in dialog.findChildren(QDialogButtonBox):
        boks.setCenterButtons(False)


def afsnit(titel, forael=None):
    """Gruppeboks med husets afstande. Returnerer (boks, layout)."""
    boks = QGroupBox(titel, forael)
    layout = QVBoxLayout(boks)
    layout.setSpacing(AFSNIT_AFSTAND)
    return boks, layout


def knap(tekst, handling=None, tip="", primaer=False):
    """Knap i husets stil."""
    k = QPushButton(tekst)
    k.setMinimumHeight(KNAP_HOEJDE)
    if tip:
        k.setToolTip(tip)
    if handling is not None:
        k.clicked.connect(handling)
    if primaer:
        k.setDefault(True)
    return k


def bundraekke(*hoejre, **kwargs):
    """Nederste række: vedligehold til venstre, det man trykker på til højre.

    ``venstre`` er en valgfri liste af knapper der skal stå yderst til
    venstre; resten lægges til højre i den rækkefølge de gives.
    """
    raekke = QHBoxLayout()
    for k in kwargs.get("venstre", ()):
        raekke.addWidget(k)
    raekke.addStretch(1)
    for k in hoejre:
        raekke.addWidget(k)
    return raekke


def forkort_sti(label, sti, mindst=160):
    """Skriv en sti forkortet på midten, så både drev og filnavn kan ses."""
    metrics = QFontMetrics(label.font())
    bredde = max(mindst, label.width())
    label.setToolTip(sti or "")
    label.setText(metrics.elidedText(
        sti or "(ingen valgt)", Qt.ElideMiddle, bredde))
