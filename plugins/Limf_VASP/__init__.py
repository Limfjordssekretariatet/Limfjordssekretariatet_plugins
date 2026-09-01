# -*- coding: utf-8 -*-
# Vandprojekter — VASP
# Copyright (C) 2026 Limfjordssekretariatet
#
# Dette program er fri software: du må videredistribuere det og/eller ændre
# det under betingelserne i GNU General Public License, som udgivet af Free
# Software Foundation — enten version 2, eller (efter dit valg) en senere
# version. Licensteksten ligger i LICENSE sammen med pluginnet.
#
# Programmet udgives i håb om at det er nyttigt, men UDEN NOGEN GARANTI.

"""VASP QGIS-plugin — indgangspunkt.

QGIS kalder classFactory(iface) for at instantiere pluginnet.
"""


def classFactory(iface):
    from .vasp_plugin import VaspPlugin
    return VaspPlugin(iface)
