"""
Ret takkede skel mellem naboparceller: find tynde slivers via opening, smelt
dem ind i nabopolygonen med laengste faelles kant, og fjern til sidst de
haartynde 'dangles' (soemme) der efterlades af overlay-operationerne.

Koeres fra QGIS' Python-konsol -> editor ("Show Editor") med "Run Script".
Ret kun afsnittet INDSTILLINGER.
"""

from qgis.core import QgsProject, QgsProcessingContext, QgsVectorLayer, QgsFeature, QgsGeometry, QgsWkbTypes
import processing

# === INDSTILLINGER ========================================================
LAYER_NAME  = "Markkort2024_simpl"     # lagnavn (store/smaa bogstaver er ligegyldigt her)
D           = 15.0        # halvdelen af bredeste sliver, i lagets enheder (meter)
MIN_AREA    = 1          # valgfrit: ignorer sliver-stykker mindre end dette (m2). 0 = alle.
GRID_SIZE   = 0.001      # snap-praecision der undgaar GEOS-robusthedsfejl (meter)
MAX_ITERS   = 1          # antal Eliminate-gennemloeb for at fange efterladte stykker
DESPIKE     = 0.1        # fjern dangles/soemme tyndere end ca. dette (meter). 0 = fra.
RESULT_NAME = "Grid_cleaned"
# ==========================================================================


def find_layer(name):
    proj = QgsProject.instance()
    ex = proj.mapLayersByName(name)
    if ex:
        return ex[0]
    for l in proj.mapLayers().values():
        if l.name().lower() == name.lower():
            return l
    raise ValueError(f"Fandt intet lag '{name}'. Lag: "
                     f"{[l.name() for l in proj.mapLayers().values()]}")


def run(alg, params):
    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    return processing.run(alg, params, context=context)["OUTPUT"]


def _python_difference(input_lyr, overlay_lyr, grid_size=0.0):
    """Beregn INPUT minus OVERLAY per feature i Python.
    Undgår QgsProcessingException når GEOS ikke kan serialisere enkeltresultater."""
    overlay_union = None
    for feat in overlay_lyr.getFeatures():
        g = feat.geometry()
        if g and not g.isNull() and not g.isEmpty():
            overlay_union = QgsGeometry(g) if overlay_union is None else overlay_union.combine(g)

    crs_str = input_lyr.crs().authid()
    out = QgsVectorLayer(f'Polygon?crs={crs_str}', 'diff', 'memory')
    out.dataProvider().addAttributes(input_lyr.fields().toList())
    out.updateFields()

    feats_out = []
    for feat in input_lyr.getFeatures():
        geom = feat.geometry()
        if not geom or geom.isNull() or geom.isEmpty():
            continue
        if overlay_union is not None:
            try:
                diff = geom.difference(overlay_union)
            except Exception:
                diff = QgsGeometry(geom)
        else:
            diff = QgsGeometry(geom)
        if grid_size > 0:
            diff = diff.snappedToGrid(grid_size, grid_size)
        if not diff or diff.isNull() or diff.isEmpty() or diff.area() < 1e-9:
            continue
        nf = QgsFeature(out.fields())
        nf.setGeometry(diff)
        nf.setAttributes(feat.attributes())
        feats_out.append(nf)

    out.dataProvider().addFeatures(feats_out)
    out.updateExtents()
    return out


def buf(lyr, dist):
    # rund join for ikke at skabe nye pigge under udglatningen
    return run("native:buffer", {"INPUT": lyr, "DISTANCE": dist, "JOIN_STYLE": 0,
        "SEGMENTS": 8, "OUTPUT": "memory:"})


def _add_gaps_to_work(work_lyr, orig_union):
    """Tilføj arealer fra orig_union der ikke dækkes af work_lyr som _sliver=1 features.
    Returnerer antal tilføjede hul-polygoner."""
    if orig_union is None:
        return 0
    work_union = None
    for feat in work_lyr.getFeatures():
        g = feat.geometry()
        if g and not g.isNull() and not g.isEmpty():
            work_union = QgsGeometry(g) if work_union is None else work_union.combine(g)
    if work_union is None:
        return 0
    try:
        gaps = orig_union.difference(work_union)
    except Exception:
        return 0
    if gaps.isNull() or gaps.isEmpty() or gaps.area() < 0.01:
        return 0
    sliver_idx = work_lyr.fields().indexFromName('_sliver')
    gap_feats = []
    for part in gaps.asGeometryCollection():
        if (QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.PolygonGeometry
                and part.area() > 0.01):
            f = QgsFeature(work_lyr.fields())
            f.setGeometry(part)
            attrs = [None] * work_lyr.fields().count()
            if sliver_idx >= 0:
                attrs[sliver_idx] = 1
            f.setAttributes(attrs)
            gap_feats.append(f)
    if gap_feats:
        work_lyr.dataProvider().addFeatures(gap_feats)
        work_lyr.updateExtents()
    return len(gap_feats)


def clean_layer(input_layer, d=15.0, min_area=1, grid_size=0.001, max_iters=1, despike=0.1):
    fixed = run("native:fixgeometries", {"INPUT": input_layer, "OUTPUT": "memory:"})
    # Single-parts inden difference – undgår at multi-part features producerer
    # tomme del-geometrier der ikke kan skrives til output-laget.
    fixed = run("native:multiparttosingleparts", {"INPUT": fixed, "OUTPUT": "memory:"})

    # Gem original dækning – bruges til at lukke huller til sidst
    orig_union = None
    for feat in fixed.getFeatures():
        g = feat.geometry()
        if g and not g.isNull() and not g.isEmpty():
            orig_union = QgsGeometry(g) if orig_union is None else orig_union.combine(g)

    # 1. 'core' = polygonerne uden de tynde dele (opening). Bruges som "kerne".
    shrunk = run("native:buffer", {"INPUT": fixed, "DISTANCE": -d, "JOIN_STYLE": 1,
        "MITER_LIMIT": 2, "SEGMENTS": 5, "OUTPUT": "memory:"})
    core = run("native:buffer", {"INPUT": shrunk, "DISTANCE": d, "JOIN_STYLE": 1,
        "MITER_LIMIT": 2, "SEGMENTS": 5, "OUTPUT": "memory:"})
    core = run("native:fixgeometries", {"INPUT": core, "OUTPUT": "memory:"})

    # 2. slivers = original MINUS kerne – Python-niveau for at undgå GEOS-serialiseringsfejl
    slivers = _python_difference(fixed, core, grid_size)
    slivers = run("native:multiparttosingleparts", {"INPUT": slivers, "OUTPUT": "memory:"})
    slivers = run("native:fixgeometries", {"INPUT": slivers, "OUTPUT": "memory:"})

    # 3. flag de to grupper og saml til ét lag
    core = run("native:fieldcalculator", {"INPUT": core, "FIELD_NAME": "_sliver",
        "FIELD_TYPE": 1, "FIELD_LENGTH": 1, "FIELD_PRECISION": 0, "FORMULA": "0",
        "OUTPUT": "memory:"})
    slivers = run("native:fieldcalculator", {"INPUT": slivers, "FIELD_NAME": "_sliver",
        "FIELD_TYPE": 1, "FIELD_LENGTH": 1, "FIELD_PRECISION": 0, "FORMULA": "1",
        "OUTPUT": "memory:"})

    work = run("native:mergevectorlayers", {"LAYERS": [core, slivers], "OUTPUT": "memory:"})
    work = run("native:fixgeometries", {"INPUT": work, "OUTPUT": "memory:"})

    # 4. Smelt slivers og huller ind i naboer – gentag til stabilt resultat.
    # Hvert gennemløb: tilføj eventuelle huller som _sliver=1, eliminer alle slivers.
    # Dette forhindrer at hul-lukning skaber nye slivers der aldrig renses op.
    sliver_expr = '"_sliver" = 1'
    if min_area > 0:
        sliver_expr = f'("_sliver" = 1) AND ($area >= {min_area})'

    for i in range(max_iters):
        n_gaps = _add_gaps_to_work(work, orig_union)
        work.selectByExpression(sliver_expr)
        n = work.selectedFeatureCount()
        print(f"  gennemloeb {i + 1}: {n} slivers/huller (heraf {n_gaps} huller)")
        if n == 0:
            break
        work = run("qgis:eliminateselectedpolygons", {"INPUT": work, "MODE": 2,
            "OUTPUT": "memory:"})
        work = run("native:fixgeometries", {"INPUT": work, "OUTPUT": "memory:"})

    # 5. fjern dangles/soemme: lukning (+,-) efterfulgt af aabning (-,+)
    if despike > 0:
        for dist in (despike, -despike, -despike, despike):
            work = buf(work, dist)
        work = run("native:fixgeometries", {"INPUT": work, "OUTPUT": "memory:"})
        print(f"  despike udfoert (eps = {despike} m)")

    # 6. laeg i kortet
    return work


def clean(layer_name, d, min_area, grid_size, max_iters, despike, result_name):
    layer = find_layer(layer_name)
    print(f"Lag '{layer.name()}': {layer.featureCount()} features, d = {d}")
    work = clean_layer(layer, d=d, min_area=min_area,
                       grid_size=grid_size, max_iters=max_iters,
                       despike=despike)
    res = run("native:fixgeometries", {"INPUT": work, "OUTPUT": "TEMPORARY_OUTPUT"})
    res.setName(result_name)
    QgsProject.instance().addMapLayer(res)
    print(f"  resultat '{result_name}': {res.featureCount()} features  ->  faerdig.")


if __name__ == '__main__':
    clean(LAYER_NAME, D, MIN_AREA, GRID_SIZE, MAX_ITERS, DESPIKE, RESULT_NAME)