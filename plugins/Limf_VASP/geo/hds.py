"""Læsning og skrivning af VASP's .hds-filer (hydrauliske datasæt).

VASP gemmer de hydrauliske parametre — oplande, afstrømninger, manningtal
m.fl. — i binære .hds-filer under PRJDATA\\PRJ<projektid>\\BER\\HYD<id>.hds,
ikke i Access-databasen. Databasen holder kun en header (HYDATHEADER).

Format (reverse-engineeret og verificeret mod alle 904 .hds-filer i
vaspdatabasen: hver eneste fil går præcis op, uden en byte til rest):

  Filhoved:
      0    int32   magi = 3902749
      4    int32   version = 1
      264  int32   fast mærke = 22712
      272  int32   projektid      (= PRJ-mappen)
      276  int32   hydatid        (= filnavnet)
      280  kort tekst, fast 11 bytes   datasættets nummer
      291  kort tekst, fast 256 bytes  datasættets navn
      ...  felter af vekslende længde (bemærkning m.m.)
      int32   N = antal serier

  N poster, én pr. serie (287 bytes + bemærkningens længde):
      +0    int32   type (1=Oplande, 2=Afstrømninger, 3=Manningtal,
                     4=Fast VSP, 5=Pkt. Q, 6=Obs Q, 7=Obs vst)
      +4    int32   serienummer, som VASP viser det i fanen
      +8    kort tekst, fast 256 bytes   seriens navn
      +264  lang tekst (int32 længde + tegn)  bemærkning
      ...   kort tekst, fast 11 bytes    initialer
      ...   float64 TDateTime            sidst rettet

  N datablokke i samme rækkefølge som posterne:
      int32   antal rækker
      rækker á 80 bytes: float64 station, float64 værdi,
                         kort tekst, fast 64 bytes (bemærkning)

"Kort tekst" er Delphis ShortString: ét længdebyte og derefter tegnene i et
felt af fast størrelse. Resten af feltet er skrald fra VASP's hukommelse og
betyder ingenting — vi skriver nuller.

Hovedet har felter af vekslende længde, som ikke er kortlagt. Derfor findes
antallet af serier ved at prøve hvert offset og kun godtage det, hvor hele
filen går præcis op. Det er samtidig kontrollen af, at filen er forstået:
kan den ikke læses helt, skriver vi ikke i den.
"""

import datetime as dt
import os
import shutil
import struct

MAGI = 3902749
H_MAERKE = 264
H_MAERKE_VAERDI = 22712
H_PROJEKTID = 272
H_HYDATID = 276
H_NUMMER = 280
H_NAVN = 291

O_NAVN = 8
O_BEM = 264
POST_FAST = 287          # postens længde når bemærkningen er tom
NAVN_FELT = 256
INIT_FELT = 11

RAEKKE = 80
R_BEM = 16
R_BEM_FELT = 64

OPLANDE = 1
AFSTROEMNINGER = 2
MANNINGTAL = 3
FAST_VSP = 4
PKT_Q = 5
OBS_Q = 6
OBS_VST = 7

TYPER = {
    OPLANDE: "Oplande",
    AFSTROEMNINGER: "Afstrømninger",
    MANNINGTAL: "Manningtal",
    FAST_VSP: "Fast VSP",
    PKT_Q: "Pkt. Q",
    OBS_Q: "Obs Q",
    OBS_VST: "Obs vst",
}

# Tegnsæt: VASP er en Delphi-ansi-applikation, så teksten er cp1252.
TEGNSAET = "cp1252"

# TDateTime tæller dage fra 30-12-1899.
_NULDAG = dt.datetime(1899, 12, 30)


class HdsFejl(Exception):
    """Rejses når en .hds-fil ikke kan læses eller skrives, med dansk besked."""


# --- tekst og tid --------------------------------------------------------

def _kort_tekst(b, off):
    """Læs en ShortString: ét længdebyte og derefter tegnene."""
    return b[off + 1:off + 1 + b[off]].decode(TEGNSAET, "replace")


def _lang_tekst(b, off):
    """Læs en tekst med int32-længde foran. None hvis længden er urimelig."""
    (n,) = struct.unpack_from("<i", b, off)
    if not 0 <= n <= 1 << 20 or off + 4 + n > len(b):
        return None
    return b[off + 4:off + 4 + n].decode(TEGNSAET, "replace")


def _pak_kort(tekst, felt):
    """Pak en tekst som ShortString i et felt af fast størrelse."""
    raa = (tekst or "").encode(TEGNSAET, "replace")[:felt - 1]
    return bytes([len(raa)]) + raa + b"\x00" * (felt - 1 - len(raa))


def _pak_lang(tekst):
    """Pak en tekst med int32-længde foran."""
    raa = (tekst or "").encode(TEGNSAET, "replace")
    return struct.pack("<i", len(raa)) + raa


def _til_tid(tal):
    """TDateTime -> datetime. None hvis tallet ikke er en rimelig dato."""
    if not 1 < tal < 100000:
        return None
    return _NULDAG + dt.timedelta(days=tal)


def _fra_tid(tid):
    """datetime -> TDateTime."""
    return (tid - _NULDAG).total_seconds() / 86400.0


# --- læsning -------------------------------------------------------------

def _laes_hoved(b):
    """Læs de faste oplysninger i filhovedet, eller None."""
    if len(b) < H_NAVN + NAVN_FELT:
        return None
    (maerke,) = struct.unpack_from("<i", b, H_MAERKE)
    if maerke != H_MAERKE_VAERDI:
        return None
    projektid, hydatid = struct.unpack_from("<ii", b, H_PROJEKTID)
    return {
        "projektid": projektid,
        "hydatid": hydatid,
        "nummer": _kort_tekst(b, H_NUMMER),
        "navn": _kort_tekst(b, H_NAVN),
    }


def _laes_post(b, o):
    """Læs én serie-post ved o. Returnerer (serie, postens længde) eller None."""
    if o + POST_FAST > len(b):
        return None
    type_, nr = struct.unpack_from("<ii", b, o)
    if type_ not in TYPER or not 1 <= nr <= 2000:
        return None
    bem = _lang_tekst(b, o + O_BEM)
    if bem is None:
        return None
    o_init = o + O_BEM + 4 + len(bem)
    o_dato = o_init + INIT_FELT
    if o_dato + 8 > len(b):
        return None
    (tal,) = struct.unpack_from("<d", b, o_dato)
    tid = _til_tid(tal)
    if tid is None or not 1980 < tid.year < 2070:
        return None
    return ({
        "type": type_,
        "nr": nr,
        "navn": _kort_tekst(b, o + O_NAVN),
        "bemaerkning": bem,
        "initialer": _kort_tekst(b, o_init),
        "dato": tid,
    }, POST_FAST + len(bem))


def _gennemgang(b, off):
    """Prøv at læse filen med antallet af serier stående ved off.

    Returnerer (serier, datastart) hvis — og kun hvis — hele filen går op.
    """
    if off + 4 > len(b):
        return None
    (n,) = struct.unpack_from("<i", b, off)
    if not 0 <= n <= 2000:
        return None
    serier = []
    o = off + 4
    for _ in range(n):
        svar = _laes_post(b, o)
        if svar is None:
            return None
        serie, laengde = svar
        serier.append(serie)
        o += laengde
    datastart = o
    for serie in serier:
        if o + 4 > len(b):
            return None
        (antal,) = struct.unpack_from("<i", b, o)
        if not 0 <= antal <= 20000 or o + 4 + antal * RAEKKE > len(b):
            return None
        serie["raekker"] = [
            (struct.unpack_from("<d", b, o + 4 + i * RAEKKE)[0],
             struct.unpack_from("<d", b, o + 4 + i * RAEKKE + 8)[0],
             _kort_tekst(b, o + 4 + i * RAEKKE + R_BEM))
            for i in range(antal)]
        o += 4 + antal * RAEKKE
    if o != len(b):
        return None
    return serier, datastart


def laes(sti):
    """Læs en .hds-fil.

    Returnerer en dict med projektid, hydatid, navn, nummer, serier og de
    offsets, skrivningen skal bruge (antal_offset, datastart). Hver serie
    er en dict med type, nr, navn, bemaerkning, initialer, dato og rækker
    (liste af (station, værdi, bemærkning)).
    """
    try:
        with open(sti, "rb") as f:
            b = f.read()
    except OSError as e:
        raise HdsFejl("Kunne ikke læse %s: %s" % (sti, e))
    if len(b) < 8 or struct.unpack_from("<i", b, 0)[0] != MAGI:
        raise HdsFejl("%s ligner ikke en VASP-datasætfil (.hds)." % sti)
    hoved = _laes_hoved(b) or {}
    for off in range(0, min(len(b), 8000) - 3):
        svar = _gennemgang(b, off)
        if svar is None:
            continue
        serier, datastart = svar
        hoved.update({"sti": sti, "serier": serier, "antal_offset": off,
                      "datastart": datastart, "bytes": len(b)})
        return hoved
    raise HdsFejl(
        "Strukturen i %s kunne ikke læses. Filen bliver ikke rørt." % sti)


def serier_af_type(data, type_):
    """Serierne af én type, i den rækkefølge VASP nummererer dem."""
    return sorted((s for s in data["serier"] if s["type"] == type_),
                  key=lambda s: s["nr"])


# --- skrivning -----------------------------------------------------------

def _pak_post(type_, nr, navn, bemaerkning, initialer, tid):
    """Byg bytes for én serie-post."""
    return (struct.pack("<ii", type_, nr)
            + _pak_kort(navn, NAVN_FELT)
            + _pak_lang(bemaerkning)
            + _pak_kort(initialer, INIT_FELT)
            + struct.pack("<d", _fra_tid(tid)))


def _pak_blok(raekker):
    """Byg bytes for én datablok: antal rækker og rækkerne selv."""
    ud = [struct.pack("<i", len(raekker))]
    for raekke in raekker:
        station, vaerdi = raekke[0], raekke[1]
        bem = raekke[2] if len(raekke) > 2 else ""
        ud.append(struct.pack("<dd", float(station), float(vaerdi))
                  + _pak_kort(bem, R_BEM_FELT))
    return b"".join(ud)


def naeste_nr(data, type_):
    """Næste ledige serienummer inden for en type."""
    brugte = [s["nr"] for s in data["serier"] if s["type"] == type_]
    return (max(brugte) + 1) if brugte else 1


def tag_backup(sti, backup_mappe):
    """Læg en kopi af filen i backup-mappen. Returnér kopiens sti."""
    os.makedirs(backup_mappe, exist_ok=True)
    stempel = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    navn, _ = os.path.splitext(os.path.basename(sti))
    kopi = os.path.join(backup_mappe, "%s-%s.hds" % (navn, stempel))
    shutil.copy2(sti, kopi)
    return kopi


def tilfoej_serie(sti, type_, navn, raekker, initialer="", bemaerkning="",
                  backup_mappe=None, tid=None):
    """Skriv en ny serie ind i en eksisterende .hds-fil.

    Serien lægges bagest — både posten og datablokken — så rækkefølgen
    mellem poster og blokke holder. Eksisterende serier røres ikke.

    Filen læses og kontrolleres først; kan den ikke læses helt igennem,
    sker der ingenting. Der tages backup, og selve skrivningen sker til en
    midlertidig fil, der først derefter sættes i stedet for originalen — så
    en afbrudt skrivning ikke kan efterlade en halv fil.

    Returnerer en dict med nr (seriens nye nummer), backup og antal rækker.
    """
    if type_ not in TYPER:
        raise HdsFejl("Ukendt serietype: %r" % (type_,))
    if not navn:
        raise HdsFejl("Serien skal have et navn.")
    if not raekker:
        raise HdsFejl("Serien er tom — der er ingen rækker at skrive.")

    data = laes(sti)
    if any(s["type"] == type_ and s["navn"].strip().lower()
           == navn.strip().lower() for s in data["serier"]):
        raise HdsFejl(
            "Der er allerede en serie under %s, der hedder \"%s\". Vælg et "
            "andet navn — eksisterende serier bliver ikke overskrevet."
            % (TYPER[type_], navn))

    nr = naeste_nr(data, type_)
    post = _pak_post(type_, nr, navn, bemaerkning, initialer,
                     tid or dt.datetime.now())
    blok = _pak_blok(raekker)

    with open(sti, "rb") as f:
        b = f.read()
    if len(b) != data["bytes"]:
        raise HdsFejl("Filen blev ændret undervejs. Prøv igen.")

    off, datastart = data["antal_offset"], data["datastart"]
    ny = (b[:off]
          + struct.pack("<i", len(data["serier"]) + 1)
          + b[off + 4:datastart]
          + post
          + b[datastart:]
          + blok)

    backup = tag_backup(sti, backup_mappe) if backup_mappe else None

    midlertidig = sti + ".ny"
    try:
        with open(midlertidig, "wb") as f:
            f.write(ny)
        # Kontrollér den nye fil, før den sættes i stedet for originalen.
        kontrol = laes(midlertidig)
        if len(kontrol["serier"]) != len(data["serier"]) + 1:
            raise HdsFejl("Den nye fil kunne ikke læses igen som forventet.")
        os.replace(midlertidig, sti)
    except HdsFejl:
        _ryd_op(midlertidig)
        raise
    except OSError as e:
        _ryd_op(midlertidig)
        raise HdsFejl("Kunne ikke skrive %s: %s" % (sti, e))

    return {"nr": nr, "backup": backup, "raekker": len(raekker),
            "type": TYPER[type_], "navn": navn}


def _ryd_op(sti):
    """Fjern en midlertidig fil, hvis den ligger der."""
    try:
        if os.path.exists(sti):
            os.remove(sti)
    except OSError:
        pass


# --- datasæt i VASP-databasen -------------------------------------------

def hds_sti(prjdata, projektid, hydatid):
    """Byg stien til en .hds-fil: PRJDATA\\PRJ<projektid>\\BER\\HYD<id>.hds."""
    return os.path.join(prjdata, "PRJ%d" % projektid, "BER",
                        "HYD%d.hds" % hydatid)


def find_datasaet(prjdata, projektid=None):
    """Find de hydrauliske datasæt under PRJDATA.

    Navn, projektid og hydatid står i filerne selv, så listen kan bygges
    uden at spørge Access. Er projektid angivet, søges kun i det projekt.
    Hver post: dict med projektid, hydatid, navn, nummer, sti og antal
    serier pr. type. Filer der ikke kan læses, springes over.
    """
    if projektid is not None:
        mapper = [os.path.join(prjdata, "PRJ%d" % projektid, "BER")]
    else:
        try:
            mapper = [os.path.join(prjdata, navn, "BER")
                      for navn in os.listdir(prjdata)
                      if navn.upper().startswith("PRJ")]
        except OSError as e:
            raise HdsFejl("Kunne ikke læse %s: %s" % (prjdata, e))
    ud = []
    for mappe in mapper:
        try:
            navne = os.listdir(mappe)
        except OSError:
            continue
        for navn in navne:
            if not navn.lower().endswith(".hds"):
                continue
            sti = os.path.join(mappe, navn)
            try:
                data = laes(sti)
            except HdsFejl:
                continue
            antal = {}
            for serie in data["serier"]:
                antal[serie["type"]] = antal.get(serie["type"], 0) + 1
            ud.append({
                "projektid": data.get("projektid"),
                "hydatid": data.get("hydatid"),
                "navn": data.get("navn") or "(uden navn)",
                "nummer": data.get("nummer") or "",
                "sti": sti,
                "antal": antal,
                "serier": len(data["serier"]),
            })
    ud.sort(key=lambda d: (d["projektid"] or 0, d["hydatid"] or 0))
    return ud
