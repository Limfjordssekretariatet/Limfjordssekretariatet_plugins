"""Geometri- og lagbehandling for VASP-pluginnet (jf. CLAUDE.md:
al geometri-behandling samles her).

Indeholder al kode der omsætter rå data fra dbaccess til QGIS-objekter.
Ingen SQL/pyodbc her. Mappen hedder 'geo' (ikke 'processing') for ikke at
kollidere med QGIS' eget 'processing'-framework, som offset.py importerer.
"""
