# -*- coding: utf-8 -*-
# Vandprojekter — Lodsejeratlas
# Copyright (C) 2026 Limfjordsrådets sekretariat
# Hovedudvikler: Henrik Rosenskjold <henrik.rosenskjold@aalborg.dk>
# Krediteres ved videreudvikling — se LICENSE og README.
#
# Dette program er fri software: du må videredistribuere det og/eller ændre
# det under betingelserne i GNU General Public License, som udgivet af Free
# Software Foundation — enten version 2, eller (efter dit valg) en senere
# version. Licensteksten ligger i LICENSE sammen med pluginnet.
#
# Programmet udgives i håb om at det er nyttigt, men UDEN NOGEN GARANTI.

"""Atlas Mapbook – QGIS plugin entry point.

QGIS kalder classFactory() ved indlæsning af pluginnet.
"""


def classFactory(iface):  # pylint: disable=invalid-name
    """Load the AtlasMapbook plugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .atlas import AtlasMapbook
    return AtlasMapbook(iface)
