"""Oplande fra QGIS omsat til en VASP-oplandsserie.

VASP's oplandsfane er en tabel med station og opland: hvor stort et areal
der afvander til vandløbet ned til den station. En oplandsudpegning i QGIS
giver derimod et polygon pr. udløbspunkt med sit eget areal. Herfra er der
tre skridt:

  1. Hvert udløbspunkt får en station ved at blive projiceret ind på
     VASP-profilets bundlinje, så stationeringen er VASP's egen og ikke en
     afstand målt i QGIS.
  2. Deloplande lægges sammen nedstrøms, så tabellen viser det samlede
     opland ved hver station — det er sådan, VASP læser den.
  3. Punkter på samme station lægges sammen, så tabellen kun har én række
     pr. station.

Hvilken vej "nedstrøms" er, afgøres af bundkoten: vandet løber nedad, så
stiger stationen i takt med at bunden falder, går stationeringen med
strømmen. Er der ingen koter at regne på, antages stigende station at være
nedstrøms — VASP's normale konvention.
"""

import math

MED_STROEMMEN = 1       # nedstrøms = stigende station
MOD_STROEMMEN = -1      # nedstrøms = faldende station
INGEN_AKKUMULERING = 0  # arealerne er allerede fulde oplande


def _punkter(centerline):
    """Træk (station, x, y) ud af bundlinjen, sorteret og uden huller."""
    ud = []
    for p in centerline or []:
        station, x, y = p.get("station"), p.get("x"), p.get("y")
        if station is None or x is None or y is None:
            continue
        ud.append((float(station), float(x), float(y)))
    ud.sort(key=lambda p: p[0])
    return ud


def station_for(centerline, x, y):
    """Find stationen på bundlinjen nærmest punktet (x, y).

    Returnerer (station, afstand til linjen i meter). Punktet projiceres
    ind på det nærmeste linjestykke, så stationen interpoleres mellem
    profilets punkter i stedet for at blive rundet til nærmeste punkt.
    Returnerer (None, None) hvis linjen ikke har brugbare punkter.
    """
    punkter = _punkter(centerline)
    if not punkter:
        return None, None
    if len(punkter) == 1:
        s, px, py = punkter[0]
        return s, math.hypot(x - px, y - py)

    bedst = None
    for (s1, x1, y1), (s2, x2, y2) in zip(punkter, punkter[1:]):
        dx, dy = x2 - x1, y2 - y1
        laengde2 = dx * dx + dy * dy
        if laengde2 <= 0:
            continue
        # Hvor langt henne på stykket ligger punktets fodpunkt (0-1)?
        t = ((x - x1) * dx + (y - y1) * dy) / laengde2
        t = max(0.0, min(1.0, t))
        fx, fy = x1 + t * dx, y1 + t * dy
        afstand = math.hypot(x - fx, y - fy)
        if bedst is None or afstand < bedst[1]:
            bedst = (s1 + t * (s2 - s1), afstand)
    if bedst is None:
        s, px, py = punkter[0]
        return s, math.hypot(x - px, y - py)
    return bedst


def stroemretning(centerline):
    """Gæt hvilken vej stationeringen vender ud fra bundkoterne.

    Returnerer MED_STROEMMEN, MOD_STROEMMEN — eller MED_STROEMMEN hvis der
    ikke er koter nok til at afgøre det. Sammenligner den øverste og den
    nederste tredjedel, så enkelte skæve koter ikke vender resultatet.
    """
    koter = [(float(p["station"]), float(p["kote"]))
             for p in centerline or []
             if p.get("station") is not None and p.get("kote") is not None]
    koter.sort()
    if len(koter) < 6:
        return MED_STROEMMEN
    n = len(koter) // 3
    foerst = sum(k for _, k in koter[:n]) / n
    sidst = sum(k for _, k in koter[-n:]) / n
    if abs(foerst - sidst) < 0.05:      # fladt — ikke noget at gå efter
        return MED_STROEMMEN
    return MED_STROEMMEN if sidst < foerst else MOD_STROEMMEN


def _slaa_sammen(poster):
    """Læg punkter på samme station sammen til én post."""
    samlet = {}
    for station, areal, tekst in poster:
        noegle = round(station, 3)
        if noegle in samlet:
            gammel = samlet[noegle]
            samlet[noegle] = (gammel[0], gammel[1] + areal,
                              "; ".join(t for t in (gammel[2], tekst) if t))
        else:
            samlet[noegle] = (station, areal, tekst)
    return [samlet[n] for n in sorted(samlet)]


def akkumuler(poster, retning=MED_STROEMMEN):
    """Læg deloplandene sammen nedstrøms.

    ``poster`` er (station, areal, bemærkning) pr. udløbspunkt. Resultatet
    er den tabel, VASP skal have: én række pr. station, sorteret efter
    station, hvor arealet er summen af alt det, der ligger opstrøms.

    Med INGEN_AKKUMULERING lægges intet sammen — så er arealerne allerede
    fulde oplande, og rækkerne sorteres blot.
    """
    samlet = _slaa_sammen(poster)
    if retning == INGEN_AKKUMULERING or not samlet:
        return samlet
    # Opstrøms først, så summen vokser i strømmens retning.
    raekkefoelge = samlet if retning == MED_STROEMMEN else list(reversed(samlet))
    sum_ = 0.0
    lagt = []
    for station, areal, tekst in raekkefoelge:
        sum_ += areal
        lagt.append((station, sum_, tekst))
    lagt.sort(key=lambda r: r[0])
    return lagt


def byg_serie(udloeb, centerline, retning=None, maks_afstand=None):
    """Byg rækkerne til en VASP-oplandsserie ud fra udpegede oplande.

    ``udloeb`` er (x, y, areal, bemærkning) pr. opland. Returnerer
    (rækker, noter), hvor rækker er (station, opland, bemærkning) klar til
    at skrive, og noter er en liste af advarsler til brugeren — fx punkter
    der ligger langt fra vandløbet, eller punkter uden station.

    Er ``retning`` ikke angivet, gættes den ud fra bundkoterne.
    ``maks_afstand`` er hvor langt et udløbspunkt må ligge fra linjen, før
    det nævnes som en note (None = nævn det ikke).
    """
    if retning is None:
        retning = stroemretning(centerline)
    poster, noter = [], []
    for x, y, areal, tekst in udloeb:
        station, afstand = station_for(centerline, x, y)
        if station is None:
            noter.append("%s kunne ikke stationeres — vandløbslinjen har "
                         "ingen punkter." % (tekst or "Et opland"))
            continue
        if maks_afstand is not None and afstand > maks_afstand:
            noter.append("%s ligger %s m fra vandløbet (station %s) — "
                         "kontrollér at det er det rigtige vandløb."
                         % (tekst or "Et opland",
                            ("%.0f" % afstand).replace(".", ","),
                            ("%.0f" % station).replace(".", ",")))
        poster.append((station, float(areal), tekst or ""))
    return akkumuler(poster, retning), noter
