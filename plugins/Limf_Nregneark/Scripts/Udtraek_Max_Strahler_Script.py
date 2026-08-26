import math

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSink,
    QgsFeatureSink,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsProcessingException,
)


def _extend_points(pts, dist):
    if len(pts) < 2:
        return list(pts)

    p0, p1 = pts[0], pts[1]
    dx, dy = p0.x() - p1.x(), p0.y() - p1.y()
    length = math.hypot(dx, dy)
    if length > 0:
        new_start = QgsPointXY(p0.x() + dx / length * dist,
                               p0.y() + dy / length * dist)
    else:
        new_start = QgsPointXY(p0.x(), p0.y())

    pn, pnm1 = pts[-1], pts[-2]
    dx, dy = pn.x() - pnm1.x(), pn.y() - pnm1.y()
    length = math.hypot(dx, dy)
    if length > 0:
        new_end = QgsPointXY(pn.x() + dx / length * dist,
                             pn.y() + dy / length * dist)
    else:
        new_end = QgsPointXY(pn.x(), pn.y())

    return [new_start] + list(pts) + [new_end]


def _extend_geom(geom, dist):
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
        return QgsGeometry.fromMultiPolylineXY(
            [_extend_points(p, dist) for p in parts if p]
        )
    return QgsGeometry.fromPolylineXY(_extend_points(geom.asPolyline(), dist))


def _collect_points(geom):
    pts = []
    if geom.isEmpty():
        return pts
    if geom.type() == QgsWkbTypes.PointGeometry:
        if geom.isMultipart():
            pts.extend(geom.asMultiPoint())
        else:
            pts.append(geom.asPoint())
    elif geom.wkbType() == QgsWkbTypes.GeometryCollection or geom.isMultipart():
        try:
            for part in geom.asGeometryCollection():
                pts.extend(_collect_points(part))
        except Exception:
            pass
    return pts


def _intersection_points(geom, boundary_geom):
    inter = geom.intersection(boundary_geom)
    return _collect_points(inter)


def _endpoints(geom):
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
        ep = []
        for p in parts:
            if p:
                ep.append(p[0])
                ep.append(p[-1])
        return ep
    pts = geom.asPolyline()
    if not pts:
        return []
    return [pts[0], pts[-1]]


def _near(p1, p2, tol):
    return math.hypot(p1.x() - p2.x(), p1.y() - p2.y()) <= tol


def _neighbor_vertex(geom, endpoint, tol):
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
    else:
        parts = [geom.asPolyline()]
    for pts in parts:
        if len(pts) < 2:
            continue
        if _near(pts[0], endpoint, tol):
            return pts[1]
        if _near(pts[-1], endpoint, tol):
            return pts[-2]
    return None


def _closest_vertex_neighbors(geom, point, tol):
    """Returnerer (prev, next, distance) for det vertex på geom tættest på point.

    prev og next er de tilstødende vertices (kan være None ved endepunkter).
    Returnerer None hvis intet vertex er inden for tol.
    """
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
    else:
        parts = [geom.asPolyline()]

    best = None  # (dist, part_idx, vert_idx)
    for pi, pts in enumerate(parts):
        for vi, v in enumerate(pts):
            d = math.hypot(v.x() - point.x(), v.y() - point.y())
            if best is None or d < best[0]:
                best = (d, pi, vi, pts)

    if best is None or best[0] > tol:
        return None

    dist, _pi, vi, pts = best
    prev_v = pts[vi - 1] if vi > 0 else None
    next_v = pts[vi + 1] if vi < len(pts) - 1 else None
    return prev_v, next_v, dist


def _angular_diff(a1, a2):
    d = a1 - a2
    while d > math.pi:
        d -= 2 * math.pi
    while d < -math.pi:
        d += 2 * math.pi
    return abs(d)


def _polygon_boundary(poly_features):
    rings = []
    for pf in poly_features:
        g = pf.geometry()
        if not g or g.isEmpty():
            continue
        if g.isMultipart():
            for poly in g.asMultiPolygon():
                rings.extend(poly)
        else:
            rings.extend(g.asPolygon())
    if not rings:
        return QgsGeometry()
    return QgsGeometry.fromMultiPolylineXY(rings)


class UdtraekMaxStrahler(QgsProcessingAlgorithm):

    INPUT          = 'INPUT'
    PROJEKTOMRAADE = 'PROJEKTOMRAADE'
    ORDER_FELT     = 'ORDER_FELT'
    EXTEND_DIST    = 'EXTEND_DIST'
    TOLERANCE      = 'TOLERANCE'
    OUTPUT         = 'OUTPUT'

    def name(self):
        return 'udtraek_max_strahler'

    def displayName(self):
        return 'Udtraek max Strahler (med fallback til næststørste)'

    def group(self):
        return 'Vaadomraade'

    def groupId(self):
        return 'vaadomraade'

    def createInstance(self):
        return UdtraekMaxStrahler()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT,
            'Linjer (klippet channel network)',
            types=[QgsProcessing.TypeVectorLine]
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.PROJEKTOMRAADE,
            'Projektområde',
            types=[QgsProcessing.TypeVectorPolygon]
        ))
        self.addParameter(QgsProcessingParameterString(
            self.ORDER_FELT,
            'Feltnavn for Strahler-order',
            defaultValue='ORDER'
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.EXTEND_DIST,
            'Forlængelse ved test af grænseskæring (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=5.0
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.TOLERANCE,
            'Knudetolerance (m)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=0.01
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, 'Udvalgte linjer'
        ))

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        input_lag   = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        poly_lag    = self.parameterAsVectorLayer(parameters, self.PROJEKTOMRAADE, context)
        order_felt  = self.parameterAsString(parameters, self.ORDER_FELT, context) or 'ORDER'
        extend_dist = self.parameterAsDouble(parameters, self.EXTEND_DIST, context)
        tolerance   = self.parameterAsDouble(parameters, self.TOLERANCE, context)

        features = list(input_lag.getFeatures())
        if not features:
            raise QgsProcessingException('Inputlaget er tomt.')

        felt_navne = [f.name() for f in input_lag.fields()]
        if order_felt not in felt_navne:
            raise QgsProcessingException(
                f'Feltet "{order_felt}" findes ikke. Tilgængelige: {", ".join(felt_navne)}'
            )

        orders = sorted(
            {f[order_felt] for f in features if f[order_felt] is not None},
            reverse=True
        )
        if not orders:
            raise QgsProcessingException(f'Feltet "{order_felt}" har ingen værdier.')

        max_order  = orders[0]
        second_max = orders[1] if len(orders) > 1 else None

        max_feats = [f for f in features if f[order_felt] == max_order]
        feedback.pushInfo(f'Max Strahler-order: {max_order} ({len(max_feats)} features)')

        poly_feats = list(poly_lag.getFeatures())
        if not poly_feats:
            raise QgsProcessingException('Projektområde-laget er tomt.')
        boundary_geom = _polygon_boundary(poly_feats)
        if boundary_geom.isEmpty():
            raise QgsProcessingException('Projektområde har ingen gyldig grænse.')

        inter_pts = []
        for f in max_feats:
            eg = _extend_geom(f.geometry(), extend_dist)
            inter_pts.extend(_intersection_points(eg, boundary_geom))
        n_inter = len(inter_pts)
        feedback.pushInfo(
            f'Skæringer mellem forlængede max-linjer og projektgrænse: {n_inter}'
        )

        selected = list(max_feats)

        if n_inter >= 2:
            feedback.pushInfo('≥ 2 skæringer fundet – bruger kun max-linjer.')
        elif second_max is None:
            raise QgsProcessingException(
                f'Kun {n_inter} skæring mellem max-orden ({max_order}) og projektgrænsen, '
                f'og der findes ingen næststørste orden i Channel Networks. '
                f'Modellen kræver mindst 2 skæringspunkter for at beregne både opstrøms '
                f'og nedstrøms opland.'
            )
        else:
            feedback.pushInfo(
                f'Kun {n_inter} skæring fra orden {max_order} – itererer ned gennem '
                f'lavere ordener for at finde flere skæringer.'
            )

            # Beregn max-orden's flow-retning: vektor fra grænseindgangen
            # ind i polygonet (langs den klippede linjes forløb).
            if not inter_pts:
                raise QgsProcessingException(
                    'Max-orden har 0 grænseskæringer — kan ikke bestemme flow-retning.'
                )
            max_entry = inter_pts[0]

            flow_dirs = []
            for f in max_feats:
                g = f.geometry()
                if g.isMultipart():
                    parts = g.asMultiPolyline()
                else:
                    parts = [g.asPolyline()]
                for pts in parts:
                    if len(pts) < 2:
                        continue
                    p0, pn = pts[0], pts[-1]
                    p0_grænse = boundary_geom.distance(QgsGeometry.fromPointXY(p0)) <= tolerance
                    pn_grænse = boundary_geom.distance(QgsGeometry.fromPointXY(pn)) <= tolerance
                    if p0_grænse and not pn_grænse:
                        flow_dirs.append(math.atan2(pn.y() - p0.y(), pn.x() - p0.x()))
                    elif pn_grænse and not p0_grænse:
                        flow_dirs.append(math.atan2(p0.y() - pn.y(), p0.x() - pn.x()))

            if not flow_dirs:
                raise QgsProcessingException(
                    'Kunne ikke bestemme max-linjens flow-retning — ingen klippet '
                    'linje har præcis ét endepunkt på grænsen.'
                )

            ax = sum(math.cos(a) for a in flow_dirs) / len(flow_dirs)
            ay = sum(math.sin(a) for a in flow_dirs) / len(flow_dirs)
            flow_angle = math.atan2(ay, ax)
            feedback.pushInfo(
                f'Max-orden flow-retning: {math.degrees(flow_angle):.1f}° '
                f'fra grænseindgang ({max_entry.x():.2f}, {max_entry.y():.2f}).'
            )

            # Iterér ned gennem alle lavere ordener. For hver orden findes
            # bedste linje (skæringen der bedst forlænger max-linjens åforløb)
            # og tilføjes til selected. Efter hver tilføjelse tælles totale
            # skæringer mellem ALLE udvalgte linjer (forlænget) og grænsen.
            # Stop så snart vi har ≥2 totale skæringer.
            lavere_ordener = [o for o in orders if o < max_order]  # sorteret faldende
            succeeded = False

            for order in lavere_ordener:
                kandidat_count = 0
                best_feat = None
                best_diff = None
                best_pt = None
                for f in features:
                    if f[order_felt] != order:
                        continue
                    kandidat_count += 1
                    g = f.geometry()
                    eg = _extend_geom(g, extend_dist)
                    sec_pts = _intersection_points(eg, boundary_geom)
                    for p in sec_pts:
                        vx = p.x() - max_entry.x()
                        vy = p.y() - max_entry.y()
                        if not (vx or vy):
                            continue
                        a = math.atan2(vy, vx)
                        diff = _angular_diff(a, flow_angle)
                        if best_diff is None or diff < best_diff:
                            best_diff = diff
                            best_feat = f
                            best_pt = p

                if best_feat is None:
                    feedback.pushInfo(
                        f'Orden {order}: {kandidat_count} kandidat(er), '
                        f'ingen krydser projektgrænsen — prøver lavere orden.'
                    )
                    continue

                selected.append(best_feat)
                afv = math.degrees(best_diff)
                feedback.pushInfo(
                    f'Orden {order}: tilføjet linje id={best_feat.id()}, '
                    f'skæring=({best_pt.x():.2f}, {best_pt.y():.2f}), '
                    f'vinkelafvigelse={afv:.1f}°.'
                )

                # Tæl totale skæringer fra alle udvalgte linjer (forlænget)
                total_pts = []
                for f in selected:
                    eg = _extend_geom(f.geometry(), extend_dist)
                    total_pts.extend(_intersection_points(eg, boundary_geom))
                feedback.pushInfo(
                    f'  Total skæringer efter orden {order}: {len(total_pts)}.'
                )

                if len(total_pts) >= 2:
                    succeeded = True
                    break

            if not succeeded:
                raise QgsProcessingException(
                    f'Kunne ikke finde 2 skæringspunkter selv efter at have tjekket '
                    f'alle ordener fra {max_order} ned til {min(orders)}. '
                    f'Modellen kan ikke beregne både opstrøms og nedstrøms opland.'
                )

        # Sluttest: verificér at de udvalgte linjer (forlænget) giver ≥2 skæringer
        # med projektgrænsen — ellers ryger fejlen først nedstrøms i modellen.
        slut_inter = []
        for f in selected:
            eg = _extend_geom(f.geometry(), extend_dist)
            slut_inter.extend(_intersection_points(eg, boundary_geom))
        if len(slut_inter) < 2:
            raise QgsProcessingException(
                f'Efter udvælgelse er der stadig kun {len(slut_inter)} skæringspunkt(er) '
                f'mellem de valgte linjer og projektgrænsen. Modellen kræver mindst 2. '
                f'Tjek at den valgte næststørste-linje faktisk når ud til projektgrænsen.'
            )

        fields = input_lag.fields()
        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            input_lag.wkbType(), input_lag.sourceCrs()
        )

        for f in selected:
            new_feat = QgsFeature(fields)
            new_feat.setGeometry(QgsGeometry(f.geometry()))
            for i, fld in enumerate(fields):
                new_feat.setAttribute(i, f[fld.name()])
            sink.addFeature(new_feat, QgsFeatureSink.FastInsert)

        return {self.OUTPUT: dest_id}
