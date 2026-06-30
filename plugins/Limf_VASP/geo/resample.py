"""Resampling af et vandløbsforløb til faste stationeringspunkter.

Tager forløbets mellempunkter (X, Y, station, kote) og genererer nye punkter
med et fast interval langs stationeringen ved lineær interpolation. Ren
geometri/matematik — ingen QGIS- eller databaseafhængighed.
"""


def _interp(a, b, t):
    """Lineær interpolation mellem a og b (t i [0, 1])."""
    return a + (b - a) * t


def resample_centerline(points, interval):
    """Resample et forløb til faste stationeringspunkter.

    points:  liste af dicts med 'station', 'x', 'y', 'kote', sorteret efter
             station og uden dubletstationer.
    interval: ønsket afstand mellem stationeringspunkter (i meter, > 0).

    Returnerer en liste af dicts med 'station', 'x', 'y', 'kote'. Stationer
    lægges på multipla af interval inden for forløbets [station_min,
    station_max], plus et slutpunkt ved station_max hvis det ikke rammes.
    Hver ny stations X/Y/kote interpoleres mellem de to omkringliggende
    mellempunkter.
    """
    if interval <= 0:
        raise ValueError("Interval skal være større end 0.")
    if len(points) < 2:
        # Kan ikke interpolere; returnér det vi har.
        return list(points)

    st_min = points[0]["station"]
    st_max = points[-1]["station"]

    # Byg listen af mål-stationer. Første punkt er forløbets start-station
    # (fx 5 hvis forløbet starter ved 5), derefter start + interval,
    # start + 2*interval osv. op til slutstationen.
    targets = []
    k = 0
    # Gang op fra et heltal for at undgå akkumuleret flydetals-fejl.
    s = st_min
    while s <= st_max + 1e-9:
        targets.append(s)
        k += 1
        s = st_min + k * interval
    # Tag altid slutstationen med, hvis den ikke lige blev ramt.
    if targets[-1] < st_max - 1e-9:
        targets.append(st_max)

    result = []
    j = 0  # indeks i points; segmentet points[j]..points[j+1]
    for st in targets:
        # Ryk frem til segmentet der indeholder st.
        while j < len(points) - 2 and points[j + 1]["station"] < st:
            j += 1
        p0 = points[j]
        p1 = points[j + 1]
        span = p1["station"] - p0["station"]
        t = 0.0 if span == 0 else (st - p0["station"]) / span
        t = max(0.0, min(1.0, t))
        result.append({
            "station": st,
            "x": _interp(p0["x"], p1["x"], t),
            "y": _interp(p0["y"], p1["y"], t),
            "kote": _interp(p0["kote"], p1["kote"], t),
        })
    return result
