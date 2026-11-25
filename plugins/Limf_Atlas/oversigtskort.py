# -*- coding: utf-8 -*-
"""Oversigtskort: ét kort over hele projektet med alle lodsejere.

Hvor atlasset giver én side pr. lodsejer, samler oversigtskortet alle på
én side (A3 eller A4, liggende):

  * Kortet til venstre viser projektgrænsen og hver lodsejers jord i sin
    egen kombination af farve og skravering, oven på baggrundskortet og
    de lag brugeren har valgt (fx genslyngning).
  * Panelet til højre har titel og projektnavn, en signaturforklaring med
    lodsejerne ("navn, adresse"), og nederst målestok, nordpil,
    sagsnummer, initialer, dato og et valgfrit logo.

Lodsejerne grupperes præcis som i atlasset (samme ejerfelt, samme
løbenumre), så numrene på oversigtskortet passer til atlassets sider.
Der bruges ingen skabelon: siden bygges i kode, så den kan tilpasse sig
papirformatet og antallet af lodsejere.
"""

import os
from datetime import date

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutMeasurement,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLinePatternFillSymbolLayer,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointPatternFillSymbolLayer,
    QgsPrintLayout,
    QgsRectangle,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)

from .atlas_builder import (
    COV_ADRESSE,
    COV_LOBENR,
    COV_NAVN,
    COV_OWNER,
    LEGEND_FONT,
    AtlasBuildError,
    AtlasBuilder,
    _farve_tekst,
    _log,
)

MM = Qgis.LayoutUnit.Millimeters

# Projektgrænsen står som en tyk sort kant, som i Mariagerfjords eksempel.
STANDARD_GRAENSE = QColor(0, 0, 0)

PAPIR_A3 = "A3"
PAPIR_A4 = "A4"

# Sidens mål (mm, liggende). Panelet til højre er bredere på A3, så
# lange lodsejernavne ikke skal brydes over for mange linjer.
SIDE = {
    #       bredde, højde, margen, panelbredde, titel pt, skalering
    PAPIR_A3: (420.0, 297.0, 7.0, 100.0, 18, 1.0),
    PAPIR_A4: (297.0, 210.0, 6.0, 78.0, 14, 0.8),
}
AFSTAND = 4.0          # luft mellem kortet og panelet og inde i panelet
LOGO_HOEJDE = 22.0     # logoets plads nederst i panelet (A3)

# Farverne lodsejerne tegnes med. Mørke, mættede farver, så skraveringen
# også kan ses oven på et luftfoto og på en sort-hvid udskrift.
PALET = [
    QColor(0, 0, 0),         # sort
    QColor(215, 25, 28),     # rød
    QColor(31, 120, 180),    # blå
    QColor(51, 160, 44),     # grøn
    QColor(176, 30, 160),    # magenta
    QColor(230, 120, 0),     # orange
    QColor(0, 145, 145),     # petrol
    QColor(120, 70, 20),     # brun
    QColor(106, 61, 154),    # violet
    QColor(128, 128, 0),     # oliven
    QColor(231, 41, 138),    # pink
    QColor(100, 100, 100),   # grå
]

# Mønstrene: (type, vinkler, afstand, stregbredde/prikstørrelse).
# Afstande og bredder er i mm på papiret, så skraveringen ser ens ud,
# uanset hvilken opløsning kortet eksporteres i (Qt's egne mønsterpensler
# tegnes i skærmpixels og bliver til grå støj i en PDF).
# Rækkefølgen veksler mellem typerne, så to lodsejere efter hinanden i
# forklaringen også får et mønster af en anden slags.
MOENSTRE = [
    ("linjer", (45,), 1.6, 0.25),
    ("prikker", (), 1.4, 0.45),
    ("linjer", (0,), 1.4, 0.25),
    ("linjer", (45, 135), 2.0, 0.2),
    ("linjer", (135,), 1.6, 0.25),
    ("linjer", (45,), 3.0, 0.5),
    ("linjer", (90,), 1.4, 0.25),
    ("prikker", (), 2.2, 0.8),
    ("linjer", (30,), 1.6, 0.25),
    ("linjer", (0, 90), 2.0, 0.2),
    ("linjer", (120,), 1.6, 0.25),
    ("flade", (), 0.0, 0.0),
    ("linjer", (60,), 1.6, 0.25),
    ("linjer", (30, 120), 2.2, 0.2),
    ("linjer", (150,), 1.6, 0.25),
    ("prikker+linjer", (45,), 2.4, 0.5),
    ("linjer", (135,), 3.0, 0.5),
    ("linjer", (60, 150), 2.2, 0.2),
]

#: Så mange lodsejere kan få hver sin kombination af farve og mønster.
UNIKKE_SYMBOLER = len(PALET) * len(MOENSTRE)

# Lyst slør under skraveringen: baggrundskortet anes, men mønstret står
# tydeligt frem.
SLOER = QColor(255, 255, 255, 140)

# Signaturforklaringens trin, fra størst til mindst. Det første der kan
# være på pladsen, bruges.
#   (kolonner, overskrift pt, tekst pt, symbolbredde mm, symbolhøjde mm)
#
# I panelet går det ikke under 5,5 pt og to kolonner — så er teksten for
# lille til at læse. Kan lodsejerne ikke være der, får de en side 2 for sig
# selv, hvor forklaringen fylder hele siden.
PANEL_TRIN = (
    (1, 10, 8, 7.0, 4.0),
    (1, 9, 7, 6.0, 3.5),
    (1, 8, 6, 5.0, 3.0),
    (2, 8, 6, 5.0, 3.0),
    (2, 7, 5.5, 4.5, 2.7),
)
SIDE2_TRIN = tuple(
    [(k, 10, 8, 7.0, 4.0) for k in (2, 3, 4)]
    + [(k, 9, 7, 6.0, 3.5) for k in (3, 4, 5)]
    + [(k, 8, 6, 5.0, 3.0) for k in (4, 5, 6)]
    + [(k, 7, 5, 4.5, 2.7) for k in (5, 6, 7)]
)

# Pæne målestokstal pr. dekade: 1:10.000, 1:12.500, 1:15.000 osv.
PAENE_MAALESTOKKE = (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5)


class OversigtsValg:
    """Det brugeren har valgt under fanen «Oversigtskort»."""

    def __init__(self):
        self.titel = "Lodsejerkort"
        self.projektnavn = ""
        self.sagsnummer = ""
        self.initialer = ""
        self.dato = date.today().strftime("%d.%m.%Y")
        #: Sti til et logo (png/jpg/svg), eller "" for intet logo.
        self.logo = ""
        self.papir = PAPIR_A3
        #: Løbenummer i hver lodsejers flade og foran navnet i forklaringen.
        self.vis_lobenr = False
        self.graense_farve = STANDARD_GRAENSE
        self.layout_navn = "Lodsejerkort"


def ejer_symbol(i):
    """Fyldsymbol nr. i: en kombination af farve og mønster.

    Farve og mønster skifter begge fra den ene lodsejer til den næste, så
    to naboer i forklaringen aldrig ligner hinanden. Kombinationen er
    entydig for de første UNIKKE_SYMBOLER = 12 * 18 = 216 lodsejere:
    to numre med samme farve ligger et multiplum k af 12 fra hinanden, og
    deres mønstre ligger så 13k fra hinanden. Da 13 og 18 ikke har fælles
    faktorer, går det først op i 18 ved k = 18 — altså ved nr. 216.
    """
    farve = PALET[i % len(PALET)]
    art, vinkler, afstand, bredde = MOENSTRE[
        (i // len(PALET) + i) % len(MOENSTRE)]

    if art == "flade":
        fyld = QColor(farve)
        fyld.setAlpha(110)
        return QgsFillSymbol.createSimple({
            "color": _farve_tekst(fyld),
            "style": "solid",
            "outline_color": _farve_tekst(farve),
            "outline_width": "0.25",
        })

    symbol = QgsFillSymbol.createSimple({
        "color": _farve_tekst(SLOER),
        "style": "solid",
        "outline_color": _farve_tekst(farve),
        "outline_width": "0.25",
    })
    if "linjer" in art:
        # Kombineret med prikker bliver stregen tynd og glissen, så
        # prikkerne stadig kan ses.
        linje_bredde = 0.2 if "prikker" in art else bredde
        linje_afstand = afstand * 1.5 if "prikker" in art else afstand
        for vinkel in vinkler:
            lag = QgsLinePatternFillSymbolLayer()
            lag.setLineAngle(vinkel)
            lag.setDistance(linje_afstand)
            lag.setDistanceUnit(Qgis.RenderUnit.Millimeters)
            lag.setSubSymbol(QgsLineSymbol.createSimple({
                "color": _farve_tekst(farve),
                "width": str(linje_bredde),
                "width_unit": "MM",
            }))
            symbol.appendSymbolLayer(lag)
    if "prikker" in art:  # forskudt hver anden række
        lag = QgsPointPatternFillSymbolLayer()
        lag.setDistanceX(afstand)
        lag.setDistanceY(afstand)
        lag.setDisplacementX(afstand / 2.0)
        for saet in (lag.setDistanceXUnit, lag.setDistanceYUnit,
                     lag.setDisplacementXUnit):
            saet(Qgis.RenderUnit.Millimeters)
        lag.setSubSymbol(QgsMarkerSymbol.createSimple({
            "name": "circle",
            "color": _farve_tekst(farve),
            "size": str(bredde),
            "size_unit": "MM",
            "outline_style": "no",
        }))
        symbol.appendSymbolLayer(lag)
    return symbol


def foelg_stil(kort, lag):
    """Tegn layoutets kort forfra, når et af lagene skifter stil.

    Layout Designer viser et gemt billede af kortet og tegner det ikke
    selv om, når et lag får ny symbologi — signaturforklaringen gør, men
    kortet blev stående med de gamle farver. Retter man en lodsejer under
    Egenskaber → Symbologi (eller med «Ret symboler…»), smides billedet
    derfor væk her, så kortet følger med.
    """
    def opdater(*_args):
        try:
            kort.invalidateCache()
        except RuntimeError:
            pass  # layoutet er slettet; intet at opdatere

    for lyr in lag:
        for signal in (lyr.rendererChanged, lyr.styleChanged,
                       lyr.repaintRequested):
            signal.connect(opdater)


def _sorteringsnoegle(tekst):
    """Alfabetisk på dansk: æ, ø, å står sidst og i den rækkefølge."""
    tekst = (tekst or "").casefold()
    for bogstav, erstatning in (("æ", "z{"), ("ø", "z|"), ("å", "z}"),
                                ("aa", "z}")):
        tekst = tekst.replace(bogstav, erstatning)
    return tekst


def paen_maalestok(maalestok):
    """Mindste pæne målestok, der mindst er så lille som den givne.

    Kortet zoomes ud til det nærmeste pæne tal (1:12.500 frem for
    1:11.873), så hele projektområdet stadig er med.
    """
    if not maalestok or maalestok <= 0:
        return maalestok
    dekade = 1.0
    while dekade * 10 <= maalestok:
        dekade *= 10
    while True:
        for trin in PAENE_MAALESTOKKE:
            if trin * dekade >= maalestok - 1e-6:
                return trin * dekade
        dekade *= 10


def maalestok_tekst(maalestok):
    """1:12500 → "1:12.500"."""
    return "1:{:,.0f}".format(maalestok).replace(",", ".")


def skalastok_inddeling(maalestok, bredde_mm, segmenter=4):
    """(meter pr. segment, antal segmenter) til en skalastok af højst bredde_mm.

    Segmentlængden er et pænt tal (1, 2, 2,5 eller 5 gange en tierpotens),
    så tallene under stokken bliver runde.
    """
    meter_pr_mm = maalestok / 1000.0
    hoejst = bredde_mm * meter_pr_mm / segmenter
    bedst = None
    potens = 1.0
    while potens <= hoejst * 10:
        for trin in (1.0, 2.0, 2.5, 5.0):
            laengde = trin * potens
            if laengde <= hoejst:
                bedst = laengde
        potens *= 10
    return (bedst or hoejst), segmenter


class OversigtskortBuilder(AtlasBuilder):
    """Bygger oversigtskortet som et almindeligt printlayout (uden atlas)."""

    def build(self, parcel_layer, field_mapping, generate_lobenr,
              generate_postnr, owner_field, valg, project_area_layer=None,
              background_auto=True, background_layer=None,
              extra_layers=None):
        if parcel_layer is None or not parcel_layer.isValid():
            raise AtlasBuildError("Det valgte lodsejerlag er ugyldigt.")
        if parcel_layer.featureCount() == 0:
            raise AtlasBuildError("Lodsejerlaget indeholder ingen objekter.")
        if not owner_field or \
                parcel_layer.fields().indexFromName(owner_field) < 0:
            raise AtlasBuildError("Ejer-grupperingsfeltet er ugyldigt.")
        papir = SIDE.get(valg.papir) or SIDE[PAPIR_A3]

        resolved = self._resolve_fields(
            parcel_layer, field_mapping, generate_lobenr, generate_postnr)

        # Baggrund og ekstra lag vælges på samme måde som i atlasset.
        exclude = {parcel_layer.id()}
        if project_area_layer is not None:
            exclude.add(project_area_layer.id())
        if background_layer is not None:
            baggrund = [background_layer]
        elif background_auto:
            baggrund = self._current_project_layers(exclude)
        else:
            baggrund = []
        baggrund_ids = {l.id() for l in baggrund}
        ekstra = []
        for lyr in extra_layers or []:
            if lyr is None or not lyr.isValid():
                continue
            if lyr.id() in exclude or lyr.id() in baggrund_ids:
                continue
            if lyr.id() not in {l.id() for l in ekstra}:
                ekstra.append(lyr)

        # Lodsejerne samles pr. ejer som i atlasset — alle kommer med, og
        # løbenumrene bliver de samme som atlassets sidenumre.
        ejere = self._build_owner_layer(
            parcel_layer, owner_field, resolved, project_area_layer,
            None, None, 0.0)
        ejere.setName("Lodsejere")
        antal = self._style_ejere(ejere, valg.vis_lobenr)
        graense = None
        if project_area_layer is not None:
            graense = self._style_graense(project_area_layer,
                                          valg.graense_farve, papir[5])
        _log("Oversigtskort: {} lodsejere, papir {}".format(antal, valg.papir))

        manager = self.project.layoutManager()
        navn = self._unique_name(manager, valg.layout_navn or "Lodsejerkort")
        self._laeg_i_lagpanelet(navn, [l for l in (ejere, graense) if l])
        #: Lodsejerlaget, så symbolerne kan rettes efter bygningen.
        self.ejere_lag = ejere

        layout = QgsPrintLayout(self.project)
        layout.initializeDefaults()
        bredde, hoejde, margen, panel_b, titel_pt, skala = papir
        layout.pageCollection().page(0).setPageSize(
            QgsLayoutSize(bredde, hoejde, MM))

        # --- Kortet ------------------------------------------------------
        panel_x = bredde - margen - panel_b
        kort = QgsLayoutItemMap(layout)
        kort.setId("Oversigtskort")
        layout.addLayoutItem(kort)
        kort.attemptMove(QgsLayoutPoint(margen, margen, MM))
        kort.attemptResize(QgsLayoutSize(
            panel_x - AFSTAND - margen, hoejde - 2 * margen, MM))
        kort.setCrs(parcel_layer.crs())
        # Top-først. De valgte lag ligger OVER lodsejerne — modsat atlasset,
        # hvor matriklerne kun er en kant. Her har hver lodsejer et slør og
        # en skravering, der ellers ville skjule fx genslyngningen.
        lag = ([graense] if graense is not None else []) + ekstra + \
            [ejere] + baggrund
        kort.setLayers(lag)
        kort.setKeepLayerSet(True)
        kort.setFollowVisibilityPreset(False)
        kort.setFrameEnabled(True)
        kort.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, MM))
        omfang = self._kortets_omfang(graense, ejere)
        kort.zoomToExtent(omfang)
        kort.setScale(paen_maalestok(kort.scale()))
        foelg_stil(kort, ([graense] if graense is not None else []) +
                   ekstra + [ejere])
        _log("Oversigtskort: målestok {}".format(maalestok_tekst(kort.scale())))

        # --- Panelet -----------------------------------------------------
        self._panel(layout, kort, graense, ejere, ekstra, valg, papir,
                    panel_x)

        layout.setName(navn)
        manager.addLayout(layout)
        return layout

    def _laeg_i_lagpanelet(self, navn, lag):
        """Læg oversigtskortets lag i en slukket gruppe øverst i lagpanelet.

        Så kan symbolerne rettes bagefter med QGIS' egen symbologi
        (højreklik → Egenskaber → Symbologi), og kortet og forklaringen i
        layoutet følger med. Gruppen er slukket, så lagene ikke fylder i
        kortvinduet — layoutet tegner dem alligevel, fordi kortet har sit
        eget faste lagsæt.
        """
        rod = self.project.layerTreeRoot()
        gruppe = rod.insertGroup(0, navn)
        for lyr in lag:
            self.project.addMapLayer(lyr, addToLegend=False)
            gruppe.addLayer(lyr)
        gruppe.setItemVisibilityChecked(False)
        gruppe.setExpanded(False)

    # ------------------------------------------------------------- lagene
    def _style_ejere(self, ejere, vis_lobenr):
        """Én kategori pr. lodsejer, med etiketten "navn, adresse".

        Sorteres alfabetisk på navnet — eller efter løbenummer, når numrene
        vises, så forklaringen kan læses som en nummerliste.
        """
        poster = []
        for f in ejere.getFeatures():
            navn = self._to_str(f[COV_NAVN]).strip()
            adresse = self._to_str(f[COV_ADRESSE]).strip()
            noegle = self._to_str(f[COV_OWNER])
            nr = f[COV_LOBENR]
            tekst = ", ".join(t for t in (navn or noegle, adresse) if t)
            if vis_lobenr and nr not in (None, ""):
                tekst = "{}. {}".format(nr, tekst)
            poster.append((noegle, tekst, navn or noegle, nr))

        if vis_lobenr:
            poster.sort(key=lambda p: (self._som_tal(p[3]),
                                       _sorteringsnoegle(p[2])))
        else:
            poster.sort(key=lambda p: (_sorteringsnoegle(p[2]), p[1]))

        kategorier = [QgsRendererCategory(noegle, ejer_symbol(i), tekst)
                      for i, (noegle, tekst, _n, _nr) in enumerate(poster)]
        if len(kategorier) > UNIKKE_SYMBOLER:
            _log("Oversigtskort: {} lodsejere — over {} gentages farve og "
                 "mønster".format(len(kategorier), UNIKKE_SYMBOLER))
        ejere.setRenderer(QgsCategorizedSymbolRenderer(COV_OWNER, kategorier))

        if vis_lobenr:
            self._lobenr_etiketter(ejere)
        else:
            ejere.setLabelsEnabled(False)
        ejere.triggerRepaint()
        return len(kategorier)

    @staticmethod
    def _som_tal(vaerdi):
        try:
            return int(vaerdi)
        except (TypeError, ValueError):
            return 10 ** 9

    @staticmethod
    def _lobenr_etiketter(ejere):
        """Løbenummeret midt i hver lodsejers flade, også de små."""
        pal = QgsPalLayerSettings()
        pal.fieldName = COV_LOBENR
        pal.isExpression = False
        try:
            pal.placement = Qgis.LabelPlacement.Horizontal
        except AttributeError:
            pass
        pal.displayAll = True
        fmt = QgsTextFormat()
        skrift = QFont(LEGEND_FONT)
        skrift.setBold(True)
        fmt.setFont(skrift)
        fmt.setSize(7)
        fmt.setSizeUnit(Qgis.RenderUnit.Points)
        fmt.setColor(QColor(0, 0, 0))
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setSize(0.8)
        buffer.setColor(QColor(255, 255, 255))
        fmt.setBuffer(buffer)
        pal.setFormat(fmt)
        ejere.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        ejere.setLabelsEnabled(True)

    def _style_graense(self, omraade, farve, skala):
        """Projektgrænsen som en tyk kant uden fyld (kopi af brugerens lag)."""
        graense = self._clone_layer(omraade, "Projektgrænse")
        graense.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
            "color": "0,0,0,0",
            "style": "no",
            "outline_color": _farve_tekst(farve, STANDARD_GRAENSE),
            "outline_width": "%.2f" % (0.9 * max(skala, 0.8)),
        })))
        graense.setLabelsEnabled(False)
        return graense

    def _kortets_omfang(self, graense, ejere):
        """Projektgrænsen med lidt luft — ellers alle lodsejernes jord.

        Grænsen bestemmer udsnittet, også når en lodsejers ejendom
        strækker sig langt ud over den; ellers kunne én stor gård zoome
        kortet så langt ud, at projektet bliver et frimærke.
        """
        omfang = None
        if graense is not None and graense.featureCount() > 0:
            omfang = QgsRectangle(graense.extent())
            if graense.crs().isValid() and ejere.crs().isValid() and \
                    graense.crs() != ejere.crs():
                try:
                    omfang = QgsCoordinateTransform(
                        graense.crs(), ejere.crs(), self.project
                    ).transformBoundingBox(omfang)
                except Exception:
                    omfang = None
        if omfang is None or omfang.isEmpty():
            omfang = QgsRectangle(ejere.extent())
        omfang.scale(1.08)
        return omfang

    # ------------------------------------------------------------- panelet
    def _panel(self, layout, kort, graense, ejere, ekstra, valg, papir,
               panel_x):
        bredde, hoejde, margen, panel_b, titel_pt, skala = papir
        top = margen
        bund = hoejde - margen
        indre_x = panel_x + AFSTAND
        indre_b = panel_b - 2 * AFSTAND

        # Rammen om panelet — tilføjes først, så den ligger nederst.
        ramme = QgsLayoutItemShape(layout)
        ramme.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        ramme.setSymbol(QgsFillSymbol.createSimple({
            "color": "255,255,255,255", "style": "solid",
            "outline_color": "0,0,0,255", "outline_width": "0.3"}))
        layout.addLayoutItem(ramme)
        ramme.attemptMove(QgsLayoutPoint(panel_x, top, MM))
        ramme.attemptResize(QgsLayoutSize(panel_b, bund - top, MM))

        # Titel og projektnavn øverst.
        y = top + AFSTAND
        titel_h = titel_pt * 0.55
        self._tekst(layout, valg.titel or "Lodsejerkort", indre_x, y,
                    indre_b, titel_h, titel_pt, fed=True)
        y += titel_h + 1.0
        if valg.projektnavn:
            navn_pt = 11 * skala
            linjer = self._linjer(valg.projektnavn, indre_b, navn_pt)
            navn_h = linjer * navn_pt * 0.45 + 1.0
            self._tekst(layout, valg.projektnavn, indre_x, y, indre_b,
                        navn_h, navn_pt)
            y += navn_h
        y += AFSTAND

        # Bunden, nedefra: logo, oplysninger, målestok + nordpil.
        y_bund = bund - AFSTAND
        logo = valg.logo if valg.logo and os.path.exists(valg.logo) else ""
        if valg.logo and not logo:
            _log("Oversigtskort: logoet findes ikke: {}".format(valg.logo))
        if logo:
            logo_h = LOGO_HOEJDE * skala
            self._logo(layout, logo, indre_x, y_bund - logo_h, indre_b, logo_h)
            y_bund -= logo_h + AFSTAND

        info_pt = 8 * skala
        linjer = []
        if valg.sagsnummer:
            linjer.append("Sagsnummer: {}".format(valg.sagsnummer))
        if valg.initialer:
            linjer.append("Initialer: {}".format(valg.initialer))
        if valg.dato:
            linjer.append("Dato: {}".format(valg.dato))
        linjer.append("Målestok {} (ved {} liggende)".format(
            maalestok_tekst(kort.scale()), valg.papir))
        info_h = len(linjer) * info_pt * 0.45 + 1.5
        self._tekst(layout, "\n".join(linjer), indre_x, y_bund - info_h,
                    indre_b, info_h, info_pt)
        y_bund -= info_h + AFSTAND

        pil = 11.0 * skala
        stok_h = 8.0 * skala
        self._nordpil(layout, kort, indre_x + indre_b - pil, y_bund - pil, pil)
        self._skalastok(layout, kort, indre_x, y_bund - stok_h,
                        indre_b - pil - AFSTAND, stok_h, skala)
        y_bund -= max(pil, stok_h) + AFSTAND

        # Signaturforklaringen får resten — med lodsejerne, hvis de kan
        # være der i en læselig størrelse. Ellers får de side 2.
        i_kortet = [l for l in ekstra
                    if self._har_objekter_i(l, kort.extent(), kort.crs())]
        fast = [graense] if graense is not None else []
        forklaring = self._forklaring(
            layout, kort, fast + [ejere] + i_kortet, indre_x, y, indre_b,
            y_bund - y, PANEL_TRIN, kan_opgive=True)
        if forklaring is not None:
            return

        _log("Oversigtskort: lodsejerne kan ikke være i panelet — de får "
             "side 2")
        hoejde_brugt = 0.0
        if fast or i_kortet:
            forklaring = self._forklaring(
                layout, kort, fast + i_kortet, indre_x, y, indre_b,
                y_bund - y, PANEL_TRIN)
            hoejde_brugt = forklaring.sizeWithUnits().height() + AFSTAND
        henvis_pt = 9 * skala
        self._tekst(layout, "Lodsejere: se signaturforklaringen på side 2.",
                    indre_x, y + hoejde_brugt, indre_b, henvis_pt * 0.9,
                    henvis_pt, fed=True)
        self._side_to(layout, kort, ejere, valg, papir)

    def _side_to(self, layout, kort, ejere, valg, papir):
        """Side 2: lodsejernes signaturforklaring over hele siden."""
        bredde, hoejde, margen, _panel_b, titel_pt, skala = papir
        side = QgsLayoutItemPage(layout)
        side.setPageSize(QgsLayoutSize(bredde, hoejde, MM))
        layout.pageCollection().addPage(side)
        nr = layout.pageCollection().pageCount() - 1

        ramme = QgsLayoutItemShape(layout)
        ramme.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        ramme.setSymbol(QgsFillSymbol.createSimple({
            "color": "255,255,255,255", "style": "solid",
            "outline_color": "0,0,0,255", "outline_width": "0.3"}))
        layout.addLayoutItem(ramme)
        ramme.attemptMove(QgsLayoutPoint(margen, margen, MM), True, False, nr)
        ramme.attemptResize(QgsLayoutSize(bredde - 2 * margen,
                                          hoejde - 2 * margen, MM))

        x = margen + AFSTAND
        b = bredde - 2 * (margen + AFSTAND)
        y = margen + AFSTAND
        titel_h = titel_pt * 0.55
        overskrift = "{} – lodsejere".format(valg.titel or "Lodsejerkort")
        self._tekst(layout, overskrift, x, y, b, titel_h, titel_pt, fed=True,
                    side=nr)
        y += titel_h + 1.0
        if valg.projektnavn:
            navn_pt = 11 * skala
            self._tekst(layout, valg.projektnavn, x, y, b, navn_pt * 0.5,
                        navn_pt, side=nr)
            y += navn_pt * 0.5
        y += AFSTAND
        self._forklaring(layout, kort, [ejere], x, y, b,
                         hoejde - margen - AFSTAND - y, SIDE2_TRIN, side=nr,
                         navn="Signaturforklaring – lodsejere")

    def _tekst(self, layout, tekst, x, y, b, h, pt, fed=False, side=0):
        label = QgsLayoutItemLabel(layout)
        label.setText(tekst)
        fmt = QgsTextFormat()
        skrift = QFont(LEGEND_FONT)
        skrift.setBold(fed)
        fmt.setFont(skrift)
        fmt.setSize(pt)
        fmt.setSizeUnit(Qgis.RenderUnit.Points)
        fmt.setColor(QColor(0, 0, 0))
        label.setTextFormat(fmt)
        label.setMargin(0)
        layout.addLayoutItem(label)
        label.attemptMove(QgsLayoutPoint(x, y, MM), True, False, side)
        label.attemptResize(QgsLayoutSize(b, h, MM))
        return label

    @staticmethod
    def _linjer(tekst, bredde_mm, pt):
        """Groft skøn over hvor mange linjer en tekst brydes over."""
        tegn_pr_linje = max(10, int(bredde_mm / (pt * 0.19)))
        linjer = 0
        for afsnit in tekst.splitlines() or [""]:
            linjer += max(1, -(-len(afsnit) // tegn_pr_linje))
        return linjer

    @staticmethod
    def _logo(layout, sti, x, y, b, h):
        billede = QgsLayoutItemPicture(layout)
        billede.setId("Logo")
        layout.addLayoutItem(billede)
        billede.attemptMove(QgsLayoutPoint(x, y, MM))
        billede.attemptResize(QgsLayoutSize(b, h, MM))
        billede.setResizeMode(QgsLayoutItemPicture.ResizeMode.Zoom)
        billede.setPictureAnchor(QgsLayoutItem.ReferencePoint.Middle)
        billede.setPicturePath(sti)

    @staticmethod
    def _nordpil(layout, kort, x, y, stoerrelse):
        pil = QgsLayoutItemPicture(layout)
        pil.setId("Nordpil")
        layout.addLayoutItem(pil)
        pil.attemptMove(QgsLayoutPoint(x, y, MM))
        pil.attemptResize(QgsLayoutSize(stoerrelse, stoerrelse, MM))
        pil.setPicturePath(
            ":/images/north_arrows/layout_default_north_arrow.svg",
            Qgis.PictureFormat.SVG)
        pil.setLinkedMap(kort)

    @staticmethod
    def _skalastok(layout, kort, x, y, bredde, hoejde, skala):
        stok = QgsLayoutItemScaleBar(layout)
        stok.setId("Skalastok")
        stok.setStyle("Single Box")
        layout.addLayoutItem(stok)
        stok.setLinkedMap(kort)
        meter, segmenter = skalastok_inddeling(kort.scale(), bredde * 0.9)
        # setUnits() omregner selv fra kortets meter. Etiket-divisoren
        # (mapUnitsPerScaleBarUnit) skal derfor blive 1 — sat til 1000 ved
        # km stod der 0,00025 km under stokken i stedet for 0,25.
        stok.setMapUnitsPerScaleBarUnit(1.0)
        if meter * segmenter >= 1000:
            stok.setUnits(Qgis.DistanceUnit.Kilometers)
            stok.setUnitLabel("km")
            stok.setUnitsPerSegment(meter / 1000.0)
        else:
            stok.setUnits(Qgis.DistanceUnit.Meters)
            stok.setUnitLabel("m")
            stok.setUnitsPerSegment(meter)
        stok.setNumberOfSegmentsLeft(0)
        stok.setNumberOfSegments(segmenter)
        stok.setHeight(1.8 * skala)
        fmt = stok.textFormat()
        skrift = QFont(LEGEND_FONT)
        fmt.setFont(skrift)
        fmt.setSize(7 * skala)
        fmt.setSizeUnit(Qgis.RenderUnit.Points)
        stok.setTextFormat(fmt)
        stok.attemptMove(QgsLayoutPoint(x, y, MM))
        stok.resizeToMinimumWidth()
        stok.update()

    def _forklaring(self, layout, kort, lag, x, y, b, h, trin, side=0,
                    navn="Signaturforklaring", kan_opgive=False):
        """Signaturforklaring over de givne lag — så stort det kan være.

        Trinene prøves fra største skrift og færrest kolonner ned mod
        mindre skrift og flere kolonner. Lange navne brydes automatisk ved
        kolonnens bredde.

        Kan intet trin være på pladsen, og ``kan_opgive`` er sat, fjernes
        forklaringen igen og der returneres None. Ellers beskæres den.
        """
        forklaring = QgsLayoutItemLegend(layout)
        forklaring.setId(navn)
        forklaring.setTitle("")
        layout.addLayoutItem(forklaring)
        forklaring.setLinkedMap(kort)
        forklaring.setAutoUpdateModel(False)
        rod = forklaring.model().rootGroup()
        rod.removeAllChildren()
        for lyr in lag:
            rod.addLayer(lyr)
        forklaring.setBackgroundEnabled(False)
        forklaring.setFrameEnabled(False)
        forklaring.setBoxSpace(0)
        forklaring.setColumnSpace(3.0)
        forklaring.setResizeToContents(True)
        forklaring.attemptMove(QgsLayoutPoint(x, y, MM), True, False, side)

        sidst = None
        for kolonner, overskrift, tekst, sb, sh in trin:
            forklaring.setColumnCount(kolonner)
            forklaring.setSplitLayer(kolonner > 1)
            forklaring.setEqualColumnWidth(kolonner > 1)
            self._legend_fonts(forklaring, titel=overskrift, lag=overskrift,
                               symbol=tekst)
            forklaring.setSymbolWidth(sb)
            forklaring.setSymbolHeight(sh)
            kolonne_b = (b - (kolonner - 1) * 3.0) / kolonner
            forklaring.setAutoWrapLinesAfter(max(10.0, kolonne_b - sb - 3.0))
            fb, fh = self._tilpas_forklaring(forklaring, layout)
            sidst = (kolonner, tekst, fb, fh)
            if fh <= h and fb <= b:
                break
        kolonner, tekst, fb, fh = sidst
        if fh > h or fb > b:
            if kan_opgive:
                layout.removeLayoutItem(forklaring)
                return None
            _log("Oversigtskort: forklaringen er større end pladsen "
                 "({:.0f} x {:.0f} mm mod {:.0f} x {:.0f}) — den beskæres"
                 .format(fb, fh, b, h))
            forklaring.setResizeToContents(False)
            forklaring.attemptResize(QgsLayoutSize(min(fb, b), min(fh, h), MM))
        _log("Oversigtskort: {} i {} kolonne(r), {} pt, {:.0f} x {:.0f} mm"
             .format(navn, kolonner, tekst, fb, fh))
        return forklaring
