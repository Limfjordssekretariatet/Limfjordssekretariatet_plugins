# -*- coding: utf-8 -*-
"""Processing-udbyderen for støttepunkterne.

Udbyderens id er "afvanding", som i det selvstændige plugin værktøjet kom
fra. Algoritmens fulde id bliver dermed det samme
(``afvanding:udfyld_afvanding``), og en model, der allerede bruger den,
virker uændret. Det betyder også, at det gamle plugin skal afinstalleres:
QGIS afviser en anden udbyder med samme id.
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .algoritme import UdfyldAfvanding


class AfvandingProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(UdfyldAfvanding())

    def id(self):
        return "afvanding"

    def name(self):
        return "Afvanding"

    def longName(self):
        return "Afvanding — Vandprojekter"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "ikon.png"))
