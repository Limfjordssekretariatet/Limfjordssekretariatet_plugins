"""Delte hjælpefunktioner til alle Vaadomraade_Modeller scripts.

Importér herfra i stedet for at kopiere logikken ind i hvert script:

    from utils import find_output_filer, find_resultater_mappe, get_resultat_excel_navn
"""
import os
import re


# ---------------------------------------------------------------------------
# Plugin-mappen — Grunddata følger ALTID med pluginnet
# ---------------------------------------------------------------------------

# utils.py ligger i <plugin>/Scripts/, så plugin-roden er én mappe op.
_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_PLUGIN_GRUNDDATA = os.path.join(_PLUGIN_ROOT, 'Grunddata')


def find_grunddata():
    """Returnerer pluginnets Grunddata-mappe (følger altid med pluginnet) eller None.

    Grunddata er input-data og er en del af pluginnet — den afhænger IKKE af
    hvilken outputmappe brugeren har valgt.
    """
    return _PLUGIN_GRUNDDATA if os.path.isdir(_PLUGIN_GRUNDDATA) else None


# ---------------------------------------------------------------------------
# QgsSettings-opslag
# ---------------------------------------------------------------------------

def _hent_settings():
    """Returnerer (rod, projekt, omraade) fra QgsSettings."""
    from qgis.core import QgsSettings
    s = QgsSettings()
    return (
        s.value('vaadomraade_modeller/mappe', ''),
        s.value('vaadomraade_modeller/projektnavn', ''),
        s.value('vaadomraade_modeller/projektomraade_navn', ''),
    )


def _projektomraade_kandidater(rod, projekt, omraade):
    """Returnerer mulige projektområde-mapper i prioriteret rækkefølge.

    Pluginnet bruger normalt skemaet <rod>/<projekt>/<omraade>/, men ældre
    eller manuelt oprettede projekter kan have et fladere skema (fx når projekt
    og område har samme navn, eller projektnavnet mangler). Vi prøver derfor
    flere varianter, så både gamle og nye projekter virker."""
    kand = []
    if rod and projekt and omraade:
        kand.append(os.path.join(rod, projekt, omraade))   # standard 3-niveau
    if rod and omraade:
        kand.append(os.path.join(rod, omraade))            # 2-niveau (projekt mangler/ens)
    if rod and projekt:
        kand.append(os.path.join(rod, projekt))            # projekt == område
    # fjern dubletter, bevar rækkefølge
    set_, ud = set(), []
    for k in kand:
        kn = os.path.normpath(k).lower()
        if kn not in set_:
            set_.add(kn); ud.append(k)
    return ud


def _find_undermappe(undernavn, opret=False):
    """Finder <projektområde>/<undernavn> på tværs af mulige skemaer.

    Returnerer den første der findes. Hvis ingen findes og opret=True,
    oprettes undermappen i den FØRSTE kandidat hvis projektmappen findes.
    Returnerer None hvis intet kan lade sig gøre."""
    rod, projekt, omraade = _hent_settings()
    if not (rod and omraade):
        return None
    kandidater = _projektomraade_kandidater(rod, projekt, omraade)
    # 1) returnér en eksisterende undermappe
    for base in kandidater:
        sti = os.path.join(base, undernavn)
        if os.path.isdir(sti):
            return sti
    # 2) opret i den første kandidat hvis selve projektområde-mappen findes
    if opret:
        for base in kandidater:
            if os.path.isdir(base):
                sti = os.path.join(base, undernavn)
                try:
                    os.makedirs(sti, exist_ok=True)
                    return sti
                except Exception:
                    pass
    return None


def find_output_filer():
    """Returnerer Outputfiler_<omraade>/ (robust over for flade skemaer) eller None."""
    _, _, omraade = _hent_settings()
    if not omraade:
        return None
    return _find_undermappe(f'Outputfiler_{omraade}')


def find_resultater_mappe():
    """Returnerer Resultater/ (robust over for flade skemaer) eller None.

    Resultater oprettes hvis projektmappen findes, så Excel-filen altid kan skrives."""
    return _find_undermappe('Resultater', opret=True)


def get_resultat_excel_navn():
    """Returnerer Resultat_<omraade>.xlsx."""
    _, _, omraade = _hent_settings()
    return f'Resultat_{omraade}.xlsx' if omraade else 'Resultat.xlsx'


# ---------------------------------------------------------------------------
# Navne-sanitering
# ---------------------------------------------------------------------------

def sanitize_naam(s):
    """Fjern Windows-ugyldige tegn fra et fil/mappenavn.

    Bruges til at beregne mappe-stier — logikken er identisk med den
    der bruges når mappen oprettes, så opslag og oprettelse altid matcher.
    """
    ugyldige = '<>:"/\\|?*'
    return ''.join(c for c in s if c not in ugyldige).strip()


def sanitize_filnavn(s):
    """Erstat ugyldige og specielle tegn med underscore. Bruges til output-filnavne."""
    return re.sub(r'[\s<>:"/\\|?*()\[\]%&]+', '_', s).strip('_') or 'output'
