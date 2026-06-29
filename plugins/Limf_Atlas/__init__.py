# -*- coding: utf-8 -*-
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
