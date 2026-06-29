# -*- coding: utf-8 -*-
"""Bygger atlasset ud fra mapbook-skabelonen.

Datamodellen (bekræftet med bruger):
  * Inputlaget har én række PR. MATRIKEL, men flere matrikler kan dele ejer.
  * Atlasset skal have én side PR. EJER med alle ejerens matrikler tegnet
    samlet – ikke alle øvrige matrikler.

Fremgangsmåde:
  1. Generér manglende felter (løbenr/postnr) på matrikellaget.
  2. Byg et midlertidigt COVERAGE-lag dissolvet pr. ejer: én feature pr. ejer,
     med multipolygon af alle ejerens matrikler + ejerens tekstfelter. Atlasset
     itererer dette lag → korrekt én-side-pr-ejer og auto-zoom til alle matrikler.
  3. Byg et midlertidigt HIGHLIGHT-lag (kopi af matriklerne) hvis renderer kun
     tegner DEN AKTUELLE ejers matrikler (filter mod atlas-featuren).
  4. Tilpas skabelonens XML (feltnavne, fjern arealtabel).
  5. Konfigurér de to kort: hovedkort = atlas-styret extent; oversigtskort =
     fast extent fra projektområde-laget. Begge viser highlight-laget.

Eksport overlades til brugeren i Layout Designer.
"""

import os
import re

from qgis.PyQt.QtXml import QDomDocument
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsPrintLayout,
    QgsReadWriteContext,
    QgsField,
    QgsFields,
    QgsFeature,
    QgsGeometry,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsVectorLayer,
    QgsMapLayer,
    QgsLayoutItemMap,
    QgsSingleSymbolRenderer,
    QgsRuleBasedRenderer,
    QgsFillSymbol,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsVectorLayerSimpleLabeling,
    QgsMessageLog,
    Qgis,
)
from qgis.PyQt.QtGui import QColor


def _log(msg):
    """Skriv diagnostik til loggen under fanen 'Atlas Mapbook'."""
    QgsMessageLog.logMessage(str(msg), "Atlas Mapbook", Qgis.Info)

from .template_spec import PLACEHOLDERS

# Map-item id'er i skabelonen (jf. mapbook_skabelon.qpt).
MAIN_MAP_ID = "Kort 1"        # atlas-styret hovedkort
OVERVIEW_MAP_ID = "Kort 2"    # oversigtskort over hele projektområdet


class AtlasBuildError(Exception):
    """Rejses ved fejl som skal vises pænt til brugeren."""


# Navne på de virtuelle/genererede felter.
GENERATED_LOBENR_FIELD = "atlas_lobenr"
GENERATED_POSTNR_FIELD = "atlas_postnr"

# Feltnavne på det dissolvede coverage-lag.
COV_OWNER = "ejer"
COV_NAVN = "navn"
COV_ADRESSE = "adresse"
COV_POSTNR = "postnr"
COV_LOBENR = "lobenr"

# Mindste matrikel-areal (m²) INDEN FOR projektgrænsen for at en ejer får en
# atlas-side. 0,1 ha = 1000 m². Ejere derunder udelades.
MIN_AREA_M2 = 1000.0

# Arealtabellens felter på coverage-laget (matcher skabelonens kolonner).
AREA_OMDRIFT = "Omdrift_ha"
AREA_GRAES = "Græs_ha"
AREA_NATUR = "Natur_ha"

# Hvilke Satskort-kategorier i referencekortet der tæller som hhv. omdrift og
# permanent græs (case-insensitivt 'contains'-match).
SATS_OMDRIFT_HINT = "omdrift"
SATS_GRAES_HINT = "græs"

# Feltet i det medfølgende referencekort der angiver arealanvendelse.
REFERENCE_CATEGORY_FIELD = "Satskort"

# Årstal-interval for det medfølgende referencekort (vises i overskriften).
REFERENCE_YEARS = "2021-2023"


class AtlasBuilder:
    def __init__(self, project, template_path, iface=None):
        self.project = project
        self.template_path = template_path
        self.iface = iface

    # ------------------------------------------------------------- public
    def build(self, coverage_layer, field_mapping, generate_lobenr,
              generate_postnr, layout_name, owner_field=None,
              project_area_layer=None, background_auto=True,
              background_layer=None, reference_path=None):
        parcel_layer = coverage_layer  # input = matrikellag
        if parcel_layer is None or not parcel_layer.isValid():
            raise AtlasBuildError("Det valgte lodsejerlag er ugyldigt.")
        if parcel_layer.featureCount() == 0:
            raise AtlasBuildError("Lodsejerlaget indeholder ingen objekter.")
        if not owner_field or parcel_layer.fields().indexFromName(owner_field) < 0:
            raise AtlasBuildError("Ejer-grupperingsfeltet er ugyldigt.")

        # 1) Sørg for at de nødvendige felter findes på matrikellaget.
        resolved = self._resolve_fields(
            parcel_layer, field_mapping, generate_lobenr, generate_postnr
        )

        # Baggrundskort: enten et bestemt valgt lag, eller auto (rasterlag).
        # Udelad matrikellaget og projektgrænse-laget, så de ikke vises som
        # ufiltreret baggrund under vores stylede kopier.
        exclude = {parcel_layer.id()}
        if project_area_layer is not None:
            exclude.add(project_area_layer.id())
        if background_layer is not None:
            existing_layers = [background_layer]
            _log("Baggrundskort (valgt): {}".format(background_layer.name()))
        elif background_auto:
            existing_layers = self._current_project_layers(exclude)
        else:
            existing_layers = []
            _log("Baggrundskort: intet")

        # Find matrikelnummer-feltet til etiketter (auto, ud fra feltnavn).
        matrikel_field = self._detect_matrikel_field(parcel_layer)

        # Referencekortet er en fast del af pluginnet – indlæs det fra disk.
        reference_layer = None
        if reference_path and os.path.exists(reference_path):
            ref = QgsVectorLayer(reference_path, "Referencekort", "ogr")
            if ref.isValid() and ref.featureCount() > 0:
                reference_layer = ref
                _log("Referencekort indlæst: {} features".format(
                    ref.featureCount()))
            else:
                _log("Referencekort ugyldigt/tomt: {}".format(reference_path))
        elif reference_path:
            _log("Referencekort ikke fundet: {}".format(reference_path))

        # Filter der begrænser til den aktuelle atlas-ejer (tekst-sammenligning
        # så numerisk ejerfelt ikke giver type-mismatch).
        owner_filter = (
            "to_string(\"{pf}\") = "
            "to_string(attribute(@atlas_feature, '{cov}'))"
        ).format(pf=owner_field, cov=COV_OWNER)

        # 2) Byg dissolvet ejer-lag (coverage) + to matrikellag + projektgrænse.
        #    Kun ejere med ≥ MIN_AREA_HA matrikel-areal INDEN FOR projektgrænsen
        #    får en side (kræver et projektgrænse-lag; ellers medtages alle).
        owner_layer = self._build_owner_layer(
            parcel_layer, owner_field, resolved, project_area_layer,
            reference_layer, REFERENCE_CATEGORY_FIELD)
        # Hovedkort: geometrien klippes til ejeren via atlas-clipping (tegn alle),
        # men etiketter begrænses til ejerens matrikler (clipping rammer ikke labels).
        main_parcels = self._build_highlight_layer(
            parcel_layer, "Matrikler (ejer)", matrikel_field,
            render_filter=None, label_filter=owner_filter, with_labels=True)
        # Oversigtskort: kun ejerens matrikler tegnes (filtreret), INGEN labels.
        overview_parcels = self._build_highlight_layer(
            parcel_layer, "Matrikler (oversigt)", matrikel_field,
            render_filter=owner_filter, with_labels=False)
        area_layer = None
        if project_area_layer is not None:
            area_layer = self._style_area_layer(project_area_layer)

        _log("Input '{}' provider={} features={}".format(
            parcel_layer.name(), parcel_layer.providerType(),
            parcel_layer.featureCount()))
        _log("Matrikelnummer-felt: {}".format(matrikel_field))
        _log("Ejer-lag (coverage): features={}".format(owner_layer.featureCount()))
        _log("Matrikler (ejer/oversigt): valid={} features={}".format(
            main_parcels.isValid(), main_parcels.featureCount()))
        if area_layer is not None:
            _log("Projektgrænse: valid={} features={}".format(
                area_layer.isValid(), area_layer.featureCount()))

        # Tilføj hjælpelagene til projektet så atlas/kort kan referere dem,
        # men UDEN at lægge dem i lagpanelet (addToLegend=False) – de er kun
        # til layoutet og skal ikke fylde i canvas/lagtræet.
        self.project.addMapLayer(owner_layer, addToLegend=False)
        self.project.addMapLayer(main_parcels, addToLegend=False)
        self.project.addMapLayer(overview_parcels, addToLegend=False)
        if area_layer is not None:
            self.project.addMapLayer(area_layer, addToLegend=False)

        # Coverage-feltnavnene er nu faste (COV_*). Postnr-kolonnen findes altid
        # på ejer-laget (tom hvis ikke valgt), så vi mapper den altid.
        cov_resolved = {
            "navn": COV_NAVN,
            "adresse": COV_ADRESSE,
            "postnr": COV_POSTNR,
            "lobenr": COV_LOBENR,
        }

        # 3) Indlæs og tilpas skabelonens XML.
        doc = self._load_template_doc()
        self._rewrite_template_fields(doc, cov_resolved)
        self._update_reference_years(doc)
        has_table = reference_layer is not None
        if has_table:
            # Behold arealtabellen og bind den til ejer-laget (ha-felterne der).
            self._rebind_area_table(doc, owner_layer)
        else:
            # Intet referencekort → drop tabellen som hidtil.
            self._remove_area_table(doc)
        self._bind_coverage_layer(doc, owner_layer)

        # 4) Opret layout og indlæs den tilpassede skabelon.
        layout = QgsPrintLayout(self.project)
        layout.initializeDefaults()
        context = QgsReadWriteContext()
        ok, _ = layout.loadFromTemplate(doc, context)
        if not ok:
            raise AtlasBuildError("Skabelonen kunne ikke indlæses som layout.")

        # 5) Atlas + kort.
        self._configure_atlas(layout, owner_layer)
        self._configure_maps(
            layout, main_parcels, overview_parcels, area_layer, existing_layers
        )

        # 6) Tilføj layoutet til projektet (unikt navn).
        manager = self.project.layoutManager()
        layout.setName(self._unique_name(manager, layout_name))
        manager.addLayout(layout)
        return layout

    # ---------------------------------------------------------- felt-setup
    def _resolve_fields(self, layer, field_mapping, generate_lobenr,
                        generate_postnr):
        """Returnér dict: placeholder.key -> faktisk feltnavn på matrikellaget."""
        resolved = dict(field_mapping)

        if generate_lobenr:
            self._ensure_lobenr_field(layer)
            resolved["lobenr"] = GENERATED_LOBENR_FIELD

        if generate_postnr:
            addr_field = field_mapping.get("adresse")
            self._ensure_postnr_field(layer, addr_field)
            resolved["postnr"] = GENERATED_POSTNR_FIELD

        existing = {f.name() for f in layer.fields()}
        for ph in PLACEHOLDERS:
            name = resolved.get(ph.key)
            if ph.required and (not name or name not in existing):
                raise AtlasBuildError(
                    "Det påkrævede felt for «{}» mangler i laget.".format(ph.label)
                )
        return resolved

    def _ensure_lobenr_field(self, layer):
        if layer.fields().indexFromName(GENERATED_LOBENR_FIELD) >= 0:
            return
        field = QgsField(GENERATED_LOBENR_FIELD, QVariant.LongLong)
        layer.addExpressionField("@id", field)
        if layer.fields().indexFromName(GENERATED_LOBENR_FIELD) < 0:
            raise AtlasBuildError("Kunne ikke generere løbenummer-felt på laget.")

    def _ensure_postnr_field(self, layer, addr_field):
        if layer.fields().indexFromName(GENERATED_POSTNR_FIELD) >= 0:
            return
        if not addr_field or layer.fields().indexFromName(addr_field) < 0:
            expr = "''"
        else:
            expr = (
                "coalesce("
                "regexp_substr(\"{f}\", '\\\\b(\\\\d{{4}}\\\\s.*)$'), "
                "regexp_substr(\"{f}\", '\\\\b(\\\\d{{4}})\\\\b'), "
                "''"
                ")"
            ).format(f=addr_field)
        field = QgsField(GENERATED_POSTNR_FIELD, QVariant.String, len=254)
        layer.addExpressionField(expr, field)
        if layer.fields().indexFromName(GENERATED_POSTNR_FIELD) < 0:
            raise AtlasBuildError("Kunne ikke generere postnr-felt på laget.")

    # --------------------------------------------------- dissolve pr. ejer
    def _build_owner_layer(self, parcel_layer, owner_field, resolved,
                           project_area_layer=None, reference_layer=None,
                           reference_field=None):
        """Byg et midlertidigt lag med én feature pr. ejer.

        Geometrien er foreningen (collect) af alle ejerens matrikler, så
        atlas-zoom dækker hele ejendommen. Tekstfelterne tages fra ejerens
        første matrikel (de er ens pr. ejer i lodsejerdata).

        Hvis et projektgrænse-lag er givet, udelades ejere hvis samlede
        matrikel-areal INDEN FOR projektgrænsen er under MIN_AREA_M2.

        Er et referencekort givet, beregnes arealtabellens felter pr. ejer
        (Omdrift_ha / Græs_ha; Natur = 0) ud fra matrikel ∩ referencekort,
        afgrænset til projektgrænsen.
        """
        # Projektgrænsens geometri (forening af alle dens features) til at
        # afgøre areal inden for grænsen. None = intet arealfilter.
        boundary = self._boundary_geometry(project_area_layer, parcel_layer)

        # Forbered referencekortet ÉN gang: klip til projektområdet og opdel i
        # forenede geometrier pr. kategori (omdrift / permanent græs).
        sats_geoms = self._prepare_reference(
            reference_layer, reference_field, boundary, parcel_layer)

        crs = parcel_layer.crs().authid() or "EPSG:25832"
        uri = "MultiPolygon?crs={}".format(crs)
        owner_layer = QgsVectorLayer(uri, "atlas_ejere", "memory")
        if not owner_layer.isValid():
            raise AtlasBuildError("Kunne ikke oprette det grupperede ejer-lag.")

        fields = QgsFields()
        fields.append(QgsField(COV_OWNER, QVariant.String, len=254))
        fields.append(QgsField(COV_NAVN, QVariant.String, len=254))
        fields.append(QgsField(COV_ADRESSE, QVariant.String, len=254))
        fields.append(QgsField(COV_POSTNR, QVariant.String, len=254))
        fields.append(QgsField(COV_LOBENR, QVariant.LongLong))
        fields.append(QgsField(AREA_OMDRIFT, QVariant.Double))
        fields.append(QgsField(AREA_GRAES, QVariant.Double))
        fields.append(QgsField(AREA_NATUR, QVariant.Double))
        owner_layer.dataProvider().addAttributes(fields.toList())
        owner_layer.updateFields()

        navn_f = resolved.get("navn")
        adr_f = resolved.get("adresse")
        post_f = resolved.get("postnr")

        # Saml geometrier og attributter pr. ejer.
        groups = {}
        order = []
        for feat in parcel_layer.getFeatures():
            owner_val = feat[owner_field]
            owner_key = "" if owner_val is None else str(owner_val)
            if owner_key not in groups:
                groups[owner_key] = {"geoms": [], "attrs": None}
                order.append(owner_key)
            g = feat.geometry()
            if g and not g.isEmpty():
                groups[owner_key]["geoms"].append(g)
            if groups[owner_key]["attrs"] is None:
                groups[owner_key]["attrs"] = {
                    COV_NAVN: feat[navn_f] if navn_f else owner_key,
                    COV_ADRESSE: feat[adr_f] if adr_f else "",
                    COV_POSTNR: feat[post_f] if post_f else "",
                }

        out_feats = []
        skipped = 0
        lobenr = 0  # løbenummer tildeles kun de ejere der medtages
        for owner_key in order:
            data = groups[owner_key]
            geom = self._collect_geometries(data["geoms"])

            # Geometri afgrænset til projektgrænsen (areal + tabel beregnes herpå).
            inside = geom
            if boundary is not None and geom is not None:
                g = geom if geom.isGeosValid() else geom.makeValid()
                inside = g.intersection(boundary)

            # Arealfilter: kun ejere med ≥ MIN_AREA_M2 inden for projektgrænsen.
            if boundary is not None:
                area_inside = inside.area() if inside and not inside.isEmpty() else 0.0
                if len(out_feats) + skipped < 5:
                    _log("  ejer {!r}: areal i grænse = {:.0f} m²".format(
                        owner_key[:30], area_inside))
                if area_inside < MIN_AREA_M2:
                    skipped += 1
                    continue

            lobenr += 1
            attrs = data["attrs"] or {}
            f = QgsFeature(owner_layer.fields())
            f.setAttribute(COV_OWNER, owner_key)
            f.setAttribute(COV_NAVN, self._to_str(attrs.get(COV_NAVN)))
            f.setAttribute(COV_ADRESSE, self._to_str(attrs.get(COV_ADRESSE)))
            f.setAttribute(COV_POSTNR, self._to_str(attrs.get(COV_POSTNR)))
            f.setAttribute(COV_LOBENR, lobenr)

            # Arealtabel pr. ejer (ha) ud fra referencekortet, hvis givet.
            omdrift_ha, graes_ha, natur_ha = self._owner_area_table(
                inside, sats_geoms)
            f.setAttribute(AREA_OMDRIFT, omdrift_ha)
            f.setAttribute(AREA_GRAES, graes_ha)
            f.setAttribute(AREA_NATUR, natur_ha)

            if geom is not None:
                f.setGeometry(geom)
            out_feats.append(f)

        owner_layer.dataProvider().addFeatures(out_feats)
        owner_layer.updateExtents()
        _log("Ejere: {} medtaget, {} udeladt (<0,1 ha i projektgrænsen)".format(
            len(out_feats), skipped))
        if owner_layer.featureCount() == 0:
            raise AtlasBuildError(
                "Ingen ejere har mindst 0,1 ha inden for projektgrænsen.")
        return owner_layer

    # ------------------------------------------------ referencekort/arealtabel
    def _prepare_reference(self, reference_layer, reference_field, boundary,
                           parcel_layer):
        """Forbered referencekortet: forenet geometri pr. Satskort-kategori.

        Returnerer dict {'omdrift': QgsGeometry, 'graes': QgsGeometry} eller
        None hvis intet referencekort. Referencekortet klippes til projekt-
        området (boundary, ellers matriklernes extent) ÉN gang, så de tunge
        skæringer pr. ejer kun rammer en lille delmængde.
        """
        if reference_layer is None or not reference_field:
            return None
        if reference_layer.fields().indexFromName(reference_field) < 0:
            _log("Referencefelt '{}' findes ikke – dropper tabel".format(
                reference_field))
            return None

        # Afgrænsningsgeometri til at hente kun relevante reference-features.
        clip_geom = boundary
        if clip_geom is None:
            ext = parcel_layer.extent()
            clip_geom = QgsGeometry.fromRect(ext)

        # CRS-transform fra reference → matrikellag hvis nødvendigt.
        xform = None
        ref_crs = reference_layer.crs()
        dst_crs = parcel_layer.crs()
        if ref_crs.isValid() and dst_crs.isValid() and ref_crs != dst_crs:
            try:
                xform = QgsCoordinateTransform(ref_crs, dst_crs, self.project)
            except Exception:
                xform = None

        # Bbox til request i reference-lagets eget CRS.
        request_rect = clip_geom.boundingBox()
        if xform is not None:
            try:
                inv = QgsCoordinateTransform(dst_crs, ref_crs, self.project)
                request_rect = inv.transformBoundingBox(clip_geom.boundingBox())
            except Exception:
                pass

        request = QgsFeatureRequest().setFilterRect(request_rect)
        omdrift_parts, graes_parts = [], []
        n = 0
        for feat in reference_layer.getFeatures(request):
            cat = feat[reference_field]
            cat_l = "" if cat is None else str(cat).lower()
            g = feat.geometry()
            if g is None or g.isEmpty():
                continue
            if xform is not None:
                try:
                    g = QgsGeometry(g)
                    g.transform(xform)
                except Exception:
                    continue
            # Klip til projektområdet for at holde delmængden lille.
            g = g.intersection(clip_geom)
            if g is None or g.isEmpty():
                continue
            if SATS_OMDRIFT_HINT in cat_l:
                omdrift_parts.append(g)
            elif SATS_GRAES_HINT in cat_l:
                graes_parts.append(g)
            n += 1

        result = {
            "omdrift": QgsGeometry.unaryUnion(omdrift_parts) if omdrift_parts else None,
            "graes": QgsGeometry.unaryUnion(graes_parts) if graes_parts else None,
        }
        _log("Referencekort forberedt: {} features i projektområdet "
             "(omdrift={}, græs={})".format(
                 n, omdrift_parts and not result["omdrift"].isEmpty(),
                 graes_parts and not result["graes"].isEmpty()))
        return result

    @staticmethod
    def _owner_area_table(owner_geom, sats_geoms):
        """Returnér (omdrift_ha, græs_ha, andet_ha) for en ejers geometri.

        owner_geom er ejerens matrikler afgrænset til projektgrænsen.
        «Andet» = ejerens areal inden for grænsen MINUS omdrift og permanent
        græs (alt referencekortet ikke dækker, fx skov/sø/bebyggelse).
        """
        if owner_geom is None or owner_geom.isEmpty():
            return 0.0, 0.0, 0.0
        total_m2 = owner_geom.area()
        omdrift_m2 = graes_m2 = 0.0
        if sats_geoms is not None:
            og = sats_geoms.get("omdrift")
            gg = sats_geoms.get("graes")
            try:
                if og is not None and not og.isEmpty():
                    inter = owner_geom.intersection(og)
                    if inter and not inter.isEmpty():
                        omdrift_m2 = inter.area()
                if gg is not None and not gg.isEmpty():
                    inter = owner_geom.intersection(gg)
                    if inter and not inter.isEmpty():
                        graes_m2 = inter.area()
            except Exception:
                pass
        # Resten = alt inden for grænsen der hverken er omdrift eller græs.
        andet_m2 = total_m2 - omdrift_m2 - graes_m2
        if andet_m2 < 0:
            andet_m2 = 0.0  # afrundings-/overlapsstøj
        return omdrift_m2 / 10000.0, graes_m2 / 10000.0, andet_m2 / 10000.0

    def _boundary_geometry(self, area_layer, parcel_layer):
        """Forening af projektgrænse-lagets geometrier i matriklernes CRS.

        Returnerer None hvis intet område-lag er givet (→ intet arealfilter).
        Geometrien transformeres om nødvendigt til parcel_layer's CRS, så
        skæring/areal beregnes korrekt.
        """
        if area_layer is None:
            return None

        geoms = []
        for feat in area_layer.getFeatures():
            g = feat.geometry()
            if g and not g.isEmpty():
                geoms.append(g)
        if not geoms:
            return None

        boundary = QgsGeometry.unaryUnion(geoms)
        if boundary is None or boundary.isEmpty():
            return None

        # Reparér evt. ugyldig geometri – en GEOS-ugyldig forening kan ellers
        # få intersection() til at returnere tomt for stort set alle ejere.
        if not boundary.isGeosValid():
            fixed = boundary.makeValid()
            if fixed is not None and not fixed.isEmpty():
                boundary = fixed

        # Transformér til matriklernes CRS hvis de afviger.
        src_crs = area_layer.crs()
        dst_crs = parcel_layer.crs()
        if src_crs.isValid() and dst_crs.isValid() and src_crs != dst_crs:
            try:
                xform = QgsCoordinateTransform(src_crs, dst_crs, self.project)
                boundary.transform(xform)
            except Exception as exc:
                _log("Kunne ikke transformere projektgrænse: {}".format(exc))
        _log("Projektgrænse: areal={:.0f} m², bbox={}".format(
            boundary.area(), boundary.boundingBox().toString(0)))
        return boundary

    @staticmethod
    def _area_within(geom, boundary):
        """Areal (m²) af geom der ligger inden for boundary."""
        try:
            clipped = geom.intersection(boundary)
            if clipped is None or clipped.isEmpty():
                return 0.0
            return clipped.area()
        except Exception:
            return 0.0

    @staticmethod
    def _collect_geometries(geoms):
        if not geoms:
            return None
        combined = geoms[0]
        for g in geoms[1:]:
            combined = combined.combine(g)
        # Sørg for multipolygon så laget er konsistent.
        if combined and not combined.isMultipart():
            combined.convertToMultiType()
        return combined

    @staticmethod
    def _to_str(value):
        return "" if value is None else str(value)

    # ----------------------------------------------- matrikellag (highlight)
    def _build_highlight_layer(self, parcel_layer, name, matrikel_field=None,
                               render_filter=None, label_filter=None,
                               with_labels=True):
        """Klon af matrikellaget med gul kant, transparent fyld.

        :param name: lagets navn (forskelligt for hoved- og oversigtskort).
        :param matrikel_field: feltnavn til matrikelnummer-etiket (eller None).
        :param render_filter: regel-filter (expression) der begrænser HVILKE
            matrikler der TEGNES. None = tegn alle (hovedkortet, hvor geometrien
            i stedet klippes til ejeren via atlas-clipping).
        :param label_filter: filter der begrænser HVILKE matrikler der får
            etiket. Bruges på hovedkortet, så kun ejerens matrikler labelles
            (clipping påvirker ikke labels).
        :param with_labels: slå etiketter til/fra (oversigtskort = fra).

        Klon i stedet for genåbning via source(): clone() bevarer data og
        provider også for MEMORY/temp-lag.
        """
        highlight = self._clone_layer(parcel_layer, name)

        # Gul kant, gennemsigtig midte.
        symbol = QgsFillSymbol.createSimple({
            "color": "0,0,0,0",                 # gennemsigtig midte
            "style": "no",                      # ingen fyld
            "outline_color": "255,210,0,255",   # gul
            "outline_width": "0.4",
        })

        if render_filter:
            # Regel-baseret renderer der kun tegner den aktuelle ejers matrikler.
            root = QgsRuleBasedRenderer.Rule(None)
            rule = QgsRuleBasedRenderer.Rule(symbol, filterExp=render_filter)
            root.appendChild(rule)
            highlight.setRenderer(QgsRuleBasedRenderer(root))
        else:
            highlight.setRenderer(QgsSingleSymbolRenderer(symbol))

        # Matrikelnummer i hver matrikel.
        if with_labels and matrikel_field:
            self._apply_matrikel_labels(highlight, matrikel_field, label_filter)
        else:
            highlight.setLabelsEnabled(False)

        highlight.triggerRepaint()
        return highlight

    @staticmethod
    def _apply_matrikel_labels(layer, matrikel_field, label_filter=None):
        """Slå etiketter til der viser matrikelnummeret midt i hver matrikel."""
        pal = QgsPalLayerSettings()
        pal.isExpression = True
        if label_filter:
            # Vis kun etiketter for den aktuelle ejers matrikler (tom ellers).
            pal.fieldName = "if({flt}, \"{f}\", '')".format(
                flt=label_filter, f=matrikel_field)
        else:
            pal.fieldName = '"{}"'.format(matrikel_field)
        # Placér etiket vandret inde i polygonen.
        try:
            pal.placement = Qgis.LabelPlacement.Horizontal
        except Exception:
            try:
                pal.placement = QgsPalLayerSettings.Horizontal
            except Exception:
                pass

        text_format = QgsTextFormat()
        text_format.setSize(8)
        text_format.setColor(QColor(0, 0, 0))
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setSize(0.8)
        buffer.setColor(QColor(255, 255, 255))
        text_format.setBuffer(buffer)
        pal.setFormat(text_format)

        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)

    # --------------------------------------------- projektgrænse (rød outline)
    def _style_area_layer(self, area_layer):
        """Stil projektgrænsen: rød kant, ingen fyld (transparent midte).

        Returnerer en separat lag-instans så brugerens eget lag ikke ændres.
        """
        styled = self._clone_layer(area_layer, "Projektgrænse")
        symbol = QgsFillSymbol.createSimple({
            "color": "0,0,0,0",               # gennemsigtig midte
            "style": "no",                    # ingen fyld
            "outline_color": "227,26,28,255",  # rød
            "outline_width": "0.6",
        })
        styled.setRenderer(QgsSingleSymbolRenderer(symbol))
        styled.triggerRepaint()
        return styled

    @staticmethod
    def _clone_layer(layer, new_name):
        """Klon et vektorlag (data + provider bevares, også for memory-lag)."""
        clone = layer.clone()
        clone.setName(new_name)
        return clone

    @staticmethod
    def _detect_matrikel_field(layer):
        """Find feltet med matrikelnummer ud fra feltnavnet (eller None)."""
        hints = ["matrikelnu", "matrikel", "matrnr", "matr_nr", "matr", "matrno"]
        names = {f.name().lower(): f.name() for f in layer.fields()}
        for hint in hints:
            if hint in names:
                return names[hint]
        for hint in hints:
            for low, original in names.items():
                if low.startswith(hint):
                    return original
        return None

    # -------------------------------------------------------- XML-tilpasning
    def _load_template_doc(self):
        try:
            with open(self.template_path, "r", encoding="utf-8") as fh:
                xml = fh.read()
        except OSError as exc:
            raise AtlasBuildError("Kunne ikke læse skabelonen:\n{}".format(exc))

        doc = QDomDocument()
        ok, err_msg, line, col = doc.setContent(xml, True)
        if not ok:
            raise AtlasBuildError(
                "Skabelonens XML kunne ikke fortolkes ({} linje {}:{}).".format(
                    err_msg, line, col
                )
            )
        return doc

    def _rewrite_template_fields(self, doc, resolved):
        """Udskift skabelonens faste feltnavne i labelText med coverage-felter."""
        replacements = []
        for ph in PLACEHOLDERS:
            new_field = resolved.get(ph.key)
            if new_field and new_field != ph.template_field:
                replacements.append((ph.template_field, new_field))
        if not replacements:
            return

        items = doc.elementsByTagName("LayoutItem")
        for i in range(items.count()):
            el = items.at(i).toElement()
            if not el.hasAttribute("labelText"):
                continue
            text = el.attribute("labelText")
            text = self._replace_field_refs(text, replacements)
            el.setAttribute("labelText", text)

    @staticmethod
    def _replace_field_refs(text, replacements):
        for old, new in replacements:
            pattern = r'"{}"'.format(re.escape(old))
            text = re.sub(pattern, '"{}"'.format(new), text)
        return text

    def _update_reference_years(self, doc):
        """Opdatér årstal-intervallet i overskriften til referencekortets år.

        Skabelonen har fx 'hektar (Reference 2017-2021)'; vi erstatter ethvert
        ÅÅÅÅ-ÅÅÅÅ med REFERENCE_YEARS (referencekortets faktiske periode).
        """
        items = doc.elementsByTagName("LayoutItem")
        for i in range(items.count()):
            el = items.at(i).toElement()
            if not el.hasAttribute("labelText"):
                continue
            text = el.attribute("labelText")
            new_text = re.sub(r"\d{4}\s*-\s*\d{4}", REFERENCE_YEARS, text)
            if new_text != text:
                el.setAttribute("labelText", new_text)

    def _remove_area_table(self, doc):
        """Fjern arealtabellen (Omdrift/Græs/Natur) og dens frame."""
        mf_uuids = []
        multiframes = doc.elementsByTagName("LayoutMultiFrame")
        to_remove = []
        for i in range(multiframes.count()):
            el = multiframes.at(i).toElement()
            if el.attribute("type") == "65649":
                mf_uuids.append(el.attribute("uuid"))
                to_remove.append(el)

        items = doc.elementsByTagName("LayoutItem")
        for i in range(items.count()):
            el = items.at(i).toElement()
            if el.attribute("type") == "65647" and \
                    el.attribute("multiFrame") in mf_uuids:
                to_remove.append(el)

        for el in to_remove:
            parent = el.parentNode()
            if not parent.isNull():
                parent.removeChild(el)

    def _rebind_area_table(self, doc, owner_layer):
        """Bind arealtabellen (multiframe) til ejer-laget i stedet for det
        hardkodede lag fra skabelonen.

        Skabelonens kolonner refererer Omdrift_ha/Græs_ha/Natur_ha, som vi nu
        har lagt på ejer-laget. Vi sætter også maxFeatures, så kun den aktuelle
        ejers række vises (atlas-filtreret).
        """
        multiframes = doc.elementsByTagName("LayoutMultiFrame")
        found = False
        for i in range(multiframes.count()):
            el = multiframes.at(i).toElement()
            if el.attribute("type") != "65649":
                continue
            el.setAttribute("vectorLayer", owner_layer.id())
            el.setAttribute("vectorLayerName", owner_layer.name())
            el.setAttribute("vectorLayerSource", owner_layer.source())
            el.setAttribute("vectorLayerProvider", owner_layer.providerType())
            # Vis kun den aktuelle atlas-ejers række. Tabellens lag er selv
            # coverage-laget, så vi filtrerer på den aktuelle atlas-feature.
            el.setAttribute("filterToAtlasIntersection", "0")
            el.setAttribute("filterFeatures", "true")
            el.setAttribute("featureFilter", "$id = @atlas_featureid")
            el.setAttribute("maxFeatures", "1")
            found = True

        # Omdøb kolonneoverskriften «Natur» → «Andet» (feltet hedder stadig
        # Natur_ha, men viser nu restarealet inden for projektgrænsen).
        columns = doc.elementsByTagName("column")
        for i in range(columns.count()):
            col = columns.at(i).toElement()
            if col.attribute("heading") == "Natur":
                col.setAttribute("heading", "Andet")

        if not found:
            _log("Arealtabel: ingen multiframe fundet i skabelonen")

    def _bind_coverage_layer(self, doc, layer):
        """Peg skabelonens Atlas mod ejer-laget i XML (renser hardkodet sti)."""
        atlas_nodes = doc.elementsByTagName("Atlas")
        for i in range(atlas_nodes.count()):
            el = atlas_nodes.at(i).toElement()
            el.setAttribute("coverageLayer", layer.id())
            el.setAttribute("coverageLayerName", layer.name())
            el.setAttribute("coverageLayerSource", layer.source())
            el.setAttribute("coverageLayerProvider", layer.providerType())

    # ----------------------------------------------------------- atlas-setup
    def _configure_atlas(self, layout, owner_layer):
        atlas = layout.atlas()
        atlas.setEnabled(True)
        atlas.setCoverageLayer(owner_layer)
        # Sidenavn = løbenummer pr. ejer.
        atlas.setPageNameExpression('"{}"'.format(COV_LOBENR))
        atlas.setSortFeatures(True)
        atlas.setSortExpression('"{}"'.format(COV_LOBENR))
        atlas.setFilterFeatures(False)

    def _configure_maps(self, layout, main_parcels, overview_parcels,
                        area_layer, background_layers):
        """Sæt hoved- og oversigtskort op.

        Lagrækkefølge i setLayers er top-først: projektgrænse → matrikler →
        brugerens eksisterende projektlag (baggrundskort).

        Hovedkort: atlas-styret extent (zoomer til aktuel ejer); matriklerne
            klippes til ejeren via atlas-clipping (uklippet lag).
        Oversigtskort: fast extent over hele projektområdet; matrikellaget er
            renderer-filtreret til kun den aktuelle ejer (ingen clipping).
        """
        main_map, overview_map = self._resolve_maps(layout)
        _log("Kort fundet: hoved={} oversigt={}".format(
            main_map.id() if main_map else None,
            overview_map.id() if overview_map else None))

        def stack(parcels):
            layers = []
            if area_layer is not None:
                layers.append(area_layer)
            layers.append(parcels)
            return layers + list(background_layers)

        if main_map is not None:
            main_map.setKeepLayerSet(True)
            main_map.setLayers(stack(main_parcels))
            main_map.setFollowVisibilityPreset(False)
            main_map.setAtlasDriven(True)
            main_map.setAtlasScalingMode(QgsLayoutItemMap.Auto)
            main_map.setAtlasMargin(0.15)
            # Klip KUN matrikellaget til den aktuelle ejers geometri.
            self._enable_atlas_clip(main_map, [main_parcels])

        if overview_map is not None:
            overview_map.setKeepLayerSet(True)
            overview_map.setLayers(stack(overview_parcels))
            overview_map.setFollowVisibilityPreset(False)
            overview_map.setAtlasDriven(False)
            # Fast extent = projektgrænsen, ellers matriklernes udstrækning.
            extent_layer = area_layer or overview_parcels
            try:
                extent = extent_layer.extent()
                if extent is not None and not extent.isEmpty():
                    extent.scale(1.05)  # lidt luft
                    overview_map.zoomToExtent(extent)
            except Exception:
                pass

    @staticmethod
    def _enable_atlas_clip(map_item, layers_to_clip):
        """Klip kortindholdet til den aktuelle atlas-feature (kun givne lag)."""
        try:
            clip = map_item.atlasClippingSettings()
            clip.setEnabled(True)
            clip.setRestrictToLayers(True)
            clip.setLayersToClip(layers_to_clip)
        except Exception:
            # Hvis API'et afviger, fortsætter vi uden clipping (matrikler vises
            # stadig, blot uden afgrænsning til ejeren).
            pass

    def _current_project_layers(self, exclude_ids=None):
        """Baggrundskort fra projektet (KUN rasterlag), top-først.

        Kortene skal kun vise baggrundskort + projektgrænse + matrikler. Derfor
        tager vi udelukkende RASTERLAG med (WMS/XYZ/ortofoto/tiles) som baggrund
        og udelader alle løse vektorlag der bare ligger i kanvas.
        Lag i exclude_ids udelades altid.
        """
        exclude_ids = exclude_ids or set()
        root = self.project.layerTreeRoot()
        layers = []
        for tree_layer in root.findLayers():
            lyr = tree_layer.layer()
            if lyr is None or lyr.id() in exclude_ids:
                continue
            if lyr.type() == QgsMapLayer.RasterLayer:
                layers.append(lyr)
        _log("Baggrundskort (rasterlag): {}".format([l.name() for l in layers]))
        return layers

    @staticmethod
    def _resolve_maps(layout):
        """Find (hovedkort, oversigtskort) i layoutet.

        Foretrækker id'erne fra skabelonen; falder ellers tilbage til at vælge
        det største kort som hovedkort og det næststørste som oversigt.
        """
        main = layout.itemById(MAIN_MAP_ID)
        overview = layout.itemById(OVERVIEW_MAP_ID)
        if isinstance(main, QgsLayoutItemMap) and \
                isinstance(overview, QgsLayoutItemMap):
            return main, overview

        maps = [i for i in layout.items() if isinstance(i, QgsLayoutItemMap)]
        if not maps:
            return None, None
        # Sortér efter areal (størst først).
        maps.sort(key=lambda m: m.rect().width() * m.rect().height(),
                  reverse=True)
        main = main if isinstance(main, QgsLayoutItemMap) else maps[0]
        if not isinstance(overview, QgsLayoutItemMap):
            overview = maps[1] if len(maps) > 1 else None
        return main, overview

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _unique_name(manager, base):
        existing = {l.name() for l in manager.layouts()}
        if base not in existing:
            return base
        n = 2
        while "{} ({})".format(base, n) in existing:
            n += 1
        return "{} ({})".format(base, n)
