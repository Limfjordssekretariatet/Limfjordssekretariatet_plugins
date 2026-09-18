# -*- coding: utf-8 -*-
"""Processing-udbyderen for Udpeg opland.

Udbyderens id er "udpegopland", som i det selvstændige plugin værktøjet kom
fra. Algoritmens fulde id bliver dermed det samme
(``udpegopland:udpeg_opland``), og en model eller en kørsel i historikken,
der bruger den, virker uændret. Er det gamle plugin stadig installeret,
afviser QGIS den ene af de to udbydere.
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .algoritme import UdpegOpland

ALGORITME_ID = "udpegopland:udpeg_opland"


class UdpegOplandProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(UdpegOpland())

    def id(self):
        return "udpegopland"

    def name(self):
        return "Udpeg opland"

    def longName(self):
        return "Udpeg opland — Vandprojekter"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "ikon.png"))
