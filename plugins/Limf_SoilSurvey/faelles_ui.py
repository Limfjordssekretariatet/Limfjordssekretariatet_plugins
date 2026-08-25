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

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QFontMetrics, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# Hvor stor en del af skærmen en dialog højst må fylde i højden. Resten
# lades til proceslinje og vindueskant.
SKAERM_ANDEL = 0.85

MARGIN = 14           # luft ud til dialogens kant
AFSTAND = 12          # mellem afsnit
AFSNIT_AFSTAND = 6    # mellem felter inde i et afsnit
KNAP_HOEJDE = 30

# Objektnavn på rullepanelet, så det kan genfindes og ikke lægges dobbelt.
RULLEPANEL = "VandprojekterRullepanel"


def anvend_stil(dialog):
    """Giv en færdigbygget dialog husets udtryk.

    Retter margener, afstande og knaphøjder og lægger indholdet i et
    rullepanel, så dialogen kan bruges på en lille skærm. Selve opbygningen
    røres ikke, så den virker på både håndskrevne og Designer-byggede
    dialoger.
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
    # Til sidst: gør dialogen rulbar, så den kan bruges på en lille skærm.
    goer_rulbar(dialog)


class _Rullepanel(QScrollArea):
    """Rullepanel der udadtil fylder lige så meget som sit indhold.

    Et almindeligt QScrollArea melder højst en ønskehøjde på 24 linjer,
    uanset hvad der ligger i det. Dialogerne ville derfor åbne lavere end
    før og rulle med det samme. Her spørges indholdet i stedet, mens
    mindstemålet lades urørt — så vinduet stadig kan trækkes mindre, og
    rullebjælken først dukker op når der er brug for den.
    """

    def sizeHint(self):
        indhold = self.widget()
        if indhold is None:
            return super().sizeHint()
        maal = indhold.sizeHint()
        ramme = 2 * self.frameWidth()
        return QSize(maal.width() + ramme, maal.height() + ramme)


def goer_rulbar(dialog):
    """Læg dialogens indhold i et rullepanel og hold den inden for skærmen.

    Uden det kan en høj dialog række ud over skærmkanten på en lille skærm,
    så knapperne i bunden ikke kan nås. Panelet er sat til at følge
    vinduets størrelse (``setWidgetResizable``), så indholdet fylder som
    før på en stor skærm; rullebjælken dukker først op, når vinduet er
    mindre end indholdets mindstemål. Derfor beholder fx en profil-liste
    sin egen rulning og bliver ikke pakket ind i to lag.

    Kaldes fra ``anvend_stil()``. Kald den ikke to gange på samme dialog —
    det tjekkes på objektnavnet.
    """
    layout = dialog.layout()
    if layout is None or dialog.findChild(QScrollArea, RULLEPANEL):
        return

    indhold = QWidget()
    indhold.setLayout(layout)

    panel = _Rullepanel(dialog)
    panel.setObjectName(RULLEPANEL)
    panel.setWidgetResizable(True)
    panel.setFrameShape(QFrame.NoFrame)
    panel.setWidget(indhold)

    ydre = QVBoxLayout(dialog)
    ydre.setContentsMargins(0, 0, 0, 0)
    ydre.setSpacing(0)
    ydre.addWidget(panel)

    skaerm = QGuiApplication.primaryScreen()
    if skaerm is not None:
        hoejst = int(skaerm.availableGeometry().height() * SKAERM_ANDEL)
        if hoejst > 200:
            dialog.setMaximumHeight(hoejst)


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
