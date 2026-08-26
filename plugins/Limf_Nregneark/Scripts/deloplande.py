# -*- coding: utf-8 -*-
"""Deloplandene som ét redigerbart lag — og de to leverancefiler udledt af det.

Modellen deler allerede oplandet op: vandløbsoplandene ét pr. kortlagt vandløb,
det direkte opland efter hvordan vandet kommer ind (koncentreret tilløb uden
kortlagt vandløb, diffus afstrømning over grænsen, projektområdet selv).
Opdelingen forsvandt før i to samlede polygoner.

Her samles alle deloplandene i ÉT lag med en `type`-kolonne. Vil man flytte et
delopland fra direkte til vandløbsopland eller omvendt, retter man værdien i den
kolonne. `Vandoplandet.gpkg` og `Direkte_Opland.gpkg` udledes af laget, så
regnearkskæden ser ét samlet polygon i hver, præcis som den altid har gjort —
den læser arealet fra første objekt og kan ikke tåle flere.

Kolonnen `beregnet_som` står uændret med modellens eget bud, så man altid kan se
hvad der er rettet i hånden.
"""
import os
from pathlib import Path

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsGeometry,
    QgsProcessingException,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

FILNAVN = 'Deloplande.gpkg'
LAGNAVN = 'Deloplande'

VANDLOEB = 'vandløbsopland'
DIREKTE = 'direkte opland'
TYPER = (VANDLOEB, DIREKTE)

# Leverancefilerne der udledes, og hvilken type de svarer til.
LEVERANCER = (('Vandoplandet.gpkg', 'Vandoplandet', VANDLOEB),
              ('Direkte_Opland.gpkg', 'Direkte_Opland', DIREKTE))

FELTER = (('del_id', 'heltal'), ('navn', 'tekst'), ('kilde', 'tekst'),
          ('areal_ha', 'tal'), ('type', 'tekst'), ('beregnet_som', 'tekst'),
          ('koersel_id', 'tekst'), ('dato', 'tekst'))


def _felt(om, navn, slags):
    return om.felt(navn, slags)


def _frigiv(sti: Path):
    """Fjerner lag i QGIS-projektet der peger paa filen. Sandt hvis filen findes.

    Trinnene laegger selv deres output i lagpanelet, saa ved anden koersel er
    filen naesten altid aaben, og paa Windows holder et indlaest lag den laast.
    """
    try:
        from qgis.core import QgsProject

        projekt = QgsProject.instance()
        if projekt is not None:
            maal = os.path.normpath(str(sti)).lower()
            for lag in list(projekt.mapLayers().values()):
                try:
                    kilde = lag.source().split('|', 1)[0]
                except RuntimeError:
                    continue
                if os.path.normpath(kilde).lower() == maal:
                    projekt.removeMapLayer(lag.id())
    except Exception:
        pass

    return os.path.isfile(sti)


def _skriv(sti: Path, lagnavn, vl):
    """Skriver et hukommelseslag til GeoPackage.

    Findes filen, skrives LAGET om inde i den frem for at filen slettes og laves
    forfra. Filerne skrives hver gang regnearkstrinnene koeres, og et lag der er
    indlaest i QGIS holder filen laast — sletning ville fejle netop naar man har
    resultatet fremme og kigger paa det.
    """
    findes = _frigiv(sti)
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = 'GPKG'
    opts.fileEncoding = 'UTF-8'
    opts.layerName = lagnavn
    if findes:
        opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    fejl, besked, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
        vl, str(sti), QgsCoordinateTransformContext(), opts)
    if fejl != QgsVectorFileWriter.NoError:
        raise QgsProcessingException(
            f'Kunne ikke gemme "{lagnavn}" til {sti}: {besked or fejl}. '
            'Ligger laget åbent i QGIS? Fjern det og kør igen.')
    return sti


def _geometrier(gpkg: Path, lagnavn: str):
    """(geometri, attributter) for hvert objekt i et lag i leverancen."""
    lag = QgsVectorLayer(f'{gpkg}|layername={lagnavn}', lagnavn, 'ogr')
    if not lag.isValid():
        return []
    ud = []
    for f in lag.getFeatures():
        g = QgsGeometry(f.geometry())
        if g.isEmpty():
            continue
        ud.append((g, {n: f[n] for n in f.fields().names()}))
    del lag
    return ud


def skriv_deloplande(om, gpkg_leverance: Path, ud_sti: Path, projekt_geom, crs):
    """Samler modellens delarealer i ét lag med en type-kolonne.

    Projektområdet trækkes fra hvert delopland, ligesom det gøres i de to
    leverancefiler — det tælles for sig i regnearket. Deloplande der forsvinder
    helt ved det (delarealet «projektområdet selv»), kommer ikke med.
    """
    raekker = []
    for lagnavn, type_, navnfelt, kildetekst in (
        ('vandloebsoplande_delarealer', VANDLOEB, 'vandloeb_navn',
         'kortlagt vandløb'),
        ('direkte_delarealer', DIREKTE, 'naermeste_vandloeb', None),
    ):
        for geom, attr in _geometrier(gpkg_leverance, lagnavn):
            if projekt_geom is not None and not projekt_geom.isEmpty():
                geom = geom.difference(projekt_geom)
            if geom is None or geom.isEmpty() or geom.area() <= 0:
                continue
            geom.convertToMultiType()
            raekker.append((geom, [
                attr.get('del_id'),
                attr.get(navnfelt) or '',
                kildetekst if kildetekst is not None else (attr.get('kilde') or ''),
                round(geom.area() / 1e4, 4),
                type_, type_,
                # Koersel og dato staar allerede paa delarealet — hentes derfra,
                # saa laget peger paa den koersel det faktisk kommer fra.
                attr.get('koersel_id') or '', attr.get('dato') or '',
            ]))

    vl = QgsVectorLayer(f'MultiPolygon?crs={crs.authid()}', LAGNAVN, 'memory')
    vl.dataProvider().addAttributes([_felt(om, n, s) for n, s in FELTER])
    vl.updateFields()
    objekter = []
    for geom, vaerdier in sorted(raekker, key=lambda r: -r[0].area()):
        f = QgsFeature(vl.fields())
        f.setGeometry(geom)
        f.setAttributes(vaerdier)
        objekter.append(f)
    vl.dataProvider().addFeatures(objekter)
    vl.updateExtents()
    _skriv(ud_sti, LAGNAVN, vl)
    _giv_stil(ud_sti)
    return len(objekter)



def _giv_stil(sti: Path):
    """Farvelægger laget efter `type` og gør kolonnen til en dropdown.

    Begge dele gemmes i selve GeoPackagen, så de følger med når laget åbnes.
    Farverne gør det synligt med det samme om en rettelse ramte det rigtige
    delopland; dropdownen gør at man ikke kan skrive noget der ikke findes —
    en stavefejl ville ellers tavst tage deloplandet ud af begge summer.
    """
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsEditorWidgetSetup,
                           QgsFillSymbol, QgsRendererCategory)

    lag = QgsVectorLayer(f'{sti}|layername={LAGNAVN}', LAGNAVN, 'ogr')
    if not lag.isValid():
        return False

    farver = {VANDLOEB: '31,120,180,140', DIREKTE: '253,180,98,140'}
    kanter = {VANDLOEB: '23,90,140', DIREKTE: '200,120,40'}
    kategorier = []
    for vaerdi in TYPER:
        symbol = QgsFillSymbol.createSimple({
            'color': farver[vaerdi],
            'outline_color': kanter[vaerdi],
            'outline_width': '0.5',
        })
        kategorier.append(QgsRendererCategory(vaerdi, symbol, vaerdi))
    lag.setRenderer(QgsCategorizedSymbolRenderer('type', kategorier))

    i = lag.fields().indexOf('type')
    if i >= 0:
        lag.setEditorWidgetSetup(i, QgsEditorWidgetSetup(
            'ValueMap', {'map': [{v: v} for v in TYPER]}))
    # Modellens eget bud og sporingsfelterne skal ikke kunne rettes — de er
    # dokumentation for hvad beregningen naaede frem til.
    form = lag.editFormConfig()
    for navn in ('del_id', 'navn', 'kilde', 'areal_ha', 'beregnet_som',
                 'koersel_id', 'dato'):
        k = lag.fields().indexOf(navn)
        if k >= 0:
            form.setReadOnly(k, True)
    lag.setEditFormConfig(form)

    # saveStyleToDatabaseV2 fra QGIS 3.42; den gamle er forældet, men er den
    # eneste der findes i ældre udgaver. Prøv den nye først.
    beskrivelse = 'Deloplande — farvet efter type'
    try:
        lag.saveStyleToDatabaseV2(LAGNAVN, beskrivelse, True, '')
    except (AttributeError, TypeError):
        lag.saveStyleToDatabase(LAGNAVN, beskrivelse, True, '')
    del lag
    return True

def udled_leverance(ud_mappe, crs=None, log=None):
    """Skriver Vandoplandet.gpkg og Direkte_Opland.gpkg ud fra deloplandene.

    Kaldes lige før regnearkstrinnene, så en rettelse i `type` slår igennem uden
    at man skal huske et ekstra skridt. Findes deloplandslaget ikke — en ældre
    kørsel — røres de to filer ikke, og der returneres None.
    """
    ud_mappe = Path(ud_mappe)
    sti = ud_mappe / FILNAVN
    if not sti.is_file():
        return None

    lag = QgsVectorLayer(f'{sti}|layername={LAGNAVN}', LAGNAVN, 'ogr')
    if not lag.isValid():
        if log is not None:
            log(f'{sti} kunne ikke læses — de eksisterende oplandsfiler bruges.')
        return None
    if 'type' not in lag.fields().names():
        if log is not None:
            log(f'{sti} mangler kolonnen "type" — de eksisterende filer bruges.')
        del lag
        return None

    if crs is None:
        crs = lag.crs()

    pr_type = {t: [] for t in TYPER}
    ukendte = set()
    rettet = 0
    for f in lag.getFeatures():
        g = QgsGeometry(f.geometry())
        if g.isEmpty():
            continue
        t = (f['type'] or '').strip().lower()
        maal = None
        for kendt in TYPER:
            if t == kendt.lower():
                maal = kendt
                break
        if maal is None:
            ukendte.add(f['type'])
            continue
        pr_type[maal].append(g)
        if 'beregnet_som' in f.fields().names() and f['beregnet_som'] != maal:
            rettet += 1
    del lag

    if ukendte and log is not None:
        log('Deloplande med en ukendt type springes over: '
            + ', '.join(repr(u) for u in sorted(ukendte, key=str))
            + f'. Brug {VANDLOEB!r} eller {DIREKTE!r}.')

    svar = {}
    for filnavn, lagnavn, type_ in LEVERANCER:
        geoms = pr_type[type_]
        samlet = QgsGeometry.unaryUnion(geoms) if geoms else QgsGeometry()
        if not samlet.isEmpty():
            samlet.convertToMultiType()
        vl = QgsVectorLayer(f'MultiPolygon?crs={crs.authid()}', lagnavn, 'memory')
        f = QgsFeature()
        f.setGeometry(samlet)
        vl.dataProvider().addFeature(f)
        vl.updateExtents()
        _skriv(ud_mappe / filnavn, lagnavn, vl)
        # En tom geometri giver et areal paa -0.0; det skal ikke staa i en log.
        svar[lagnavn] = max(0.0, samlet.area() / 1e4)

    # En tom kategori er lovlig, men naesten altid en fejltagelse: regnearket
    # regner videre paa nul uden at sige fra.
    if log is not None:
        for type_, filnavn in ((VANDLOEB, 'Vandoplandet.gpkg'),
                               (DIREKTE, 'Direkte_Opland.gpkg')):
            if not pr_type[type_]:
                log(f'ADVARSEL: intet delopland har typen {type_!r}, saa '
                    f'{filnavn} bliver tomt. Regnearket faar 0 ha for det. '
                    'Er alle deloplande flyttet over i den anden type?')

    if log is not None:
        besked = (f'Oplandene udledt af {len(pr_type[VANDLOEB])} + '
                  f'{len(pr_type[DIREKTE])} deloplande: vandløbsopland '
                  f'{svar["Vandoplandet"]:,.1f} ha, direkte opland '
                  f'{svar["Direkte_Opland"]:,.1f} ha')
        if rettet:
            besked += f' ({rettet} delopland(e) flyttet i hånden)'
        log(besked + '.')
    return svar
