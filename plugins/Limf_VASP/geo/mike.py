# -*- coding: utf-8 -*-
"""Geometri for tværprofiler i MIKE-stil (profilerne kommer fra VASP).

Indeholder al beregning der ikke kræver QGIS: opbygning af profilerne fra
VASP-databasen, forankring af dem, snapping til en centerlinje og opbygning af
den korridor (stationer x offsets) der skal brændes ned i terraenmodellen.

Metoden foelger principperne fra Klimadatastyrelsens rivertopo
(https://github.com/Klimadatastyrelsen/rivertopo):

* et profil behandles som en funktion z(offset) der evalueres paa et fast
  offset-gitter -- ikke som en punktsky med profil-afhaengig punktafstand.
  Dermed svarer punkt nr. i i to naboprofiler til samme fysiske afstand fra
  vandloebets bund, og der interpoleres ikke mellem en brink og en bund.
* offset maales fra profilets dybeste punkt (thalweg) eller fra MIKE's
  markoer 2, ikke fra bredde-midten. Ellers vandrer den indbraendte kanal
  sidelaens fra profil til profil.
* placeringen paa centerlinjen findes ved vektorprojektion paa hvert segment,
  saa baade stationering og vinkelret afstand er kendt og kan valideres.
"""

from bisect import bisect_right
from collections import namedtuple

import numpy as np

# MIKE-markoer 2 er konventionelt profilets laveste punkt (bundpunkt).
MARKER_BOTTOM = 2

ANCHOR_AUTO = "auto"
ANCHOR_THALWEG = "thalweg"
ANCHOR_MARKER = "marker"
ANCHOR_MIDDLE = "midte"


class MikeProfile:
    """Ét tvaerprofil.

    ``dist`` er raa afstand langs profilet som den staar i filen (0 i profilets
    venstre ende), ``offsets`` er den samme akse flyttet saa 0 ligger i
    profilets nulpunkt (se :meth:`set_anchor`).
    """

    def __init__(self, name, station, base_x, base_y, dist, z, markers,
                 block_no):
        order = np.argsort(dist, kind="stable")
        self.name = name
        self.station = station
        self.base_x = base_x
        self.base_y = base_y
        self.dist = np.asarray(dist, dtype=float)[order]
        self.z = np.asarray(z, dtype=float)[order]
        self.markers = np.asarray(markers, dtype=int)[order]
        self.block_no = block_no
        self.anchor = 0.0
        self.anchor_kind = None
        self.offsets = self.dist.copy()
        # Udfyldes af kalderen naar profilet er snappet til centerlinjen.
        self.chainage = None
        self.snap_dist = None
        self.snap_offset = None
        self.part = None

    # --- forankring -------------------------------------------------------

    def thalweg(self):
        """Position (paa dist-aksen) af profilets dybeste punkt.

        Ligger flere punkter lige lavt, bruges deres midte -- som i rivertopo's
        OpmaaltProfil.
        """
        lowest = self.dist[self.z == self.z.min()]
        return float(np.mean(lowest))

    def marker_pos(self, marker=MARKER_BOTTOM):
        """Position af en MIKE-markoer, eller None hvis den ikke findes."""
        hit = self.dist[self.markers == marker]
        if hit.size == 0:
            return None
        return float(np.mean(hit))

    def middle(self):
        """Bredde-midten -- den gamle metode, medtaget til sammenligning."""
        return float((self.dist[0] + self.dist[-1]) / 2.0)

    def set_anchor(self, mode):
        """Vaelg profilets nulpunkt og beregn ``offsets``."""
        if mode == ANCHOR_MIDDLE:
            anchor, kind = self.middle(), ANCHOR_MIDDLE
        elif mode == ANCHOR_MARKER:
            pos = self.marker_pos()
            anchor, kind = ((pos, ANCHOR_MARKER) if pos is not None
                            else (self.thalweg(), ANCHOR_THALWEG))
        elif mode == ANCHOR_AUTO:
            pos = self.marker_pos()
            anchor, kind = ((pos, ANCHOR_MARKER) if pos is not None
                            else (self.thalweg(), ANCHOR_THALWEG))
        else:
            anchor, kind = self.thalweg(), ANCHOR_THALWEG
        self.anchor = anchor
        self.anchor_kind = kind
        self.offsets = self.dist - anchor
        return kind

    # --- opslag -----------------------------------------------------------

    def interp(self, offsets):
        """Profilets kote i de oenskede offsets.

        Uden for profilets egen bredde returneres NaN -- der gaettes altsaa
        ikke terraen hvor profilet ikke naaer hen (samme valg som rivertopo).
        """
        return np.interp(offsets, self.offsets, self.z,
                         left=np.nan, right=np.nan)


# ---------------------------------------------------------------------------
# Profiler fra VASP-databasen
# ---------------------------------------------------------------------------

# TVPDATAEXT.TVPTYPEKODE
VASP_OPMAALT = 0
VASP_SIMPEL = 4
VASP_SAMMENSAT = 5


def profile_from_survey(section, nr=0):
    """Byg et profil ud fra ét opmaalt VASP-tvaersnit (TVPTYPEKODE 0).

    ``section`` er en post fra ``dbaccess.read_cross_sections``: afstand langs
    tvaersnittet og kote pr. punkt. Der er ingen markoerer i VASP-data, saa
    profilet forankres i sit dybeste punkt.
    """
    punkter = section.get("punkter") or []
    if len(punkter) < 2:
        return None
    dist = np.array([p[0] for p in punkter], dtype=float)
    z = np.array([p[1] for p in punkter], dtype=float)
    station = section.get("station")
    navn = ("st. %.1f" % station) if station is not None else ("tvp %s" % nr)
    return MikeProfile(navn, station, section.get("x"), section.get("y"),
                       dist, z, np.zeros(len(dist), dtype=int), nr)


def profile_from_simple(row, nr=0, height=2.0):
    """Byg et profil ud fra en simpel geometri (TVPTYPEKODE 4).

    VASP gemmer den som bundkote (``p2``, i cm), bundbredde (``p3``, i m) og
    anlaeg (``p4``, symmetrisk). Det omsaettes til en trapez der foeres
    ``height`` meter op over bunden. Hoejden er ligegyldig for resultatet:
    brinker der ligger over terraenet aendrer alligevel ingenting, fordi der
    braendes med min(DHM, profil).
    """
    bundkote_cm = row.get("p2")
    bundbredde = row.get("p3")
    anlaeg = row.get("p4")
    if bundkote_cm is None:
        return None
    bundkote = bundkote_cm / 100.0 + (row.get("dnnaddent") or 0.0)
    bundbredde = float(bundbredde) if bundbredde else 0.0
    anlaeg = float(anlaeg) if anlaeg else 0.0

    halv = bundbredde / 2.0
    top = halv + anlaeg * height
    if top <= 0:
        # Hverken bredde eller anlaeg: intet at braende.
        return None
    if anlaeg > 0:
        dist = np.array([0.0, top - halv, top + halv, 2.0 * top])
        z = np.array([bundkote + height, bundkote, bundkote,
                      bundkote + height])
    else:
        # Lodrette sider: kun bunden er kendt.
        dist = np.array([0.0, bundbredde])
        z = np.array([bundkote, bundkote])

    station = row.get("station")
    navn = ("st. %.1f (simpel)" % station) if station is not None \
        else ("simpel %s" % nr)
    return MikeProfile(navn, station, row.get("x"), row.get("y"),
                       dist, z, np.zeros(len(dist), dtype=int), nr)


def profiles_from_vasp(sections, params=None, height=2.0):
    """Byg profiler ud fra VASP-data.

    Opmaalte tvaersnit (type 0) og simple geometrier (type 4) bruges.
    Sammensatte geometrier (type 5) springes over: kun 25 raekker i basen, og
    betydningen af flere af deres PARAM-felter er ikke fastlagt — det er bedre
    at sige det hoejt end at braende en gaettet form ned i terraenet.

    :return: ``(profiler, advarsler)``
    """
    profiles = []
    warnings = []

    for i, section in enumerate(sections or []):
        prof = profile_from_survey(section, nr=i)
        if prof is None:
            continue
        profiles.append(prof)

    n_simpel = 0
    n_sammensat = 0
    for i, row in enumerate(params or []):
        kode = row.get("typekode")
        if kode == VASP_SIMPEL:
            prof = profile_from_simple(row, nr=i, height=height)
            if prof is not None:
                profiles.append(prof)
                n_simpel += 1
        elif kode == VASP_SAMMENSAT:
            n_sammensat += 1

    if n_sammensat:
        warnings.append(
            "%d tvaersnit er 'Sammensat geometri' (TVPTYPEKODE 5) og "
            "springes over — formen af de felter er ikke fastlagt."
            % n_sammensat)
    if n_simpel:
        warnings.append(
            "%d tvaersnit er 'Simpel geometri' og er omsat til en trapez "
            "(bundkote, bundbredde, anlaeg) foert %.1f m op over bunden."
            % (n_simpel, height))

    # Profiler uden koordinater kan ikke placeres ved snapping; de faar
    # koordinater fra stationeringen af kalderen.
    return profiles, warnings


# ---------------------------------------------------------------------------
# Centerlinje: snapping og stationering
# ---------------------------------------------------------------------------

Snap = namedtuple("Snap", ["part", "chainage", "offset", "dist"])


def cumulative_length(points):
    """Kumuleret laengde til hvert knaekpunkt paa en polylinje."""
    seg = np.diff(points, axis=0)
    return np.concatenate([[0.0], np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))])


def snap_point(x, y, parts):
    """Find det naermeste punkt paa en samling polylinjer.

    ``parts`` er en liste af ``(navn, punkter Nx2, kumuleret laengde)``.
    Beregningen er en ren vektorprojektion paa hvert linjesegment (som
    rivertopo's snapping.py), saa vi faar baade stationeringen langs linjen og
    den vinkelrette, fortegnsbestemte afstand ud. Afstanden bruges til at
    afvise profiler der slet ikke hoerer til linjen.

    Fortegn paa offset: positiv = hoejre side set i linjens retning.
    """
    best = None
    for part_index, (_, points, cum) in enumerate(parts):
        if len(points) < 2:
            continue
        start = points[:-1]
        vec = np.diff(points, axis=0)
        seg_len2 = (vec ** 2).sum(axis=1)
        safe = np.where(seg_len2 > 0.0, seg_len2, 1.0)

        rel = np.array([x, y], dtype=float) - start
        param = np.clip((rel * vec).sum(axis=1) / safe, 0.0, 1.0)
        param = np.where(seg_len2 > 0.0, param, 0.0)

        proj = start + param[:, None] * vec
        dists = np.hypot(proj[:, 0] - x, proj[:, 1] - y)
        k = int(np.argmin(dists))

        if best is not None and dists[k] >= best.dist:
            continue

        chainage = cum[k] + param[k] * np.sqrt(seg_len2[k])
        cross = vec[k, 0] * rel[k, 1] - vec[k, 1] * rel[k, 0]
        offset = float(dists[k]) * (-1.0 if cross > 0 else 1.0)
        best = Snap(part=part_index, chainage=float(chainage),
                    offset=offset, dist=float(dists[k]))
    return best


def longest_non_decreasing(values):
    """Indeks paa den laengste ikke-aftagende delfoelge (patience sorting).

    Bruges til at finde den stoerste indbyrdes konsistente gruppe af profiler:
    stationeringen i MIKE og stationeringen paa centerlinjen skal stige samtidig.
    Gør de ikke det, er profilet snappet forkert (typisk til en naboslynge).
    """
    if len(values) == 0:
        return []
    tails = []
    tails_index = []
    prev = [-1] * len(values)
    for i, value in enumerate(values):
        pos = bisect_right(tails, value)
        if pos == len(tails):
            tails.append(value)
            tails_index.append(i)
        else:
            tails[pos] = value
            tails_index[pos] = i
        prev[i] = tails_index[pos - 1] if pos > 0 else -1
    out = []
    k = tails_index[-1]
    while k != -1:
        out.append(k)
        k = prev[k]
    return out[::-1]


def consistent_order(stations, chainages):
    """Vaelg de profiler hvis stationering og placering paa linjen passer sammen.

    :return: ``(beholdte indeks (sorteret efter station), modsat_retning)``
             hvor ``modsat_retning`` er True hvis centerlinjen er digitaliseret
             modsat vandloebets stationering.
    """
    order = np.argsort(stations, kind="stable")
    ordered = np.asarray(chainages, dtype=float)[order]
    keep_up = longest_non_decreasing(ordered)
    keep_down = longest_non_decreasing(-ordered)
    if len(keep_down) > len(keep_up):
        return [int(order[i]) for i in keep_down], True
    return [int(order[i]) for i in keep_up], False


# ---------------------------------------------------------------------------
# Korridoren
# ---------------------------------------------------------------------------

def positions_at(points, cum, stations):
    """Punkter paa polylinjen ved de givne stationeringer."""
    stations = np.clip(stations, cum[0], cum[-1])
    idx = np.clip(np.searchsorted(cum, stations, side="right") - 1,
                  0, len(cum) - 2)
    seg = cum[idx + 1] - cum[idx]
    t = np.where(seg > 0, (stations - cum[idx]) / np.where(seg > 0, seg, 1.0),
                 0.0)
    return points[idx] + t[:, None] * (points[idx + 1] - points[idx])


def normals_at(points, cum, stations, window):
    """Enhedsnormaler (positiv = hoejre) ved de givne stationeringer.

    Retningen bestemmes over et vindue paa ``window`` meter i stedet for over
    to nabo-knaekpunkter. Paa en taetdigitaliseret centerlinje ligger
    knaekpunkterne faa decimeter fra hinanden, og en kort maalebasis goer
    tvaersnittene til ren digitaliseringsstoej -- de vifter og krydser
    hinanden, hvilket giver takker i resultatet.
    """
    half = max(window, 1e-6) / 2.0
    before = positions_at(points, cum, stations - half)
    after = positions_at(points, cum, stations + half)
    delta = after - before
    length = np.hypot(delta[:, 0], delta[:, 1])
    # Faldback for stationer hvor vinduet kollapser (fx en linje kortere end
    # vinduet): brug hele linjens retning.
    bad = length < 1e-9
    if np.any(bad):
        whole = points[-1] - points[0]
        norm = np.hypot(whole[0], whole[1]) or 1.0
        delta[bad] = whole / norm
        length[bad] = 1.0
    unit = delta / length[:, None]
    # Rotér 90 grader med uret: (dx, dy) -> (dy, -dx) = hoejre side.
    return np.column_stack([unit[:, 1], -unit[:, 0]])


def offset_grid(profiles, spacing, max_width=0.0):
    """Faelles offset-akse for alle profiler.

    Alle profiler evalueres paa den samme akse, saa punkt nr. i altid betyder
    samme afstand fra bunden. Aksen daekker det bredeste profil; smallere
    profiler giver NaN i enderne og braendes ikke der.
    """
    low = min(float(p.offsets[0]) for p in profiles)
    high = max(float(p.offsets[-1]) for p in profiles)
    if max_width and max_width > 0:
        low = max(low, -max_width / 2.0)
        high = min(high, max_width / 2.0)
    if high - low <= 0:
        raise ValueError("Profilerne har ingen bredde at braende ned.")
    count = int(np.ceil((high - low) / max(spacing, 1e-6))) + 1
    return np.linspace(low, high, count)


def profile_matrix(profiles, offsets):
    """Matrix (profil x offset) med koter, NaN uden for hvert profil."""
    return np.vstack([p.interp(offsets) for p in profiles])


def interpolate_along(chainages, matrix, stations):
    """Interpolér koterne mellem profilerne langs vandloebet.

    Blandingen er NaN-bevidst: naar kun det ene naboprofil naaer ud til et
    givet offset, bruges dets kote alene i stedet for at goere hele punktet
    ugyldigt. Naar ingen af dem naaer derud, bliver resultatet NaN og punktet
    braendes ikke.
    """
    chainages = np.asarray(chainages, dtype=float)
    idx = np.clip(np.searchsorted(chainages, stations, side="right") - 1,
                  0, len(chainages) - 2)
    span = chainages[idx + 1] - chainages[idx]
    t = np.where(span > 0,
                 (stations - chainages[idx]) / np.where(span > 0, span, 1.0),
                 0.0)
    t = np.clip(t, 0.0, 1.0)[:, None]

    lower = matrix[idx]
    upper = matrix[idx + 1]
    w_low = (1.0 - t) * ~np.isnan(lower)
    w_high = t * ~np.isnan(upper)
    total = w_low + w_high

    result = np.full(lower.shape, np.nan)
    good = total > 0
    stacked = np.where(np.isnan(lower), 0.0, lower) * w_low \
        + np.where(np.isnan(upper), 0.0, upper) * w_high
    result[good] = stacked[good] / total[good]
    return result
