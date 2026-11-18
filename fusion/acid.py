"""Build one stage of an acid test in Fusion, with Fusion's own features, and report what it measures.

This file runs inside Fusion: tools/run_fusion.py sends it through Fusion's local MCP server (the
"script" execution), with TEST, STAGE and PROBES appended. It uses only the public Fusion API.

How a test is built, as a Fusion user would:
- Primitives (box, cylinder, cone, sphere, torus) are exact bodies added in a base feature, which is
  what Fusion's own primitive commands give. Every other step is a timeline feature: Combine
  (join, cut, intersect), Fillet, Chamfer, Shell, Draft, Thread, Split Body, Mirror, Move, Scale,
  Extrude, Loft, joints, a motion link and Interference.
- Picks are by a point on the face or edge, as PLAN.md asks, using the measure manager.
- Fusion's API works in cm; every helper takes mm.
Each test is its own new, unsaved design. Stage 1 creates it; later stages continue in it.
"""
import json
import math
import time

import adsk.core
import adsk.fusion

APP = adsk.core.Application.get()
MM = 0.1
TOL = 1e-3  # mm, for picks
BIG = 5000.0  # mm, half-size of the boxes that cut a body by a plane


# ------------------------------------------------------------------ basics
def design():
    return adsk.fusion.Design.cast(APP.activeProduct)


COMP = []  # the component being built in; the root unless a stage says otherwise
OCC = []  # its occurrence, while it is built in place (identity transform) for an assembly


def comp():
    return COMP[-1] if COMP else design().rootComponent


def P(x, y, z):
    return adsk.core.Point3D.create(x * MM, y * MM, z * MM)


def VI(mm):
    return adsk.core.ValueInput.createByReal(mm * MM)


def VA(deg):
    return adsk.core.ValueInput.createByReal(math.radians(deg))


def coll(items):
    c = adsk.core.ObjectCollection.create()
    for i in items:
        c.add(i)
    return c


def tbm():
    return adsk.fusion.TemporaryBRepManager.get()


def bodies(c=None):
    c = c or comp()
    return [c.bRepBodies.item(i) for i in range(c.bRepBodies.count) if c.bRepBodies.item(i).isVisible]


def body():
    bs = bodies()
    if len(bs) != 1:
        raise RuntimeError("expected one body, have %d" % len(bs))
    return bs[0]


def volume(b):
    return b.getPhysicalProperties(adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy).volume * 1000.0


def check_health(feature):
    if feature is None:
        raise RuntimeError("the feature was not created")
    t = getattr(feature, "healthState", None)
    if t == adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState:
        raise RuntimeError("feature %s failed: %s" % (feature.name, feature.errorOrWarningMessage))
    return feature


# ------------------------------------------------------------------ primitives (exact bodies in a base feature)
def t_box(x0, y0, z0, x1, y1, z1):
    c = P((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    obb = adsk.core.OrientedBoundingBox3D.create(c, adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0),
                                                 abs(x1 - x0) * MM, abs(y1 - y0) * MM, abs(z1 - z0) * MM)
    return tbm().createBox(obb)


def t_cyl(r, h, base, axis=(0, 0, 1)):
    top = [base[i] + h * axis[i] for i in range(3)]
    return tbm().createCylinderOrCone(P(*base), r * MM, P(*top), r * MM)


def t_cone(r0, r1, h, base=(0, 0, 0)):
    return tbm().createCylinderOrCone(P(*base), r0 * MM, P(base[0], base[1], base[2] + h), r1 * MM)


def t_sphere(r, c=(0, 0, 0)):
    return tbm().createSphere(P(*c), r * MM)


def t_torus(R, r, c=(0, 0, 0)):
    return tbm().createTorus(P(*c), adsk.core.Vector3D.create(0, 0, 1), R * MM, r * MM)


def t_rotated(t, deg, axis=(0, 0, 1), origin=(0, 0, 0)):
    m = adsk.core.Matrix3D.create()
    m.setToRotation(math.radians(deg), adsk.core.Vector3D.create(*axis), P(*origin))
    tbm().transform(t, m)
    return t


def half_below(z):
    return t_box(-BIG, -BIG, z - BIG, BIG, BIG, z)


def half_above(z):
    return t_box(-BIG, -BIG, z, BIG, BIG, z + BIG)


def add(temps):
    """Add temporary bodies to the design in one base feature; returns the new bodies."""
    c = comp()
    bf = c.features.baseFeatures.add()
    bf.startEdit()
    for t in temps:
        c.bRepBodies.add(t, bf)
    bf.finishEdit()
    return [bf.bodies.item(i) for i in range(bf.bodies.count)]


# ------------------------------------------------------------------ features
_OPS = {"join": adsk.fusion.FeatureOperations.JoinFeatureOperation,
        "cut": adsk.fusion.FeatureOperations.CutFeatureOperation,
        "intersect": adsk.fusion.FeatureOperations.IntersectFeatureOperation}


def combine(target, tools, op):
    cf = comp().features.combineFeatures
    ci = cf.createInput(target, coll(tools))
    ci.operation = _OPS[op]
    ci.isKeepToolBodies = False
    return check_health(cf.add(ci))


def combine_temps(target, temps, op):
    return combine(target, add(temps), op)


def fillet(edges, r):
    ff = comp().features.filletFeatures
    fi = ff.createInput()
    fi.edgeSetInputs.addConstantRadiusEdgeSet(coll(edges), VI(r), False)
    return check_health(ff.add(fi))


def chamfer(edges, d):
    cf = comp().features.chamferFeatures
    ci = cf.createInput2()
    ci.chamferEdgeSets.addEqualDistanceChamferEdgeSet(coll(edges), VI(d), False)
    return check_health(cf.add(ci))


def shell(entities, t):
    """Shell inward to wall t; entities are the faces to remove, or [body] for a closed void."""
    sf = comp().features.shellFeatures
    si = sf.createInput(coll(entities), False)
    si.insideThickness = VI(t)
    return check_health(sf.add(si))


def draft(b, side_pt, neutral_pt, angle, outward):
    """Draft the face at side_pt about the plane face at neutral_pt. Fusion's pull direction depends on
    the neutral face; both directions are tried and the one that adds (outward) or removes volume is
    kept, as a user would flip it."""
    v0 = volume(b)
    last = ""
    for flip in (False, True):
        b = bodies()[0] if len(bodies()) == 1 else b
        side, neutral = face_near(b, side_pt), face_near(b, neutral_pt)
        df = comp().features.draftFeatures
        di = df.createInput([side], neutral, False)
        di.setSingleAngle(False, VA(angle))
        di.isDirectionFlipped = flip
        try:
            f = check_health(df.add(di))
        except Exception as e:  # noqa: BLE001  (a refused direction: try the other one)
            last = str(e)
            continue
        nb = body_near(side_pt) or bodies()[0]
        if (volume(nb) > v0) == outward:
            return f
        last = "the draft went the wrong way"
        f.timelineObject.rollTo(True)
        f.deleteMe()
        design().timeline.moveToEnd()
    raise RuntimeError("the draft did not build in the asked direction: " + last)


def keep_half(b, z, above):
    """Split the body by the plane Z = z and remove the other side (Split Body + Remove)."""
    f = split(b, plane_z(z))
    pieces = [f.bodies.item(i) for i in range(f.bodies.count)]
    for piece in pieces:
        bb = piece.boundingBox
        if ((bb.minPoint.z + bb.maxPoint.z) / 2 / MM < z) == above:
            check_health(comp().features.removeFeatures.add(piece))


def half_torus(R, r, z):
    """The half of a torus about the Z axis above Z = z: a half disc revolved (Sketch + Revolve)."""
    c = comp()
    sk = c.sketches.add(c.xZConstructionPlane)
    m = sk.transform.copy()
    m.invert()

    def sp(x, zz):
        p = P(x, 0, zz)
        p.transformBy(m)
        return p
    sk.sketchCurves.sketchArcs.addByThreePoints(sp(R - r, z), sp(R, z + r), sp(R + r, z))
    sk.sketchCurves.sketchLines.addByTwoPoints(sp(R - r, z), sp(R + r, z))
    if sk.profiles.count != 1:
        raise RuntimeError("the half disc gave %d profiles" % sk.profiles.count)
    rf = c.features.revolveFeatures
    ri = rf.createInput(sk.profiles.item(0), c.zConstructionAxis, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ri.setAngleExtent(False, VA(360))
    return check_health(rf.add(ri))


def plane_offset(base, d):
    cps = comp().constructionPlanes
    ci = cps.createInput()
    ci.setByOffset(base, VI(d))
    return cps.add(ci)


def plane_z(z):
    return plane_offset(comp().xYConstructionPlane, z)


def plane_x(x):
    return plane_offset(comp().yZConstructionPlane, x)


def split(b, plane):
    sf = comp().features.splitBodyFeatures
    return check_health(sf.add(sf.createInput(b, plane, True)))


def mirror(bs, plane):
    mf = comp().features.mirrorFeatures
    return check_health(mf.add(mf.createInput(coll(bs), plane)))


def move(bs, dx, dy, dz):
    mf = comp().features.moveFeatures
    mi = mf.createInput2(coll(bs))
    mi.defineAsTranslateXYZ(VI(dx), VI(dy), VI(dz), True)
    return check_health(mf.add(mi))


# ------------------------------------------------------------------ picking by points
def dist(a, pt):
    if OCC and a.assemblyContext is None:  # measuring needs the occurrence's proxy in an assembly
        a = a.createForAssemblyContext(OCC[-1])
    return APP.measureManager.measureMinimumDistance(a, P(*pt)).value / MM


def face_near(b, pt, tol=TOL):
    best = min((b.faces.item(i) for i in range(b.faces.count)), key=lambda f: dist(f, pt))
    if dist(best, pt) > tol:
        raise RuntimeError("no face at %r" % (pt,))
    return best


def edges_near(b, pt, tol=TOL):
    found = [b.edges.item(i) for i in range(b.edges.count) if dist(b.edges.item(i), pt) < tol]
    if not found:
        raise RuntimeError("no edge at %r" % (pt,))
    return found


def loop_edges(face, pt, tol=TOL):
    for i in range(face.loops.count):
        lp = face.loops.item(i)
        es = [lp.edges.item(j) for j in range(lp.edges.count)]
        if any(dist(e, pt) < tol for e in es):
            return es
    raise RuntimeError("no loop of the face at %r" % (pt,))


def outer_loop(face):
    for i in range(face.loops.count):
        lp = face.loops.item(i)
        if lp.isOuter:
            return [lp.edges.item(j) for j in range(lp.edges.count)]
    raise RuntimeError("the face has no outer loop")


def body_near(pt, tol=TOL):
    for b in bodies():
        if dist(b, pt) < tol:
            return b
    return None


# ------------------------------------------------------------------ the tests: STAGES[test][n]() builds stage n
def acid1():
    bx = math.sqrt(30 ** 2 - 0.95001 ** 2)

    def s1():
        half_torus(30, 8, 0)

    def s2():
        combine_temps(body(), [t_cyl(0.95, 1.9, (bx, 0.95001, -1.9))], "join")

    def s3():
        combine_temps(body(), [t_cyl(0.7, 4, (0, 38, -1))], "cut")

    def s4():
        f = face_near(body(), (-30, 0, 0))
        fillet(loop_edges(f, (-38, 0, 0)) + loop_edges(f, (-22, 0, 0)), 0.8)

    def s5():
        shell([face_near(body(), (bx, 0.95001, -1.9))], 0.6)

    def s6():
        a = [math.radians(15 + 30 * k) for k in range(12)]
        combine_temps(body(), [t_cyl(0.5, 40, (30 * math.cos(t), 30 * math.sin(t), -20)) for t in a], "cut")

    def s7():
        split(body(), plane_z(4))

    def s8():
        move(bodies(), 10000, 0, 0)

    return [s1, s2, s3, s4, s5, s6, s7, s8]


def spline_sketch_profile(z=0.0):
    """acid-2's outline, built exactly from its Bezier segments (BEZIER is appended by the runner)."""
    pts, knots = [], [0.0]
    for i, seg in enumerate(BEZIER):  # noqa: F821
        pts += [P(x, y, z) for x, y in seg[:3]]
        knots.append(knots[-1] + SPLINE_H[i])  # noqa: F821
    pts.append(P(BEZIER[0][0][0], BEZIER[0][0][1], z))  # noqa: F821
    kv = [knots[0]] * 4
    for k in knots[1:-1]:
        kv += [k] * 3
    kv += [knots[-1]] * 4
    curve = adsk.core.NurbsCurve3D.createNonRational(pts, 3, [k * MM for k in kv], False)
    sk = comp().sketches.add(comp().xYConstructionPlane)
    sk.sketchCurves.sketchFixedSplines.addByNurbsCurve(curve)
    if sk.profiles.count != 1:
        raise RuntimeError("the outline gave %d profiles" % sk.profiles.count)
    return sk.profiles.item(0)


def acid2():
    pillars = [(x, y) for x in (-30, 30) for y in (-12, 12)]

    def s1():
        ef = comp().features.extrudeFeatures
        check_health(ef.addSimple(spline_sketch_profile(), VI(25), adsk.fusion.FeatureOperations.NewBodyFeatureOperation))

    def s2():
        fillet(loop_edges(face_near(body(), (0, 0, 25)), (60, 0, 25), tol=1e-2), 3)

    def s3():
        combine_temps(body(), [t_cyl(3, 10, (x, y, 25)) for x, y in pillars], "join")

    def s4():
        draft(body(), (60, 0, 10), (0, 0, 0), 3, outward=True)

    def s5():
        shell([face_near(body(), (0, 0, 0))], 1.5)

    def s6():
        combine_temps(body(), [t_cyl(2, 50, (x, y, -5)) for x, y in pillars], "cut")

    def s7():
        mirror([body()], plane_x(80))

    def s8():
        bs = bodies()
        bar = add([t_box(55, -5, 0, 105, 5, 5)])
        combine(bs[0], bs[1:] + bar, "join")

    return [s1, s2, s3, s4, s5, s6, s7, s8]


def acid3():
    def s1():
        add([t_box(0, 0, 0, 80, 60, 30)])

    def s2():
        pads = [t_box(20 + 20 * i - 1.5, 20 + 20 * j - 1.5, 30, 20 + 20 * i + 1.5, 20 + 20 * j + 1.5, 32)
                for i in range(3) for j in range(2)]
        combine_temps(body(), pads, "join")

    def s3():
        combine_temps(body(), [t_cyl(4.917 / 2, 20, (40, 30, 10))], "cut")
        b = body()
        face = face_near(b, (40 + 4.917 / 2, 30, 20))
        tf = comp().features.threadFeatures
        info = tf.createThreadInfo(True, "ISO Metric profile", "M6x1", "6H")
        for loc in (adsk.fusion.ThreadLocations.HighEndThreadLocation, adsk.fusion.ThreadLocations.LowEndThreadLocation):
            ti = tf.createInput(face, info)
            ti.isModeled = True
            ti.isFullLength = False
            ti.threadLength = VI(15)
            ti.threadLocation = loc
            f = check_health(tf.add(ti))
            # the thread belongs at the top (Z 15..30): metal must be gone just outside the bore at Z 29
            gone = all(body().pointContainment(P(40 + 4.917 / 2 + 0.25 * k, 30, z)) ==
                       adsk.fusion.PointContainment.PointOutsidePointContainment
                       for z in (29.0, 29.25, 29.5, 29.75) for k in (1,)) or \
                any(body().pointContainment(P(40 + 4.917 / 2 + 0.25, 30, 29 + 0.1 * i)) ==
                    adsk.fusion.PointContainment.PointOutsidePointContainment for i in range(10))
            low_gone = any(body().pointContainment(P(40 + 4.917 / 2 + 0.25, 30, 11 + 0.1 * i)) ==
                           adsk.fusion.PointContainment.PointOutsidePointContainment for i in range(10))
            if gone and not low_gone:
                return
            f.deleteMe()
        raise RuntimeError("the thread could not be placed at the top of the bore")

    def s4():
        a = [math.radians(45 * k) for k in range(8)]
        combine_temps(body(), [t_cyl(1.25, 12, (40 + 12 * math.cos(t), 30 + 12 * math.sin(t), 20)) for t in a], "cut")

    def s5():
        combine_temps(body(), [t_cyl(1.5, 6, (1.50001, 0, 26))], "cut")

    def s6():
        shell([body()], 1.2)

    def s7():
        b = body()
        fillet(outer_loop(face_near(b, (40, 0.5, 30))), 0.5)

    return [s1, s2, s3, s4, s5, s6, s7]


def cube_and_cyl():
    bs = bodies()
    cube = body_near((0, 0, 20)) or min(bs, key=lambda b: b.boundingBox.minPoint.x)
    other = [b for b in bs if b != cube]
    return cube, other


def acid4():
    def s1():
        add([t_box(-20, -20, -20, 20, 20, 20)])

    def s2():
        combine_temps(body(), [t_cyl(9, 42, (-21, 0, 0), (1, 0, 0)), t_cyl(9, 42, (0, -21, 0), (0, 1, 0)),
                               t_cyl(9, 42, (0, 0, -21), (0, 0, 1))], "cut")

    def s3():
        combine_temps(body(), [t_cyl(4, 42, (-21, y, 0), (1, 0, 0)) for y in (-10, 10)]
                      + [t_cyl(4, 42, (x, -21, 1e-5), (0, 1, 0)) for x in (-10, 10)], "cut")

    def s4():
        a, b = add([t_cyl(5, 60, (26, 0, -30)), t_cyl(5, 60, (35.99999, 0, -30))])
        combine(a, [b], "join")

    def s5():
        _, other = cube_and_cyl()
        combine_temps(other[0], [t_sphere(5, (26, 0, 34.99999))], "join")

    def s6():
        cube, _ = cube_and_cyl()
        mids = [(x, y, 0) for x in (-20, 20) for y in (-20, 20)] + [(x, 0, z) for x in (-20, 20) for z in (-20, 20)] \
            + [(0, y, z) for y in (-20, 20) for z in (-20, 20)]
        fillet([edges_near(cube, p)[0] for p in mids], 2)

    return [s1, s2, s3, s4, s5, s6]


def acid5():
    def s1():
        add([t_box(1000, 1000, 1000, 1030, 1020, 1010)])

    def s2():
        b = body()
        fillet([edges_near(b, (x, y, 1005))[0] for x in (1000, 1030) for y in (1000, 1020)], 1.3)

    def s3():
        b = body()
        fillet(loop_edges(face_near(b, (1015, 1010, 1010)), (1015, 1000, 1010)), 1.29)

    def s4():
        chamfer(edges_near(body(), (1015, 1000, 1000)), 0.00002)

    def s5():
        fillet(edges_near(body(), (1000, 1010, 1000)), 0.000025)

    def s6():
        shell([face_near(body(), (1015, 1010, 1000))], 0.4)

    return [s1, s2, s3, s4, s5, s6]


def acid6():
    z0 = 499.99975

    def sphere_body():
        return body_near((0, 0, 0)) or max(bodies(), key=volume)

    def s1():
        keep_half(add([t_sphere(500)])[0], z0, above=False)

    def s2():
        combine_temps(body(), [t_cyl(0.06, 0.12, (0, 0, z0))], "join")

    def s3():
        half_torus(500, 0.5, 600)

    def s4():
        shell([body_near((500, 0, 600.25), tol=0.3)], 0.06)

    def s5():
        needle = add([t_cyl(0.5, 1000, (0, 0, -1600))])[0]
        draft(needle, (0.5, 0, -1100), (0, 0, -1600), 0.02, outward=False)

    def s6():
        needle = body_near((0, 0, -1100))
        rod = add([t_cyl(0.4, 200, (0, 0, -600))])
        combine(sphere_body(), rod + [needle], "join")

    return [s1, s2, s3, s4, s5, s6]


def acid7():
    def s1():
        add([t_cone(100, 40, 200)])

    def s2():
        a = [math.radians(60 * k) for k in range(6)]
        combine_temps(body(), [t_cyl(5, 51, (32 * math.cos(t), 32 * math.sin(t), 150)) for t in a], "cut")

    def s3():
        draft(body(), (70, 0, 100), (0, 0, 0), DRAFT7, outward=False)  # noqa: F821  (the runner sets it)

    def s4():
        fillet(outer_loop(face_near(body(), (0, 0, 200))), 0.5)

    def s5():
        combine_temps(body(), [t_cyl(60, 81, (0, 0, -1))], "cut")

    def s6():
        shell([face_near(body(), (80, 0, 0))], 2)

    return [s1, s2, s3, s4, s5, s6]


def acid8():
    def ring(z):
        return [t_rotated(t_cyl(3, 20, (50, 0, z), (-1, 0, 0)), 15 * k) for k in range(24)]

    def s1():
        add([t_cyl(50, 120, (0, 0, 0))])

    def s2():
        combine_temps(body(), ring(20), "cut")

    def s3():
        combine_temps(body(), sum((ring(z) for z in (40, 60, 80, 100)), []), "cut")

    def s4():
        a = [math.radians(15 * k) for k in range(7)]
        combine_temps(body(), [t_cyl(2, 3, (30 * math.cos(t), 30 * math.sin(t), 120)) for t in a], "join")

    def s5():
        shell([body()], 2)

    def s6():
        split(body(), plane_z(61))

    def s7():
        upper = max(bodies(), key=lambda b: b.boundingBox.maxPoint.z)
        mirror([upper], plane_z(140))

    return [s1, s2, s3, s4, s5, s6, s7]


def acid9():
    def square(z, turn):
        r = 20 * math.sqrt(2)
        return [(r * math.cos(math.radians(turn + 45 + 90 * i)), r * math.sin(math.radians(turn + 45 + 90 * i)), z)
                for i in range(4)]

    def s1():
        c = comp()
        lo, hi = square(0, 0), square(100, 45)
        profiles = []
        for z, pts in ((0, lo), (100, hi)):
            pl = c.xYConstructionPlane if z == 0 else plane_z(z)
            sk = c.sketches.add(pl)
            lines = sk.sketchCurves.sketchLines
            m = sk.transform.copy()
            m.invert()
            ps = []
            for x, y, zz in pts:
                p = P(x, y, zz)
                p.transformBy(m)
                ps.append(p)
            for i in range(4):
                lines.addByTwoPoints(ps[i], ps[(i + 1) % 4])
            profiles.append(sk.profiles.item(0))
        # straight rails from each bottom corner to the corner 45 degrees further around at the top
        rk = c.sketches.add(c.xZConstructionPlane)
        m = rk.transform.copy()
        m.invert()
        rails = []
        for (x0, y0, z0), (x1, y1, z1) in zip(lo, hi):
            a, b = P(x0, y0, z0), P(x1, y1, z1)
            a.transformBy(m)
            b.transformBy(m)
            rails.append(rk.sketchCurves.sketchLines.addByTwoPoints(a, b))
        lf = c.features.loftFeatures
        li = lf.createInput(adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        for p in profiles:
            li.loftSections.add(p)
        for r in rails:
            li.centerLineOrRails.addRail(r)
        li.isSolid = True
        check_health(lf.add(li))

    def s2():
        combine_temps(body(), [t_cyl(36.94 / 2, 102, (0, 0, -1))], "cut")

    def s3():
        combine_temps(body(), [t_sphere(55, (0, 0, 50))], "intersect")

    def s4():
        b = body()
        edges = []
        for i in range(b.edges.count):
            e = b.edges.item(i)
            kinds = {e.faces.item(j).geometry.surfaceType for j in range(e.faces.count)}
            S = adsk.core.SurfaceTypes
            if S.SphereSurfaceType in kinds and kinds - {S.SphereSurfaceType, S.PlaneSurfaceType, S.CylinderSurfaceType}:
                edges.append(e)
        if not edges:
            raise RuntimeError("no sphere-to-twisted-face edges found")
        fillet(edges, 1)

    def s5():
        shell([face_near(body(), (0, 22, 100))], 1)

    def s6():
        c = comp()
        cps = c.constructionPlanes
        ci = cps.createInput()
        ci.setByAngle(c.zConstructionAxis, VA(45), c.xZConstructionPlane)
        pl = cps.add(ci)
        n = pl.geometry.normal
        if abs(abs(n.x) - abs(n.y)) > 1e-9 or n.x * n.y > 0:  # it must be normal to (1, -1, 0)
            pl.deleteMe()
            ci = cps.createInput()
            ci.setByAngle(c.zConstructionAxis, VA(-45), c.xZConstructionPlane)
            pl = cps.add(ci)
        split(body(), pl)

    return [s1, s2, s3, s4, s5, s6]


def acid10():
    def s1():
        add([t_box(0, 0, 0, 50, 50, 50)])

    def s2():
        combine_temps(body(), [t_cyl(10, 50, (0, 25, 59.99999), (1, 0, 0))], "join")

    def s3():
        combine_temps(body(), [t_sphere(15, (64.99999, 25, 25))], "join")

    def s4():
        combine_temps(body(), [t_box(20, -1, -1, 30, 0.00001, 51)], "cut")

    def s5():
        b = body()
        fillet([edges_near(b, (x, y, 25))[0] for x in (0, 50) for y in (0, 50)], 5)

    def s6():
        move(bodies(), 1000000, 1000000, 0)

    return [s1, s2, s3, s4, s5, s6]


def acid11():
    """The orrery, with Fusion's own assembly tools: components, revolute joints, a motion link at
    1 : 3 for the gear, the ring's joint driven step by step, and Interference at every step."""
    root = design().rootComponent

    def occ(name):
        for i in range(root.occurrences.count):
            o = root.occurrences.item(i)
            if o.component.name == name:
                return o
        raise RuntimeError("no component %s" % name)

    def new_part(name):
        o = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        o.component.name = name
        return o

    def build_in(o, stages):
        COMP.append(o.component)
        OCC.append(o)
        try:
            for s in stages:
                s()
        finally:
            COMP.pop()
            OCC.pop()

    def circle(o, centre, radius):
        """The circular edge of the part, in its own coordinates, as an assembly-context proxy."""
        b = o.component.bRepBodies.item(0)
        for i in range(b.edges.count):
            e = b.edges.item(i)
            g = e.geometry
            if isinstance(g, adsk.core.Circle3D) and abs(g.radius / MM - radius) < 1e-6 and \
                    g.center.distanceTo(P(*centre)) / MM < 1e-6:
                return e.createForAssemblyContext(o)
        raise RuntimeError("no circle of radius %g at %r in %s" % (radius, centre, o.component.name))

    def zangle(o):
        m = o.transform2
        x = adsk.core.Vector3D.create(1, 0, 0)
        x.transformBy(m)
        return math.degrees(math.atan2(x.y, x.x))

    def placed_as(o, want):
        m = o.transform2
        t = m.translation
        z = adsk.core.Vector3D.create(0, 0, 1)
        z.transformBy(m)
        return (abs(t.x / MM - want[0]) < 1e-6 and abs(t.y / MM - want[1]) < 1e-6 and abs(t.z / MM - want[2]) < 1e-6
                and z.z > 1 - 1e-12)

    def revolute(moving, e_moving, fixed, e_fixed, name, want):
        """A revolute joint between the two circles, 18 up from the pin's top; flipped as a user
        would until the part sits where TESTS.md places it, with its own X along the assembly's."""
        def make(flip, off, ang):
            g1 = adsk.fusion.JointGeometry.createByCurve(e_moving, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            g2 = adsk.fusion.JointGeometry.createByCurve(e_fixed, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            ji = root.joints.createInput(g1, g2)
            ji.offset = VI(off)
            ji.angle = adsk.core.ValueInput.createByReal(math.radians(ang))
            ji.isFlipped = flip
            ji.setAsRevoluteJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
            return check_health(root.joints.add(ji))

        for flip in (False, True):
            for off in (18, -18):
                j = make(flip, off, 0)
                a = zangle(moving)
                if abs(a) > 1e-9:
                    j.deleteMe()
                    j = make(flip, off, -a)
                if placed_as(moving, want) and abs(zangle(moving)) < 1e-9:
                    j.name = name
                    return j
                j.deleteMe()
        raise RuntimeError("the joint %s could not place the part at %r" % (name, want))

    def s1():
        base = new_part("Base")
        build_in(base, [lambda: combine_temps(add([t_box(-80, -50, 0, 80, 50, 10)])[0],
                                              [t_cyl(3, 2, (-40, 0, 10)), t_cyl(3, 2, (13.5, 0, 10))], "join")])
        base.isGrounded = True

    def s2():
        ring = new_part("Ring")
        build_in(ring, acid1()[:3])
        revolute(ring, circle(ring, (0, 0, 0), 22), occ("Base"), circle(occ("Base"), (-40, 0, 12), 3), "J1", (-40, 0, 30))

    def s3():
        cam = new_part("Cam")

        def scale_and_bore():
            c = comp()
            sf = c.features.scaleFeatures
            si = sf.createInput(coll([body()]), c.originConstructionPoint, adsk.core.ValueInput.createByReal(0.25))
            check_health(sf.add(si))
            combine_temps(body(), [t_cyl(1, 50, (0, 0, -10))], "cut")
        build_in(cam, acid2()[:3] + [scale_and_bore])
        revolute(cam, circle(cam, (0, 0, 0), 1), occ("Base"), circle(occ("Base"), (13.5, 0, 12), 3), "J2", (13.5, 0, 30))

    def s4():
        ml = root.motionLinks
        mi = ml.createInput(root.joints.itemByName("J1"), root.joints.itemByName("J2"))
        mi.valueOne = adsk.core.ValueInput.createByReal(math.radians(360))
        mi.valueTwo = adsk.core.ValueInput.createByReal(math.radians(1080))
        mi.isReversed = True
        check_health(ml.add(mi))

    def drive(deg):
        j1 = root.joints.itemByName("J1")
        j1.jointMotion.rotationValue = math.radians(deg)
        return zangle(occ("Ring")), zangle(occ("Cam"))

    def s5():
        exp = MOTION  # noqa: F821  (the runner passes values.TESTS["acid-11"][5]["motion"])
        bad = []
        for want in exp["steps"]:
            r, c = drive(want)
            dr = abs(((r - want + 180) % 360) - 180)
            dc = abs(((c - exp["ratio"] * r + 180) % 360) - 180)
            if dr > exp["tol_deg"] or dc > exp["tol_deg"]:
                bad.append("step %g: ring %.9f (want %g), cam %.9f (want %.9f)" % (want, r, want, c, (exp["ratio"] * r) % 360))
                break
        return bad

    def s6():
        exp = MOTION  # noqa: F821
        bad = []
        occs = [occ("Base"), occ("Ring"), occ("Cam")]
        for want in exp["steps"]:
            drive(want)
            ii = design().createInterferenceInput(coll(occs))
            ii.areCoincidentFacesIncluded = False
            res = design().analyzeInterference(ii)
            for k in range(res.count):
                r = res.item(k)
                bad.append("step %g: %s and %s overlap by %.3g mm3" % (
                    want, r.entityOne.name, r.entityTwo.name, r.interferenceBody.volume * 1000.0))
            if bad:
                break
        drive(0)
        return bad

    return [s1, s2, s3, s4, s5, s6]


STAGES = {"acid-1": acid1, "acid-2": acid2, "acid-3": acid3, "acid-4": acid4, "acid-5": acid5, "acid-6": acid6,
          "acid-7": acid7, "acid-8": acid8, "acid-9": acid9, "acid-10": acid10, "acid-11": acid11}


# ------------------------------------------------------------------ measuring
def all_bodies():
    """Every visible solid in the design, in assembly coordinates (occurrence proxies for parts)."""
    root = design().rootComponent
    out = bodies(root)
    for i in range(root.allOccurrences.count):
        o = root.allOccurrences.item(i)
        if o.isLightBulbOn:
            out += [o.bRepBodies.item(j) for j in range(o.bRepBodies.count) if o.bRepBodies.item(j).isVisible]
    return out


def measure(probes):
    bs = all_bodies()
    PC = adsk.fusion.PointContainment

    def cube_inside(b, pt, h=0.0001):
        """As PLAN.md's SolidCraft probe: a 0.0002 cube at the point is inside when at least half of
        it overlaps the body (exact for a point 0.000005 or more from a surface)."""
        cube = t_box(pt[0] - h, pt[1] - h, pt[2] - h, pt[0] + h, pt[1] + h, pt[2] + h)
        full = cube.volume
        tbm().booleanOperation(cube, tbm().copy(b), adsk.fusion.BooleanTypes.IntersectionBooleanType)
        return cube.volume >= 0.5 * full

    def where(pt):
        states = [b.pointContainment(P(*pt)) for b in bs]
        if any(s == PC.PointInsidePointContainment for s in states):
            return "in"
        on = [b for b, s in zip(bs, states) if s == PC.PointOnPointContainment]
        if on:  # within Fusion's tolerance of a face: decide by the cube
            return "in" if any(cube_inside(b, pt) for b in on) else "out"
        return "out"

    return {"bodies": len(bs), "solid_vols": [volume(b) for b in bs],
            "valid": all(b.isSolid for b in bs) and len(bs) > 0,
            "inside": [where(p) for p in probes.get("inside", [])],
            "outside": [where(p) for p in probes.get("outside", [])]}


def new_design():
    doc = APP.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    d = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    d.designType = adsk.fusion.DesignTypes.ParametricDesignType
    d.fusionUnitsManager.distanceDisplayUnits = adsk.fusion.DistanceUnits.MillimeterDistanceUnits
    return doc


def run(_context: str):
    out = {"stage": STAGE}  # noqa: F821
    if ACTION == "build":  # noqa: F821
        if STAGE == 1:  # noqa: F821
            new_design()
        t0 = time.time()
        problems = STAGES[TEST]()[STAGE - 1]()  # noqa: F821
        out["seconds"] = round(time.time() - t0, 3)
        out["problems"] = problems or []
        out["measured"] = measure(PROBES)  # noqa: F821
    elif ACTION == "export":  # noqa: F821
        em = design().exportManager
        em.execute(em.createFusionArchiveExportOptions(PATH))  # noqa: F821
        out["exported"] = PATH  # noqa: F821
    elif ACTION == "close":  # noqa: F821
        APP.activeDocument.close(False)
        out["closed"] = True
    print(json.dumps(out))
