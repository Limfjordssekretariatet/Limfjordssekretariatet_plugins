# -*- coding: utf-8 -*-
"""Brænd MIKE-tværprofiler ned i en terrænmodel (DHM).

Testudgave i VASP-pluginnet af værktøjet "Fra MIKE til DHM" fra
Limf_WetlandTools. Selve metoden er skrevet om efter principperne i
Klimadatastyrelsens rivertopo (https://github.com/Klimadatastyrelsen/rivertopo):

1. Profilerne forankres i deres dybeste punkt (MIKE-markør 2, ellers thalweg)
   i stedet for i bredde-midten, så kanalen ikke vandrer sidelæns.
2. Alle profiler evalueres på ét fælles offset-gitter, så der interpoleres
   mellem sammenlignelige punkter på tværs af profilerne.
3. Profilerne snappes til *alle* features i centerlinjelaget med en
   afstandsgrænse, og profiler hvis placering strider mod MIKE-stationeringen
   sorteres fra i stedet for at ødelægge interpolationen.
4. Resultatet rasteriseres direkte ind i DHM'ets eget gitter
   (``BURN_VALUE_FROM=Z``) i stedet for TIN + concave hull + warp. Dermed er
   der ingen resampling, ingen maske der lukker hen over slyngninger, og
   ingen risiko for at rammen ikke passer.

Beregningen uden QGIS-afhængigheder ligger i :mod:`.geo.mike`.
"""

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProject,
    QgsWkbTypes,
)

from .geo import mike

# Værdi i brænde-rasteret der betyder "ikke ramt af en linje".
_UNSET = 3.0e38
_UNSET_LIMIT = 1.0e30

_ANCHOR_CHOICES = [
    "Automatisk (MIKE-markør 2, ellers dybeste punkt)",
    "Dybeste punkt (thalweg)",
    "MIKE-markør 2",
    "Profilets midte (som den gamle udgave)",
]
_ANCHOR_MODES = [
    mike.ANCHOR_AUTO,
    mike.ANCHOR_THALWEG,
    mike.ANCHOR_MARKER,
    mike.ANCHOR_MIDDLE,
]


def _warn(feedback, message):
    """Advarsel der virker på både nyere og ældre QGIS-versioner."""
    if hasattr(feedback, "pushWarning"):
        feedback.pushWarning(message)
    else:
        feedback.pushInfo("ADVARSEL: %s" % message)


def _runs(valid):
    """Sammenhængende strækninger af True i et boolsk array (mindst 2 lange)."""
    flags = np.asarray(valid, dtype=bool)
    if flags.size == 0:
        return []
    change = np.diff(flags.astype(np.int8))
    starts = list(np.flatnonzero(change == 1) + 1)
    stops = list(np.flatnonzero(change == -1) + 1)
    if flags[0]:
        starts.insert(0, 0)
    if flags[-1]:
        stops.append(flags.size)
    return [(int(a), int(b)) for a, b in zip(starts, stops) if b - a >= 2]


class BraendVandloebITerraenAlgorithm(QgsProcessingAlgorithm):
    """Brænder MIKE-tværprofiler ned i DHM'et og beholder min(DHM, profil)."""

    PARAM_MIKE_TXT = "MIKE_TXT"
    PARAM_LGDID = "VASP_LGDID"
    PARAM_CENTERLINE = "CENTERLINE"
    PARAM_DHM = "DHM"
    PARAM_MAX_SNAP = "MAX_SNAP"
    PARAM_ANCHOR = "ANCHOR"
    PARAM_NORMAL_WINDOW = "NORMAL_WINDOW"
    PARAM_DENSITY = "DENSITY"
    PARAM_MAX_WIDTH = "MAX_WIDTH"
    PARAM_OUTPUT = "OUTPUT"
    PARAM_OUTPUT_LINES = "OUTPUT_LINES"

    def name(self):
        return "vasp_braend_vandloeb"

    def displayName(self):
        return "Brænd vandløb i terræn (MIKE/VASP → DHM)"

    def group(self):
        return "VASP"

    def groupId(self):
        return "vasp"

    def shortHelpString(self):
        return (
            "Placerer tværprofiler på en centerlinje og brænder dem ned i "
            "terrænmodellen. Output er min(DHM, profil).\n\n"
            "Profilerne kommer enten fra VASP eller fra en MIKE-eksport. "
            "Startes værktøjet fra VASP-dialogen, er profil og centerlinje "
            "allerede udfyldt; ellers vælges en MIKE-tekstfil og et linjelag "
            "manuelt.\n\n"
            "Profilerne forankres i deres dybeste punkt og evalueres på et "
            "fælles offset-gitter, så der interpoleres mellem sammenlignelige "
            "punkter. Resultatet rasteriseres direkte i DHM'ets gitter, så der "
            "hverken resamples eller klippes med en concave hull.\n\n"
            "Profiler der ligger længere fra centerlinjen end grænsen, eller "
            "hvis placering strider mod MIKE-stationeringen, sorteres fra og "
            "nævnes i loggen. Kør med kontrol-laget slået til for at se hvor "
            "hvert profil landede.\n\n"
            "MIKE-koordinaterne antages at være i centerlinjelagets CRS."
        )

    def createInstance(self):
        return BraendVandloebITerraenAlgorithm()

    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.PARAM_MIKE_TXT, "MIKE-eksport (tekstfil)", extension="txt",
            optional=True))

        # Udfyldes automatisk når værktøjet startes fra VASP-dialogen. Er den
        # sat, hentes tværprofilerne fra VASP i stedet for fra en MIKE-fil.
        param = QgsProcessingParameterNumber(
            self.PARAM_LGDID,
            "VASP-profil (LGDID — udfyldes fra VASP-dialogen)",
            QgsProcessingParameterNumber.Integer,
            defaultValue=0, minValue=0, optional=True)
        self.addParameter(param)

        self.addParameter(QgsProcessingParameterFeatureSource(
            self.PARAM_CENTERLINE, "Centerlinje (vandløbsmidte)",
            [QgsProcessing.TypeVectorLine]))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.PARAM_DHM, "Terrænmodel (DHM)"))

        self.addParameter(QgsProcessingParameterNumber(
            self.PARAM_MAX_SNAP,
            "Maks. afstand fra profil til centerlinje (m)",
            QgsProcessingParameterNumber.Double,
            defaultValue=50.0, minValue=0.1))

        self.addParameter(QgsProcessingParameterEnum(
            self.PARAM_ANCHOR, "Profilernes nulpunkt",
            options=_ANCHOR_CHOICES, defaultValue=0))

        param = QgsProcessingParameterNumber(
            self.PARAM_NORMAL_WINDOW,
            "Udjævning af tværsnitsretning (m)",
            QgsProcessingParameterNumber.Double,
            defaultValue=10.0, minValue=0.1)
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        param = QgsProcessingParameterNumber(
            self.PARAM_DENSITY,
            "Punktafstand (× pixelstørrelse)",
            QgsProcessingParameterNumber.Double,
            defaultValue=0.5, minValue=0.1, maxValue=2.0)
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        param = QgsProcessingParameterNumber(
            self.PARAM_MAX_WIDTH,
            "Maks. profilbredde der bruges (m, 0 = hele profilet)",
            QgsProcessingParameterNumber.Double,
            defaultValue=0.0, minValue=0.0)
        param.setFlags(param.flags()
                       | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.PARAM_OUTPUT, "Terrænmodel med vandløb"))

        sink = QgsProcessingParameterFeatureSink(
            self.PARAM_OUTPUT_LINES, "Tværsnit til kontrol",
            QgsProcessing.TypeVectorLine, optional=True,
            createByDefault=False)
        self.addParameter(sink)

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        mike_path = self.parameterAsFile(
            parameters, self.PARAM_MIKE_TXT, context)
        lgdid = self.parameterAsInt(parameters, self.PARAM_LGDID, context)
        source = self.parameterAsSource(
            parameters, self.PARAM_CENTERLINE, context)
        dhm_layer = self.parameterAsRasterLayer(
            parameters, self.PARAM_DHM, context)
        max_snap = self.parameterAsDouble(
            parameters, self.PARAM_MAX_SNAP, context)
        anchor_mode = _ANCHOR_MODES[self.parameterAsEnum(
            parameters, self.PARAM_ANCHOR, context)]
        normal_window = self.parameterAsDouble(
            parameters, self.PARAM_NORMAL_WINDOW, context)
        density = self.parameterAsDouble(
            parameters, self.PARAM_DENSITY, context)
        max_width = self.parameterAsDouble(
            parameters, self.PARAM_MAX_WIDTH, context)
        out_path = self.parameterAsOutputLayer(
            parameters, self.PARAM_OUTPUT, context)

        if not mike_path and not lgdid:
            raise QgsProcessingException(
                "Vælg enten en MIKE-tekstfil eller start værktøjet fra "
                "VASP-dialogen, så profilerne hentes fra databasen.")
        if source is None:
            raise QgsProcessingException("Centerlinjelaget kunne ikke læses.")
        if dhm_layer is None or not dhm_layer.isValid():
            raise QgsProcessingException("DHM-laget kunne ikke læses.")
        if not out_path:
            raise QgsProcessingException("Ingen output-fil valgt.")

        dhm_ds, gt, dhm_band = self._open_dhm(dhm_layer)
        pixel = min(abs(gt[1]), abs(gt[5]))
        step = max(pixel * density, 1e-3)

        # --- 1) profiler ------------------------------------------------
        if lgdid:
            profiles, warnings = self._load_vasp_profiles(lgdid, feedback)
            kilde = "VASP-profil %d" % lgdid
        else:
            profiles, warnings = mike.parse_mike_file(mike_path)
            kilde = "MIKE-filen"
        for message in warnings:
            _warn(feedback, message)
        if len(profiles) < 2:
            raise QgsProcessingException(
                "Der blev kun læst %d brugbare tværprofiler fra %s "
                "(der skal mindst være 2)." % (len(profiles), kilde))
        feedback.pushInfo("Læste %d tværprofiler fra %s."
                          % (len(profiles), kilde))

        kinds = {}
        for profile in profiles:
            kind = profile.set_anchor(anchor_mode)
            kinds[kind] = kinds.get(kind, 0) + 1
        feedback.pushInfo("Nulpunkt: %s." % ", ".join(
            "%s: %d" % (kind, count) for kind, count in sorted(kinds.items())))

        # --- 2) centerlinje --------------------------------------------
        parts = self._read_centerline(source, dhm_layer, feedback)
        if not parts:
            raise QgsProcessingException(
                "Centerlinjelaget indeholder ingen linjer med mindst 2 punkter.")
        feedback.pushInfo("Centerlinje: %d linjestykke(r), i alt %.0f m."
                          % (len(parts), sum(p[2][-1] for p in parts)))

        transform = self._transform(source, dhm_layer)
        kept = self._snap_profiles(profiles, parts, transform, max_snap,
                                   feedback)
        if len(kept) < 2:
            raise QgsProcessingException(
                "Færre end 2 profiler kunne placeres på centerlinjen. Tjek at "
                "MIKE-koordinaterne er i samme koordinatsystem som "
                "centerlinjen, og at afstandsgrænsen ikke er for lille.")

        part_index, kept = self._pick_part(kept, feedback)
        kept = self._check_order(kept, feedback)
        if len(kept) < 2:
            raise QgsProcessingException(
                "Færre end 2 profiler var indbyrdes konsistente. Se "
                "advarslerne ovenfor.")

        name, points, cum = parts[part_index]
        reverse = self._orientation(kept)
        if reverse:
            feedback.pushInfo(
                "Centerlinjen er digitaliseret modsat stationeringen — "
                "vender den, så venstre/højre passer med MIKE-profilerne.")
            points = points[::-1].copy()
            cum = mike.cumulative_length(points)
            total = cum[-1]
            for profile in kept:
                profile.chainage = total - profile.chainage
        kept.sort(key=lambda p: p.chainage)
        kept = self._drop_duplicates(kept, feedback)
        if len(kept) < 2:
            raise QgsProcessingException(
                "Færre end 2 profiler tilbage efter frasortering af dubletter.")

        feedback.pushInfo(
            "Bruger %d profiler over %.1f m af '%s'."
            % (len(kept), kept[-1].chainage - kept[0].chainage, name))
        self._check_datum(kept, points, cum, dhm_band, gt, dhm_ds, feedback)
        feedback.setProgress(15)

        # --- 3) korridoren ---------------------------------------------
        offsets = mike.offset_grid(kept, step, max_width)
        chainages = np.array([p.chainage for p in kept])
        n_stations = int(np.ceil(
            (chainages[-1] - chainages[0]) / step)) + 1
        stations = np.linspace(chainages[0], chainages[-1], n_stations)

        total_points = n_stations * len(offsets)
        if total_points > 40_000_000:
            raise QgsProcessingException(
                "Korridoren ville blive %d punkter. Sæt punktafstanden op "
                "eller begræns profilbredden." % total_points)
        feedback.pushInfo(
            "Korridor: %d stationer × %d offsets (%.2f m mellem punkterne, "
            "bredde %.1f m)."
            % (n_stations, len(offsets), step, offsets[-1] - offsets[0]))

        matrix = mike.profile_matrix(kept, offsets)
        z_grid = mike.interpolate_along(chainages, matrix, stations)
        positions = mike.positions_at(points, cum, stations)
        normals = mike.normals_at(points, cum, stations, normal_window)

        x_grid = positions[:, 0][:, None] + offsets[None, :] * normals[:, 0][:, None]
        y_grid = positions[:, 1][:, None] + offsets[None, :] * normals[:, 1][:, None]
        valid = ~np.isnan(z_grid)
        if not np.any(valid):
            raise QgsProcessingException(
                "Ingen gyldige koter i korridoren — tjek MIKE-filen.")
        feedback.setProgress(25)

        # --- 4) linjenet der kan rasteriseres --------------------------
        lines_ds, lines_layer = self._build_mesh(
            x_grid, y_grid, z_grid, valid, offsets, dhm_ds.GetProjection(),
            feedback)
        if feedback.isCanceled():
            return {}
        feedback.setProgress(55)

        # --- 5) brænd ned i DHM'ets eget gitter ------------------------
        changed = self._burn(dhm_ds, dhm_band, gt, lines_layer,
                             x_grid, y_grid, valid, out_path, feedback)
        lines_ds = None
        dhm_ds = None

        results = {self.PARAM_OUTPUT: out_path}
        feedback.pushInfo("Ændrede %d pixels i terrænmodellen." % changed)

        # --- 6) kontrol-lag --------------------------------------------
        sink_id = self._write_control_lines(
            parameters, context, kept, points, cum, offsets, matrix,
            normal_window, dhm_layer)
        if sink_id is not None:
            results[self.PARAM_OUTPUT_LINES] = sink_id

        feedback.pushInfo("Færdig: %s" % out_path)
        return results

    # ------------------------------------------------------------------
    # Delfunktioner
    # ------------------------------------------------------------------
    def _load_vasp_profiles(self, lgdid, feedback):
        """Hent tværprofilerne for ét VASP-profil-datalag fra datafilen."""
        from . import dbaccess
        try:
            sections = dbaccess.read_cross_sections(lgdid)
            params = dbaccess.read_cross_section_params(lgdid)
        except dbaccess.VaspDbError as exc:
            raise QgsProcessingException(str(exc))

        profiles, warnings = mike.profiles_from_vasp(sections, params)
        feedback.pushInfo(
            "VASP-profil %d: %d opmålte tværsnit, %d parametriske rækker."
            % (lgdid, len(sections), len(params)))
        profiles, extra = self._fill_missing_coords(lgdid, profiles)
        return profiles, warnings + extra

    def _fill_missing_coords(self, lgdid, profiles):
        """Giv koordinater til de tværsnit der kun har en stationering.

        Godt 60 % af de parametriske tværsnit i VASP har ingen KOORDX/KOORDY.
        De kan stadig placeres, fordi den geokodede vandløbslinje bærer
        stationering i hvert knækpunkt — så positionen kan slås op direkte i
        stationeringen i stedet for at blive snappet.
        """
        mangler = [p for p in profiles
                   if p.base_x is None or p.base_y is None]
        if not mangler:
            return profiles, []

        from . import dbaccess
        prof = dbaccess.tvp_profile(lgdid)
        gid = prof.get("geocodegdsid") if prof else None
        linje = dbaccess.read_geocoded_line(gid) if gid else []

        stationer, xs, ys = [], [], []
        sidste = None
        for punkt in sorted(
                (p for p in linje if p["station"] is not None),
                key=lambda p: p["station"]):
            if sidste is not None and punkt["station"] <= sidste:
                continue
            stationer.append(punkt["station"])
            xs.append(punkt["x"])
            ys.append(punkt["y"])
            sidste = punkt["station"]

        if len(stationer) < 2:
            uden = {id(p) for p in mangler}
            beholdt = [p for p in profiles if id(p) not in uden]
            return beholdt, [
                "%d tværsnit har hverken koordinater eller en geokodet "
                "vandløbslinje at slå stationeringen op i, og springes over."
                % len(mangler)]

        stationer = np.array(stationer)
        xs = np.array(xs)
        ys = np.array(ys)
        udenfor = 0
        beholdt = []
        for profile in profiles:
            if profile.base_x is not None and profile.base_y is not None:
                beholdt.append(profile)
                continue
            if (profile.station is None
                    or profile.station < stationer[0]
                    or profile.station > stationer[-1]):
                udenfor += 1
                continue
            profile.base_x = float(np.interp(profile.station, stationer, xs))
            profile.base_y = float(np.interp(profile.station, stationer, ys))
            beholdt.append(profile)

        besked = ["%d tværsnit uden koordinater blev placeret ud fra deres "
                  "stationering på den geokodede vandløbslinje."
                  % (len(mangler) - udenfor)]
        if udenfor:
            besked.append(
                "%d tværsnit ligger uden for linjens stationering (%.0f–%.0f m) "
                "og springes over." % (udenfor, stationer[0], stationer[-1]))
        return beholdt, besked

    def _open_dhm(self, dhm_layer):
        """Åbn DHM'et med GDAL og kontrollér at gitteret er nord-op."""
        path = dhm_layer.source().split("|", 1)[0]
        dataset = gdal.Open(path, gdal.GA_ReadOnly)
        if dataset is None:
            raise QgsProcessingException(
                "Kunne ikke åbne DHM'et med GDAL:\n%s" % path)
        gt = dataset.GetGeoTransform()
        if gt is None or gt[2] != 0.0 or gt[4] != 0.0:
            raise QgsProcessingException(
                "DHM'et er roteret. Værktøjet kræver et nord-vendt gitter.")
        band = dataset.GetRasterBand(1)
        if band is None:
            raise QgsProcessingException("DHM'et har ingen læsbar kanal.")
        return dataset, gt, band

    def _transform(self, source, dhm_layer):
        """Transformation fra centerlinjens CRS til DHM'ets, eller None."""
        src_crs = source.sourceCrs()
        dst_crs = dhm_layer.crs()
        if not src_crs.isValid() or not dst_crs.isValid():
            return None
        if src_crs == dst_crs:
            return None
        return QgsCoordinateTransform(
            src_crs, dst_crs, QgsProject.instance())

    def _read_centerline(self, source, dhm_layer, feedback):
        """Alle linjestykker i laget som (navn, punkter, kumuleret længde).

        Både enkelt- og flerdelte geometrier tages med — den gamle udgave
        brugte kun lagets første feature, hvilket sendte alle øvrige profiler
        hen til den forkerte linje.
        """
        transform = self._transform(source, dhm_layer)
        parts = []
        for feature in source.getFeatures():
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            if transform is not None:
                geometry = QgsGeometry(geometry)
                geometry.transform(transform)
            if geometry.isMultipart():
                polylines = geometry.asMultiPolyline()
            else:
                polylines = [geometry.asPolyline()]
            for i, polyline in enumerate(polylines):
                if len(polyline) < 2:
                    continue
                points = np.array([[p.x(), p.y()] for p in polyline])
                label = "fid %s" % feature.id()
                if len(polyline) > 1 and len(polylines) > 1:
                    label += " (del %d)" % (i + 1)
                parts.append((label, points, mike.cumulative_length(points)))
        return parts

    def _snap_profiles(self, profiles, parts, transform, max_snap, feedback):
        """Placér profilerne på centerlinjen og frasortér dem der ligger langt væk."""
        kept = []
        too_far = []
        for profile in profiles:
            x, y = profile.base_x, profile.base_y
            if transform is not None:
                point = transform.transform(x, y)
                x, y = point.x(), point.y()
            snap = mike.snap_point(x, y, parts)
            if snap is None:
                continue
            if snap.dist > max_snap:
                too_far.append((profile, snap.dist))
                continue
            profile.chainage = snap.chainage
            profile.snap_dist = snap.dist
            profile.snap_offset = snap.offset
            profile.part = snap.part
            kept.append(profile)

        if too_far:
            _warn(feedback,
                  "%d profiler ligger længere end %.1f m fra centerlinjen og "
                  "springes over (nærmeste: %.1f m, fjerneste: %.1f m). "
                  "Fx: %s." % (
                      len(too_far), max_snap,
                      min(d for _, d in too_far), max(d for _, d in too_far),
                      ", ".join("'%s' (%.0f m)" % (p.name, d)
                                for p, d in too_far[:5])))
        if kept:
            dists = np.array([p.snap_dist for p in kept])
            feedback.pushInfo(
                "Placerede %d profiler (afstand til linjen: median %.2f m, "
                "største %.2f m)."
                % (len(kept), float(np.median(dists)), float(dists.max())))
        return kept

    def _pick_part(self, kept, feedback):
        """Vælg det linjestykke flest profiler hører til.

        Profiler på andre stykker kan ikke stationeres i samme forløb, så de
        frasorteres med en tydelig besked i stedet for at blive blandet ind.
        """
        counts = {}
        for profile in kept:
            counts[profile.part] = counts.get(profile.part, 0) + 1
        best = max(counts, key=lambda key: counts[key])
        if len(counts) > 1:
            _warn(feedback,
                  "Profilerne fordeler sig på %d linjestykker. Bruger det med "
                  "flest profiler (%d af %d). Kør værktøjet én gang pr. "
                  "strækning, eller vælg strækningen i laget først."
                  % (len(counts), counts[best], len(kept)))
        return best, [p for p in kept if p.part == best]

    def _check_order(self, kept, feedback):
        """Frasortér profiler hvis placering strider mod MIKE-stationeringen."""
        if any(p.station is None for p in kept):
            _warn(feedback,
                  "Ikke alle profiler har en stationering i MIKE-filen — "
                  "rækkefølgen kan ikke kontrolleres.")
            return kept
        stations = np.array([p.station for p in kept])
        chainages = np.array([p.chainage for p in kept])
        keep_idx, _ = mike.consistent_order(stations, chainages)
        dropped = [p for i, p in enumerate(kept) if i not in set(keep_idx)]
        if dropped:
            _warn(feedback,
                  "%d profiler blev placeret ude af rækkefølge i forhold til "
                  "MIKE-stationeringen og springes over — typisk et fejlsnap "
                  "til en naboslynge. Fx: %s."
                  % (len(dropped),
                     ", ".join("'%s' (station %.1f, %.1f m fra linjen)"
                               % (p.name, p.station, p.snap_dist)
                               for p in dropped[:5])))
        return [kept[i] for i in keep_idx]

    def _orientation(self, kept):
        """True hvis centerlinjen er digitaliseret modsat stationeringen."""
        if any(p.station is None for p in kept) or len(kept) < 2:
            return False
        stations = np.array([p.station for p in kept])
        chainages = np.array([p.chainage for p in kept])
        order = np.argsort(stations)
        return bool(chainages[order][-1] < chainages[order][0])

    def _drop_duplicates(self, kept, feedback):
        """Fjern profiler der lander samme sted på linjen."""
        out = [kept[0]]
        dropped = 0
        for profile in kept[1:]:
            if profile.chainage - out[-1].chainage < 1e-6:
                dropped += 1
                continue
            out.append(profile)
        if dropped:
            _warn(feedback,
                  "%d profiler lå samme sted på centerlinjen og blev "
                  "frasorteret." % dropped)
        return out

    def _check_datum(self, kept, points, cum, band, gt, dataset, feedback):
        """Sammenlign profilernes bundkote med DHM'et samme sted.

        En bundkote der ligger over terrænet, eller absurd langt under, peger
        på forkert datum eller på at profilerne hører til et andet vandløb.
        Det er en advarsel, ikke en fejl — beregningen kører videre.
        """
        chainages = np.array([p.chainage for p in kept])
        positions = mike.positions_at(points, cum, chainages)
        above, deep = [], []
        for profile, (x, y) in zip(kept, positions):
            col = int((x - gt[0]) / gt[1])
            row = int((y - gt[3]) / gt[5])
            if not (0 <= col < dataset.RasterXSize
                    and 0 <= row < dataset.RasterYSize):
                continue
            window = band.ReadAsArray(col, row, 1, 1)
            if window is None:
                continue
            terrain = float(window[0, 0])
            nodata = band.GetNoDataValue()
            if np.isnan(terrain) or (nodata is not None
                                     and terrain == nodata):
                continue
            bottom = float(profile.z.min())
            if bottom > terrain + 0.5:
                above.append((profile, bottom - terrain))
            elif bottom < terrain - 15.0:
                deep.append((profile, terrain - bottom))
        if above:
            _warn(feedback,
                  "%d profilers bundkote ligger over terrænet (op til %.1f m) "
                  "— de sænker derfor ikke terrænet. Tjek datum/koordinatsystem. "
                  "Fx: %s."
                  % (len(above), max(d for _, d in above),
                     ", ".join("'%s'" % p.name for p, _ in above[:5])))
        if deep:
            _warn(feedback,
                  "%d profilers bundkote ligger mere end 15 m under terrænet "
                  "(op til %.1f m). Fx: %s."
                  % (len(deep), max(d for _, d in deep),
                     ", ".join("'%s'" % p.name for p, _ in deep[:5])))

    def _ogr_memory_driver(self):
        """OGR's hukommelses-driver. Hed 'Memory' indtil GDAL 3.11, 'MEM' efter."""
        for name in ("MEM", "Memory"):
            driver = ogr.GetDriverByName(name)
            if driver is not None:
                try:
                    probe = driver.CreateDataSource("probe")
                except Exception:
                    continue
                if probe is not None:
                    return driver
        raise QgsProcessingException(
            "GDAL har ingen hukommelses-driver til vektordata.")

    def _build_mesh(self, x_grid, y_grid, z_grid, valid, offsets, srs_wkt,
                    feedback):
        """Byg et net af 3D-linjer på tværs og på langs af korridoren.

        Både tvær- og længdelinjer tegnes, så rasteriseringen med
        ``ALL_TOUCHED`` dækker hver eneste pixel i korridoren. Koten
        interpoleres lineært langs hvert linjestykke af GDAL selv.

        Rækkefølgen betyder noget: rammer to linjer samme pixel, vinder den
        sidst tegnede. Længdelinjerne lægges derfor yderst-først, så linjen i
        vandløbets bund tegnes til sidst og ikke bliver overskrevet af en
        nabolinje der ligger en anelse højere oppe ad brinken.
        """
        dataset = self._ogr_memory_driver().CreateDataSource("burn")
        srs = None
        if srs_wkt:
            srs = osr.SpatialReference()
            if srs.ImportFromWkt(srs_wkt) != 0:
                srs = None
        layer = dataset.CreateLayer("mesh", srs=srs,
                                    geom_type=ogr.wkbLineString25D)

        n_stations, n_offsets = z_grid.shape
        total = n_stations + n_offsets
        done = 0
        for i in range(n_stations):
            self._add_line(layer, x_grid[i], y_grid[i], z_grid[i], valid[i])
            done += 1
            if done % 250 == 0:
                if feedback.isCanceled():
                    return dataset, layer
                feedback.setProgress(25 + 30.0 * done / total)
        for j in sorted(range(n_offsets), key=lambda k: -abs(offsets[k])):
            self._add_line(layer, x_grid[:, j], y_grid[:, j], z_grid[:, j],
                           valid[:, j])
            done += 1
            if done % 250 == 0:
                if feedback.isCanceled():
                    return dataset, layer
                feedback.setProgress(25 + 30.0 * done / total)

        feedback.pushInfo("Byggede %d linjer til rasterisering."
                          % layer.GetFeatureCount())
        return dataset, layer

    def _add_line(self, layer, xs, ys, zs, valid):
        """Tilføj én række/kolonne som 3D-linjer, brudt ved manglende koter."""
        definition = layer.GetLayerDefn()
        for start, stop in _runs(valid):
            geometry = ogr.Geometry(ogr.wkbLineString25D)
            for k in range(start, stop):
                geometry.AddPoint(float(xs[k]), float(ys[k]), float(zs[k]))
            feature = ogr.Feature(definition)
            feature.SetGeometry(geometry)
            layer.CreateFeature(feature)

    def _burn(self, dhm_ds, dhm_band, gt, lines_layer, x_grid, y_grid, valid,
              out_path, feedback):
        """Rasterisér linjenettet i DHM'ets gitter og skriv min(DHM, profil).

        Der arbejdes kun i det vindue korridoren dækker, og outputtet skrives
        blokvis, så et stort DHM ikke skal ligge i hukommelsen på én gang.
        """
        x_valid = x_grid[valid]
        y_valid = y_grid[valid]
        col_min = int(np.floor((x_valid.min() - gt[0]) / gt[1])) - 2
        col_max = int(np.ceil((x_valid.max() - gt[0]) / gt[1])) + 2
        row_min = int(np.floor((y_valid.max() - gt[3]) / gt[5])) - 2
        row_max = int(np.ceil((y_valid.min() - gt[3]) / gt[5])) + 2

        col_min = max(col_min, 0)
        row_min = max(row_min, 0)
        col_max = min(col_max, dhm_ds.RasterXSize)
        row_max = min(row_max, dhm_ds.RasterYSize)
        win_w = col_max - col_min
        win_h = row_max - row_min
        if win_w <= 0 or win_h <= 0:
            raise QgsProcessingException(
                "Vandløbet ligger uden for terrænmodellens udstrækning.")

        win_gt = (gt[0] + col_min * gt[1], gt[1], 0.0,
                  gt[3] + row_min * gt[5], 0.0, gt[5])
        mem = gdal.GetDriverByName("MEM").Create(
            "burn", win_w, win_h, 1, gdal.GDT_Float32)
        if mem is None:
            raise QgsProcessingException(
                "Kunne ikke oprette arbejds-rasteret i hukommelsen.")
        mem.SetGeoTransform(win_gt)
        mem.SetProjection(dhm_ds.GetProjection())
        mem_band = mem.GetRasterBand(1)
        mem_band.Fill(_UNSET)

        feedback.pushInfo("Rasteriserer i DHM'ets gitter (%d × %d pixels)."
                          % (win_w, win_h))
        # burn_values=[0] er nødvendig: uden den lægger GDAL 255 til z-værdien.
        error = gdal.RasterizeLayer(
            mem, [1], lines_layer, burn_values=[0],
            options=["BURN_VALUE_FROM=Z", "ALL_TOUCHED=TRUE"])
        if error not in (0, None):
            raise QgsProcessingException(
                "Rasteriseringen af tværsnittene fejlede (GDAL-kode %s)."
                % error)

        burned = mem_band.ReadAsArray()
        mem = None
        if burned is None:
            raise QgsProcessingException("Kunne ikke læse det brændte raster.")
        burned_ok = np.isfinite(burned) & (burned < _UNSET_LIMIT)
        feedback.setProgress(65)

        nodata = dhm_band.GetNoDataValue()
        out_nodata = -9999.0 if nodata is None else float(nodata)
        driver = gdal.GetDriverByName("GTiff")
        out_ds = driver.Create(
            out_path, dhm_ds.RasterXSize, dhm_ds.RasterYSize, 1,
            gdal.GDT_Float32,
            options=["COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES",
                     "BIGTIFF=IF_SAFER"])
        if out_ds is None:
            raise QgsProcessingException(
                "Kunne ikke oprette output-filen:\n%s" % out_path)
        out_ds.SetGeoTransform(gt)
        out_ds.SetProjection(dhm_ds.GetProjection())
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(out_nodata)

        chunk = 512
        changed = 0
        for row0 in range(0, dhm_ds.RasterYSize, chunk):
            if feedback.isCanceled():
                out_ds = None
                return changed
            rows = min(chunk, dhm_ds.RasterYSize - row0)
            block = dhm_band.ReadAsArray(0, row0, dhm_ds.RasterXSize, rows)
            if block is None:
                raise QgsProcessingException(
                    "Kunne ikke læse terrænmodellen ved række %d." % row0)
            block = block.astype(np.float32)
            if nodata is None:
                block_nd = np.isnan(block)
            elif np.isnan(nodata):
                block_nd = np.isnan(block)
            else:
                block_nd = np.isnan(block) | (block == nodata)
            block[block_nd] = out_nodata

            first = max(row0, row_min)
            last = min(row0 + rows, row_min + win_h)
            if last > first:
                view = block[first - row0:last - row0, col_min:col_max]
                nd_view = block_nd[first - row0:last - row0, col_min:col_max]
                sub = burned[first - row_min:last - row_min, :]
                ok = burned_ok[first - row_min:last - row_min, :]
                # Almindelig nedbrænding: behold den laveste kote.
                lower = ok & ~nd_view & (sub < view)
                view[lower] = sub[lower]
                # Huller i DHM'et (fx vandflader) fyldes med profilets kote.
                holes = ok & nd_view
                view[holes] = sub[holes]
                changed += int(lower.sum() + holes.sum())

            out_band.WriteArray(block, 0, row0)
            feedback.setProgress(
                65 + 30.0 * (row0 + rows) / dhm_ds.RasterYSize)

        out_band.FlushCache()
        out_ds = None
        return changed

    def _write_control_lines(self, parameters, context, kept, points, cum,
                             offsets, matrix, normal_window, dhm_layer):
        """Skriv ét tværsnit pr. brugt profil, så placeringen kan efterses."""
        fields = QgsFields()
        fields.append(QgsField("navn", QVariant.String))
        fields.append(QgsField("station", QVariant.Double))
        fields.append(QgsField("kaede_m", QVariant.Double))
        fields.append(QgsField("afstand_m", QVariant.Double))
        fields.append(QgsField("nulpunkt", QVariant.String))
        fields.append(QgsField("bundkote", QVariant.Double))

        sink, sink_id = self.parameterAsSink(
            parameters, self.PARAM_OUTPUT_LINES, context, fields,
            QgsWkbTypes.LineStringZ, dhm_layer.crs())
        if sink is None:
            return None

        chainages = np.array([p.chainage for p in kept])
        positions = mike.positions_at(points, cum, chainages)
        normals = mike.normals_at(points, cum, chainages, normal_window)

        for i, profile in enumerate(kept):
            zs = matrix[i]
            vertices = []
            for j, offset in enumerate(offsets):
                if np.isnan(zs[j]):
                    continue
                vertices.append(QgsPoint(
                    float(positions[i, 0] + offset * normals[i, 0]),
                    float(positions[i, 1] + offset * normals[i, 1]),
                    float(zs[j])))
            if len(vertices) < 2:
                continue
            feature = QgsFeature(fields)
            feature.setGeometry(QgsGeometry.fromPolyline(vertices))
            feature.setAttributes([
                profile.name,
                float(profile.station) if profile.station is not None else None,
                float(profile.chainage),
                float(profile.snap_dist),
                profile.anchor_kind,
                float(profile.z.min()),
            ])
            sink.addFeature(feature, QgsFeatureSink.FastInsert)
        return sink_id
