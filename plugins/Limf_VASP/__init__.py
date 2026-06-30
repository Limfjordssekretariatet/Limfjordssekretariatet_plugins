"""VASP QGIS-plugin — indgangspunkt.

QGIS kalder classFactory(iface) for at instantiere pluginnet.
"""


def classFactory(iface):
    from .vasp_plugin import VaspPlugin
    return VaspPlugin(iface)
