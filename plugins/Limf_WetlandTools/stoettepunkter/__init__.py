# -*- coding: utf-8 -*-
"""Støttepunkter til afvandingsanalysen.

Et standardiseret punktlag, hvor hvert punkt bærer terrænkoten fra en
højdemodel og den ønskede afvandingsklasse nu og i fremtiden (sommermiddel).
Af klassen udledes afvandingsdybden, og af den og terrænkoten
vandspejlskoten — de punkter, afvandingsanalysen interpolerer imellem.

Tre dele:

  nyt_lag     opretter en GeoPackage med felter, dropdowns og afledte
              felter. Stilen skrives ind i selve filen, så laget opfører sig
              ens i ethvert projekt.
  algoritme   Processing-algoritmen der henter koterne og genberegner de
              afledte felter. Ligger i værktøjskassen under Afvanding, så den
              kan indgå i en model eller køres i batch.
  panel       dialogen der samler de tre trin.
"""
