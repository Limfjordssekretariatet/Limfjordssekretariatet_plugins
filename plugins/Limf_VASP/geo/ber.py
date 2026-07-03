"""Dekodning af VASP's .ber-filer (vandspejlsberegninger).

VASP gemmer beregnede vandspejl i binære .ber-filer under
PRJDATA\\PRJ<projektid>\\BER\\, IKKE i Access-databasen. Databasen holder kun
en header (VSPBERHEADER for simpel, VSPBERMULHEADER for multi).

Format (reverse-engineeret og verificeret mod VASP's egne shapefile-eksporter):
  Hvert beregningspunkt er en record på 23 float64 (184 bytes, little-endian).
  Records ligger i sammenhængende blokke.

  Simpel (BER<id>.ber): én blok. Record-felter:
      [0]=Station [1]=X [2]=Y [4]=VSP [5]=VNF [6]=Manning
      [10]=Energi [11]=Bund [12]=Bredde [13]=Areal [14]=Radius
  Multi (MULBER<dsid>.ber): N blokke = N scenarier. Record-felter (forskudt):
      [0]=X [1]=Y [3]=VSP [9]=Energi [10]=Bund [11]=Bredde

En blok genkendes ved at flere fortløbende records har gyldige UTM32-
koordinater i X/Y-felterne (Danmark). Antal records udledes af blokkens
udstrækning. Fyldværdier (~1.7e308) i ubrugte felter ignoreres.
"""

import struct
import re

RECORD_DOUBLES = 23
RECORD_BYTES = RECORD_DOUBLES * 8

# Scenarienavne (multi) står som ASCII-strenge lige før hver record-blok.
# Et navn består af bogstaver/tal/mellemrum/_/-, starter med bogstav, og er
# overvejende bogstaver (så vi ikke fanger binært skrald).
_STR_RE = re.compile(rb"[\x20-\x7e\xe6\xf8\xe5\xc6\xd8\xc5]{5,}")
_NAME_RE = re.compile(r"[A-Za-zÆØÅæøå0-9 _\-]+$")


def _is_name(s):
    s = s.strip()
    if len(s) < 5 or not s[0].isalpha() or not _NAME_RE.match(s):
        return False
    return sum(c.isalpha() for c in s) >= len(s) * 0.6


def _find_names(buf):
    """Returnér liste af (offset, navn) for tekststrenge der ligner navne."""
    out = []
    for m in _STR_RE.finditer(buf):
        s = m.group().decode("latin1").strip()
        if _is_name(s):
            out.append((m.start(), s))
    return out

# Grænser for at genkende gyldige UTM32-koordinater (Danmark).
_X_MIN, _X_MAX = 400000.0, 900000.0
_Y_MIN, _Y_MAX = 6000000.0, 6500000.0
# VASP's "ingen værdi"-fyld er ~1.7e308.
_FILL = 1e308


def _d(buf, off):
    return struct.unpack_from("<d", buf, off)[0]


def _valid_xy(x, y):
    return _X_MIN < x < _X_MAX and _Y_MIN < y < _Y_MAX


def _clean(v):
    """Returnér None for fyldværdier, ellers tallet."""
    if v is None or abs(v) >= _FILL:
        return None
    return v


def _find_blocks(buf, x_index):
    """Find record-blokke hvor felt[x_index], felt[x_index+1] er X/Y-koordinater.

    Returnerer liste af (start_offset, antal_records).
    """
    n = len(buf)
    blocks = []
    o = 0
    # Records kan starte på vilkårlig byte-grænse (simpel: %4==4, multi: %4==1),
    # så vi scanner byte for byte. Filerne er små (< nogle MB).
    step = 1
    while o <= n - RECORD_BYTES:
        xo = o + x_index * 8
        if xo + 16 <= n and _valid_xy(_d(buf, xo), _d(buf, xo + 8)):
            # Tæl fortløbende records med gyldige X/Y. Hele recorden (23
            # doubles) skal kunne læses inden for bufferen.
            cnt = 0
            while True:
                rec_start = o + cnt * RECORD_BYTES
                if rec_start + RECORD_BYTES > n:
                    break
                xo2 = rec_start + x_index * 8
                if _valid_xy(_d(buf, xo2), _d(buf, xo2 + 8)):
                    cnt += 1
                else:
                    break
            if cnt >= 5:
                blocks.append((o, cnt))
                o += cnt * RECORD_BYTES
                continue
        o += step
    return blocks


def decode_simpel(path):
    """Dekod en simpel vandspejlsberegning (BER<id>.ber).

    Returnerer liste af dicts: station, x, y, vsp, vnf, manning, energi,
    bund, bredde, areal, radius.
    """
    with open(path, "rb") as f:
        buf = f.read()
    # Simpel-record starter med Station i [0], X i [1], Y i [2].
    blocks = _find_blocks(buf, x_index=1)
    if not blocks:
        return []
    start, count = max(blocks, key=lambda b: b[1])  # største blok = data
    points = []
    for i in range(count):
        o = start + i * RECORD_BYTES
        r = struct.unpack_from("<" + "d" * RECORD_DOUBLES, buf, o)
        points.append({
            "station": _clean(r[0]),
            "x": r[1], "y": r[2],
            "vsp": _clean(r[4]),
            "vnf": _clean(r[5]),
            "manning": _clean(r[6]),
            "energi": _clean(r[10]),
            "bund": _clean(r[11]),
            "bredde": _clean(r[12]),
            "areal": _clean(r[13]),
            "radius": _clean(r[14]),
        })
    return points


def decode_multi(path):
    """Dekod en multivandspejlsberegning (MULBER<dsid>.ber).

    Hver blok er ét scenarie. Returnerer en liste af scenarier, hver:
        {"navn": <scenarienavn>, "points": [ {x, y, vsp, energi, bund,
         bredde}, ... ]}
    i filens rækkefølge. Scenarienavnet læses fra tekststrengen lige før
    blokken (fx "MedMin", "Sommer Middel", "Års middel", "MedMax").
    """
    with open(path, "rb") as f:
        buf = f.read()
    # Multi-record starter med X i [0], Y i [1].
    blocks = _find_blocks(buf, x_index=0)
    names = _find_names(buf)
    scenarier = []
    for idx, (start, count) in enumerate(blocks):
        # Scenarienavn = nærmeste navn-streng før blokkens start.
        before = [s for off, s in names if off < start]
        navn = before[-1] if before else "Scenarie %d" % (idx + 1)
        pts = []
        for i in range(count):
            o = start + i * RECORD_BYTES
            r = struct.unpack_from("<" + "d" * RECORD_DOUBLES, buf, o)
            pts.append({
                "x": r[0], "y": r[1],
                "vsp": _clean(r[3]),
                "energi": _clean(r[9]),
                "bund": _clean(r[10]),
                "bredde": _clean(r[11]),
            })
        scenarier.append({"navn": navn, "points": pts})
    return scenarier
