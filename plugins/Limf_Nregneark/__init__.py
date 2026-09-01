# -*- coding: utf-8 -*-
# Vandprojekter — N-regneark
# Copyright (C) 2026 Limfjordssekretariatet
#
# Dette program er fri software: du må videredistribuere det og/eller ændre
# det under betingelserne i GNU General Public License, som udgivet af Free
# Software Foundation — enten version 2, eller (efter dit valg) en senere
# version. Licensteksten ligger i LICENSE sammen med pluginnet.
#
# Programmet udgives i håb om at det er nyttigt, men UDEN NOGEN GARANTI.

def classFactory(iface):
    from .Interface.modelmappe_plugin import ModelMappePlugin
    return ModelMappePlugin(iface)
