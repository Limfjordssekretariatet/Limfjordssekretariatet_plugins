"""Frasortering af de matrikler, der ikke er lodsejere i projektet.

Matriklen spørges med et rektangel (BBOX), for det er det, WFS'en kan. Et
projektområde er sjældent et rektangel, så svaret rummer alt det, der
ligger i hjørnerne udenom — grænser området op til en by eller et
sommerhusområde, kan det alene være hundreder af matrikler. Derfor skæres
svaret til her, ud fra selve polygonet, før der hentes ejeroplysninger. Det
er samtidig det, der tager tid: ét opslag pr. matrikel.

Oveni kan to slags matrikler sorteres fra, som sjældent er lodsejere i
denne sammenhæng:

* **Vejmatrikler.** En udskilt vej kendes på, at hele det registrerede
  areal er vejareal. Det rammer både vejlitra (matrikelnumre i
  7000-serien) og de byveje, der har et almindeligt matrikelnummer — på
  2.598 matrikler i Nordjylland fandt vi 103 vejlitra, som alle havde
  areal = vejareal, og yderligere 30 vejstykker, som kun kendes på det.
* **Jernbane.** Banearealer har ``arealtype`` = "Jernbane" og kun et lille
  vejareal, så de skal findes på arealtypen.

Endelig kan små matrikler sorteres fra. Matriklen oplyser ikke, hvad der
står på en matrikel, så der er ingen "boligtype" at gå efter; men byhuse
og sommerhuse ligger på små grunde, og en nedre arealgrænse er derfor det
nærmeste, man kommer, uden at hente andre registre ind.
"""

#: Hvor meget et jordstykke mindst skal overlappe området for at tælle med.
#: Rører to matrikler kun hinandens skel, er overlappet nul — og naboen er
#: ikke lodsejer i projektet.
MINDSTE_OVERLAP_M2 = 0.5

#: Arealtyper fra Matriklen, der ikke er lodsejere i denne sammenhæng.
BANE_AREALTYPER = ('Jernbane',)

#: Grundene til at en matrikel ikke kom med. Teksterne vises til brugeren.
UDEN_FOR = 'uden for området'
VEJ = 'vejmatrikel'
BANE = 'jernbane'
FOR_LILLE = 'under mindste areal'


def tal(vaerdi):
    """Læs et tal fra WFS-svaret; 0 hvis det ikke er et tal."""
    try:
        return float(vaerdi)
    except (TypeError, ValueError):
        return 0.0


def er_vejmatrikel(jordstykke):
    """Er hele matriklen vejareal — altså en udskilt vej?

    Vejlitra i 7000-serien regnes altid med; ellers afgøres det af, at
    vejarealet dækker hele det registrerede areal.
    """
    nummer = (jordstykke.get('matrikelnummer') or '').strip().lower()
    if nummer.startswith('7000'):
        return True
    areal = tal(jordstykke.get('registreret_areal'))
    vejareal = tal(jordstykke.get('vejareal'))
    return areal > 0 and vejareal >= areal - 0.5


def er_jernbane(jordstykke):
    """Er matriklen et baneareal?"""
    return (jordstykke.get('arealtype') or '').strip() in BANE_AREALTYPER


def overlapper(flade, jordstykke, mindste_overlap=MINDSTE_OVERLAP_M2):
    """Overlapper matriklen området med mere end et skel?

    ``flade`` er projektområdet som QgsGeometry (allerede i EPSG:25832 og
    med den ønskede udvidelse lagt på). Mangler matriklen geometri, tages
    den med — så forsvinder den ikke i det stille.
    """
    from qgis.core import QgsGeometry

    wkt = jordstykke.get('geometri_wkt') or ''
    if not wkt:
        return True
    geom = QgsGeometry.fromWkt(wkt)
    if geom.isEmpty():
        return True
    if not geom.intersects(flade):
        return False
    faelles = geom.intersection(flade)
    if faelles.isEmpty():
        return False
    return faelles.area() > mindste_overlap


def frasorter(jordstykker, flade=None, udelad_vej_og_bane=True,
              mindste_areal_m2=0.0):
    """Skær svaret fra Matriklen til.

    Returnerer (beholdte, grunde), hvor grunde er en dict fra grund til
    antal — så brugeren kan få at vide, hvad der blev sorteret fra og
    hvorfor, i stedet for blot at se et mindre tal.
    """
    beholdte = []
    grunde = {}

    def tael(grund):
        grunde[grund] = grunde.get(grund, 0) + 1

    for js in jordstykker:
        if flade is not None and not overlapper(flade, js):
            tael(UDEN_FOR)
            continue
        if udelad_vej_og_bane and er_jernbane(js):
            tael(BANE)
            continue
        if udelad_vej_og_bane and er_vejmatrikel(js):
            tael(VEJ)
            continue
        if (mindste_areal_m2 > 0
                and tal(js.get('registreret_areal')) < mindste_areal_m2):
            tael(FOR_LILLE)
            continue
        beholdte.append(js)
    return beholdte, grunde


def forklar(grunde):
    """Én linje om, hvad der blev sorteret fra. Tom tekst hvis ingenting."""
    if not grunde:
        return ''
    raekkefoelge = (UDEN_FOR, VEJ, BANE, FOR_LILLE)
    dele = ['%d %s' % (grunde[g], g) for g in raekkefoelge if grunde.get(g)]
    for grund, antal in grunde.items():
        if grund not in raekkefoelge:
            dele.append('%d %s' % (antal, grund))
    return 'Sorteret fra: ' + ', '.join(dele) + '.'
