#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pakker et præberegnet oplandsgrundlag til udgivelse på GitHub Releases.

Hver flise bliver til én zip. Det er ikke pynt: 262 oplande à syv filer ville være
1.834 løse assets i ét release, og både upload, oversigt og fejlretning bliver
uoverskuelig. Én zip pr. flise holder sig langt under grænsen på 2 GB pr. asset —
den største flise indtil nu er 140 MB.

Ved siden af zipperne skrives `index.json`: den fortegnelse pluginnet henter først
for at finde ud af HVILKEN flise der dækker et projektområde, og om den er beregnet
med de samme parametre som brugeren har. Uden den måtte pluginnet hente fliser på
må og få for at finde ud af om de passer.

    python Udgiv_Grundlag.py --bibliotek C:/temp/Oplandsgrundlag \\
        --udgivelse C:/temp/Udgivelse --repo limfjord/oplandsgrundlag --tag grundlag-v3

Uden --repo pakkes der bare, og upload-kommandoerne skrives ud til sidst.
Kræver GitHub CLI ('gh') for at uploade; pakningen virker uden.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
INDEKS = "index.json"

# Hvor meget dækningspolygonen forenkles inden den kommer i fortegnelsen.
# Den bruges KUN til at finde den rigtige flise; den nøjagtige kontrol sker på den
# hentede flises egen daekning.gpkg. 100 m holder fortegnelsen på nogle få hundrede
# kilobyte selv med et par hundrede fliser.
FORENKLING_M = 100.0


def _indlaes(navn: str, filnavn: str):
    if navn in sys.modules:
        return sys.modules[navn]
    spec = importlib.util.spec_from_file_location(navn, SCRIPTS / filnavn)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[navn] = modul
    spec.loader.exec_module(modul)
    return modul


def sha256(sti: Path, blok=1 << 20):
    """Checksum, så en halvt hentet fil ikke bliver taget for et grundlag."""
    h = hashlib.sha256()
    with open(sti, "rb") as f:
        for stykke in iter(lambda: f.read(blok), b""):
            h.update(stykke)
    return h.hexdigest()


def daekning_wkt(flise: Path, om):
    """Flisens dækningsområde, forenklet, som WKT — eller None hvis det mangler."""
    from osgeo import ogr

    fil = flise / om.DAEKNING
    if not fil.is_file():
        return None
    ds = ogr.Open(str(fil))
    if ds is None:
        return None
    lag = ds.GetLayer(0)
    samlet = None
    for f in lag:
        g = f.GetGeometryRef()
        if g is None:
            continue
        g = g.Clone()
        samlet = g if samlet is None else samlet.Union(g)
    ds = None
    if samlet is None:
        return None
    forenklet = samlet.Simplify(FORENKLING_M)
    # En forenkling kan i sjældne tilfælde ødelægge geometrien; så bruges originalen.
    if forenklet is None or forenklet.IsEmpty() or not forenklet.IsValid():
        forenklet = samlet
    return forenklet.ExportToWkt()


def pak_flise(flise: Path, ud: Path, om):
    """Pakker én flise til zip. Filerne ligger uden mappeniveau i zippen.

    Er zippen nyere end alt i flisen, genbruges den. Biblioteket er paa 14 GB, og
    at pakke det hele om for at tilfoeje én flise koster tre kvarter uden at
    aendre noget.
    """
    zipfil = ud / (flise.name + ".zip")
    if zipfil.exists():
        nyeste = max((f.stat().st_mtime for f in flise.iterdir() if f.is_file()),
                     default=0)
        if zipfil.stat().st_mtime >= nyeste:
            return zipfil
        zipfil.unlink()
    with zipfile.ZipFile(zipfil, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for fil in sorted(flise.iterdir()):
            if fil.is_file():
                z.write(fil, fil.name)
    return zipfil


def byg_udgivelse(bibliotek: Path, udgivelse: Path, om, log=print):
    """Pakker hele biblioteket og skriver fortegnelsen."""
    udgivelse.mkdir(parents=True, exist_ok=True)
    fliser = []
    sprunget = []

    mapper = sorted(m for m in bibliotek.iterdir() if m.is_dir()
                    and not m.name.endswith(".ufaerdig")
                    and ".gammel_" not in m.name)
    log(f"{len(mapper)} flise(r) i {bibliotek}")

    for mappe in mapper:
        manifest = om.laes_manifest(mappe)
        if not manifest:
            sprunget.append((mappe.name, "intet manifest"))
            continue
        if manifest.get("grundlag_version") != om.GRUNDLAG_VERSION:
            sprunget.append((mappe.name,
                             f"grundlag_version {manifest.get('grundlag_version')} "
                             f"(forventet {om.GRUNDLAG_VERSION})"))
            continue
        mangler = [r for r in om.GRUNDLAG_RASTERE if not (mappe / r).is_file()]
        if mangler:
            sprunget.append((mappe.name, "mangler " + ", ".join(mangler)))
            continue

        zipfil = pak_flise(mappe, udgivelse, om)
        post = {
            "flise_id": manifest.get("flise_id", mappe.name),
            "navn": manifest.get("navn") or None,
            "areal_km2": manifest.get("areal_km2"),
            "udstraekning": manifest.get("udstraekning"),
            "epsg": manifest.get("epsg"),
            "oploesning_m": manifest.get("oploesning_m"),
            "konditioneringsnoegle": manifest.get("konditioneringsnoegle"),
            "traefprocent": manifest.get("traefprocent"),
            "daekning_af_kortlagte_pct": manifest.get("daekning_af_kortlagte_pct"),
            "fil": zipfil.name,
            "bytes": zipfil.stat().st_size,
            "sha256": sha256(zipfil),
            "daekning_wkt": daekning_wkt(mappe, om),
        }
        fliser.append(post)
        log(f"   {mappe.name:24s} {post['bytes']/1e6:7.1f} MB  "
            f"noegle {str(post['konditioneringsnoegle'])[:8]}")

    if not fliser:
        raise SystemExit("Ingen fliser at udgive.")

    noegler = {f["konditioneringsnoegle"] for f in fliser}
    if len(noegler) > 1:
        log(f"   ADVARSEL: fliserne er beregnet med {len(noegler)} forskellige "
            "parametersaet. Pluginnet bruger kun dem der matcher brugerens "
            "indstillinger — resten bliver aldrig hentet.")

    indeks = {
        "grundlag_version": om.GRUNDLAG_VERSION,
        "udgivet": dt.datetime.now().isoformat(timespec="seconds"),
        "antal_fliser": len(fliser),
        "bytes_i_alt": sum(f["bytes"] for f in fliser),
        "forenkling_m": FORENKLING_M,
        "fliser": sorted(fliser, key=lambda f: f["flise_id"]),
    }
    (udgivelse / INDEKS).write_text(
        json.dumps(indeks, indent=1, ensure_ascii=False), encoding="utf-8")

    log("")
    log(f"{len(fliser)} flise(r), {indeks['bytes_i_alt']/1e9:.2f} GB, "
        f"fortegnelse: {udgivelse / INDEKS} "
        f"({(udgivelse / INDEKS).stat().st_size/1024:.0f} kB)")
    for navn, hvorfor in sprunget:
        log(f"   sprunget over: {navn} — {hvorfor}")
    return indeks


def find_gh():
    """Stien til GitHub CLI.

    shutil.which alene raekker ikke: python-qgis-ltr.bat saetter sit eget PATH op,
    og gh ligger typisk i brugerens egen mappe, fordi MSI-installationen kraever
    administrator.
    """
    fundet = shutil.which("gh")
    if fundet:
        return fundet
    for rod in (os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)")):
        if not rod:
            continue
        for stump in (("Programs", "gh", "bin", "gh.exe"), ("GitHub CLI", "gh.exe")):
            sti = Path(rod).joinpath(*stump)
            if sti.is_file():
                return str(sti)
    return None


def upload(udgivelse: Path, repo: str, tag: str, titel: str, log=print,
           prerelease: bool = False):
    """Lægger zipperne op som assets på et GitHub-release via gh CLI."""
    gh = find_gh()
    if gh is None:
        log("")
        log("GitHub CLI ('gh') er ikke installeret — uploader ikke.")
        log("Installér den med:  winget install --id GitHub.cli")
        log("Kør derefter:")
        log(f'   gh release create {tag} --repo {repo} --title "{titel}" \\')
        log(f'       --notes "Praeberegnet oplandsgrundlag" {udgivelse}/*.zip '
            f'{udgivelse / INDEKS}')
        return False

    findes = subprocess.run([gh, "release", "view", tag, "--repo", repo],
                            capture_output=True, text=True).returncode == 0
    if not findes:
        log(f"opretter release {tag} i {repo}"
            + (" som prerelease" if prerelease else ""))
        kommando = [gh, "release", "create", tag, "--repo", repo,
                    "--title", titel,
                    "--notes", "Praeberegnet oplandsgrundlag til "
                               "Vaadomraade-pluginnet."]
        if prerelease:
            # Et prerelease bliver ikke 'latest', og pluginnet henter fra
            # releases/latest/download/. Udgivelsen kan altsaa proeves af paa en
            # udtrykkelig adresse, uden at den slaar igennem hos alle med det samme.
            kommando.append("--prerelease")
        subprocess.run(kommando, check=True)

    # Hvad ligger der allerede? Et release paa 12 GB skal ikke sendes af sted
    # igen, fordi der er kommet én flise til.
    oppe = {}
    svar = subprocess.run([gh, "release", "view", tag, "--repo", repo,
                           "--json", "assets"], capture_output=True, text=True)
    if svar.returncode == 0:
        try:
            for a in json.loads(svar.stdout).get("assets", []):
                if a.get("state") == "uploaded":
                    oppe[a["name"]] = a.get("size")
        except (ValueError, KeyError):
            pass

    filer = sorted(udgivelse.glob("*.zip")) + [udgivelse / INDEKS]
    sendt = sprunget_over = 0
    for i, fil in enumerate(filer, 1):
        stoerrelse = fil.stat().st_size
        # Fortegnelsen sendes altid: den aendrer sig hver gang noget andet goer.
        if fil.name != INDEKS and oppe.get(fil.name) == stoerrelse:
            sprunget_over += 1
            continue
        log(f"   [{i}/{len(filer)}] {fil.name} ({stoerrelse/1e6:.0f} MB)")
        subprocess.run([gh, "release", "upload", tag, str(fil),
                        "--repo", repo, "--clobber"], check=True)
        sendt += 1
    log(f"   {sendt} fil(er) sendt, {sprunget_over} laa der allerede")
    log("")
    if prerelease:
        log("Udgivet som prerelease — 'latest' peger stadig paa den forrige.")
        log("Proev den af med indstillingen vaadomraade_modeller/grundlag_url:")
        log(f"   https://github.com/{repo}/releases/download/{tag}/")
        log("Naar den skal gaelde for alle:")
        log(f"   gh release edit {tag} --repo {repo} --prerelease=false --latest")
    else:
        log("Pluginnets base-URL bliver:")
        log(f"   https://github.com/{repo}/releases/latest/download/")
    return True


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Pak et oplandsgrundlag til udgivelse på GitHub Releases")
    p.add_argument("--bibliotek", required=True, help="mappen med de beregnede fliser")
    p.add_argument("--udgivelse", required=True, help="mappe til zipperne og index.json")
    p.add_argument("--repo", help="GitHub-repo som ejer/navn — uden den pakkes der kun")
    p.add_argument("--tag", default=None, help="release-tag (standard: grundlag-v<version>)")
    p.add_argument("--titel", default=None)
    p.add_argument("--prerelease", action="store_true",
                   help="udgiv uden at blive 'latest' — pluginnet henter stadig den forrige")
    a = p.parse_args(argv)

    om = _indlaes("oplandsmodel_udgiv", "oplandsmodel.py")
    tag = a.tag or f"grundlag-v{om.GRUNDLAG_VERSION}"
    titel = a.titel or f"Oplandsgrundlag v{om.GRUNDLAG_VERSION}"

    indeks = byg_udgivelse(Path(a.bibliotek), Path(a.udgivelse), om)
    if a.repo:
        upload(Path(a.udgivelse), a.repo, tag, titel, prerelease=a.prerelease)
    else:
        print("")
        print("Ingen --repo angivet, så der er kun pakket. Upload med:")
        print(f'   gh release create {tag} --repo <ejer>/<navn> --title "{titel}" \\')
        print(f'       {a.udgivelse}/*.zip {Path(a.udgivelse) / INDEKS}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
